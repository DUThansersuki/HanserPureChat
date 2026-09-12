from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from ... import db
from ...config import Settings
from ...models import DialoguePlan, StyleExample
from ...persona.data_pipeline import (
    STYLE_COLLECTION,
    label_style,
    style_row_to_model,
)
from ...persona.schemas import BehaviorDecision, ExpressionObservation, TurnSignals
from ...persona.style_ranking import style_score_components
from ...retrieval import Embedder, VectorStore


class StyleSearchResult(BaseModel):
    examples: list[StyleExample] = Field(default_factory=list)
    scores: dict[str, float] = Field(default_factory=dict)
    score_components: dict[str, dict[str, float]] = Field(default_factory=dict)
    excluded: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _ScoredExample:
    score: float
    example: StyleExample
    components: dict[str, float]


class StyleSearchTool:
    """Retrieve reviewed real examples plus explicit synthetic fallbacks, never evidence."""

    name = "style_search"
    description = "检索审核过的真实表达样例及显式合成兜底，仅用于风格。"
    _FACT_PAYLOAD = re.compile(
        r"(?:我|憨憨|我们).{0,12}(?:喜欢|去过|去|来过|有|没有|没|看过|吃|喝|"
        r"唱|录|配|买|住|工作|出差|过敏|嗓子|小时候|以前|上次|现在|今天|"
        r"最近|打算|准备|觉得|记得|知道)"
    )

    def __init__(
        self,
        *,
        settings: Settings,
        embedder: Embedder,
        vector_store: VectorStore,
        strict_v2: bool = False,
        pinned_generation: str | None = None,
    ):
        self.settings = settings
        self.embedder = embedder
        self.vector_store = vector_store
        self.strict_v2 = strict_v2
        self.pinned_generation = pinned_generation

    async def search(
        self,
        message: str,
        plan: DialoguePlan,
        *,
        turn_signals: TurnSignals | None = None,
        behavior_decision: BehaviorDecision | None = None,
        observations: ExpressionObservation | None = None,
        recent_example_ids: list[str] | None = None,
        repetition_penalty: float = 0.30,
    ) -> StyleSearchResult:
        if not self.settings.style.enabled or plan.response_mode == "factual":
            return StyleSearchResult()
        v2_active = self.strict_v2 or turn_signals is not None or behavior_decision is not None
        labels = label_style(message, "") if turn_signals is None else {}
        scene = str(labels.get("scene", "shared_signals"))
        acts = (
            [item.id for item in behavior_decision.persona_affordances]
            if behavior_decision is not None
            else [str(labels.get("speech_act", "react"))]
        )
        query_text = (
            f"scene={scene} mode={plan.response_mode} "
            f"affordances={' '.join(acts)}\nUser: {message}"
        )
        vector = (await self.embedder.embed_queries([query_text]))[0]
        active_generation: str | None = None
        expression_seed_ids: list[str] = []
        if self.settings.style.reviewed_only or v2_active:
            with db.connect(self.settings.db_path) as conn:
                if v2_active:
                    table_exists = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='active_index_generations'"
                    ).fetchone()
                    if table_exists is None:
                        return StyleSearchResult()
                    active = conn.execute(
                        "SELECT generation FROM active_index_generations "
                        "WHERE collection = ?",
                        (STYLE_COLLECTION,),
                    ).fetchone()
                    if active is None:
                        return StyleSearchResult()
                    active_generation = str(active["generation"])
                    if self.pinned_generation is not None and active_generation != self.pinned_generation:
                        return StyleSearchResult(
                            excluded={"__generation__": "active_generation_incompatible_with_persona_package"}
                        )
                eligible_ids = [
                    str(row["id"])
                    for row in conn.execute(
                        """
                        SELECT id FROM style_examples
                        WHERE review_status = 'approved'
                          AND ((source_tier = 'primary' AND source_type = 'real')
                               OR (source_tier = 'synthetic' AND source_type = 'synthetic'))
                          AND source_speaker = 'hanser'
                          AND source_user_turn IS NOT NULL
                          AND source_response_turn IS NOT NULL
                          AND embedding_ref IS NOT NULL
                        """
                    ).fetchall()
                ]
                requested_expression_tags = self._requested_expression_tags(
                    behavior_decision
                )
                if v2_active and active_generation and requested_expression_tags:
                    placeholders = ",".join("?" for _ in requested_expression_tags)
                    expression_seed_ids = [
                        str(row["id"])
                        for row in conn.execute(
                            f"""
                            SELECT DISTINCT style_examples.id
                            FROM style_examples,
                                 json_each(style_examples.metadata_json, '$.expression_tags') AS tag
                            WHERE style_examples.index_generation = ?
                              AND style_examples.review_status = 'approved'
                              AND style_examples.source_speaker = 'hanser'
                              AND style_examples.embedding_ref IS NOT NULL
                              AND tag.value IN ({placeholders})
                            ORDER BY style_examples.quality_score DESC,
                                     style_examples.authenticity_score DESC,
                                     style_examples.id
                            LIMIT 4
                            """,
                            (active_generation, *requested_expression_tags),
                        ).fetchall()
                    ]
            if not eligible_ids:
                return StyleSearchResult()
            hits = self.vector_store.search_filtered(
                STYLE_COLLECTION,
                self.embedder.model_name,
                vector,
                item_ids=eligible_ids,
                top_k=self.settings.style.candidate_pool_size * 3,
            )
        else:
            hits = self.vector_store.search(
                STYLE_COLLECTION,
                self.embedder.model_name,
                vector,
                top_k=self.settings.style.candidate_pool_size,
            )
        if not hits and not expression_seed_ids:
            return StyleSearchResult()
        ids = list(
            dict.fromkeys(
                [int(item.item_id) for item in hits]
                + [int(item_id) for item_id in expression_seed_ids]
            )
        )
        placeholders = ",".join("?" for _ in ids)
        with db.connect(self.settings.db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM style_examples WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
        by_id = {int(row["id"]): row for row in rows}
        dense_scores = {int(item.item_id): item.score for item in hits}
        dense_scores.update(
            (int(item_id), 0.0)
            for item_id in expression_seed_ids
            if int(item_id) not in dense_scores
        )
        scored: list[_ScoredExample] = []
        excluded: dict[str, str] = {}
        components_by_id: dict[str, dict[str, float]] = {}
        recent_ids = set(recent_example_ids or [])
        normalized_message = "".join(message.split()).casefold()
        for item_id in ids:
            row = by_id.get(item_id)
            if row is None:
                continue
            example = style_row_to_model(row)
            if "".join(example.user_context.split()).casefold() == normalized_message:
                excluded[example.id] = "exact_input_duplicate"
                continue
            if v2_active and not self._eligible_v2(
                example,
                behavior_decision,
                active_generation=active_generation,
            ):
                excluded[example.id] = "v2_fail_closed_metadata"
                continue
            if len(example.character_response) > 240:
                excluded[example.id] = "response_too_long"
                continue
            if not v2_active and (
                self._FACT_PAYLOAD.search(example.character_response)
                or (
                    example.source_type == "real"
                    and any(token in example.character_response for token in ("我", "我们", "憨憨"))
                )
            ):
                excluded[example.id] = "legacy_first_person_fact_guard"
                continue
            components = style_score_components(
                dense_score=dense_scores[item_id],
                quality_score=example.quality_score,
                authenticity_score=example.authenticity_score,
                response_mode_match=example.response_mode == plan.response_mode,
                behavior_tags=set(example.behavior_tags),
                decision=behavior_decision or BehaviorDecision(),
                expression_tags=set(example.expression_tags),
                observations=observations,
                repetition_penalty=repetition_penalty,
                scene_match=example.scene == scene,
                length_match=example.answer_length == plan.target_length,
                synthetic=example.source_type == "synthetic",
                recently_used=example.id in recent_ids,
            )
            score = sum(components.values())
            components_by_id[example.id] = {
                key: round(value, 6) for key, value in components.items()
            }
            scored.append(_ScoredExample(score=score, example=example, components=components))
        selected: list[_ScoredExample] = []
        non_verbatim = 0
        seen_groups: set[str] = set()
        remaining = list(scored)
        while remaining and len(selected) < self.settings.style.top_k:
            item = max(
                remaining,
                key=lambda candidate: candidate.score - (
                    0.12 if candidate.example.group_id in seen_groups else 0.0
                ),
            )
            remaining.remove(item)
            example = item.example
            is_non_verbatim = example.provenance_kind in {"adapted", "designed", "generated"}
            if v2_active and is_non_verbatim and non_verbatim >= 1:
                excluded[example.id] = "non_verbatim_limit"
                continue
            group_penalty = -0.12 if example.group_id and example.group_id in seen_groups else 0.0
            if group_penalty:
                item.components["same_group_penalty"] = group_penalty
                item = _ScoredExample(
                    score=item.score + group_penalty,
                    example=example,
                    components=item.components,
                )
                components_by_id[example.id] = {
                    key: round(value, 6) for key, value in item.components.items()
                }
            selected.append(item)
            non_verbatim += int(is_non_verbatim)
            if example.group_id:
                seen_groups.add(example.group_id)
        return StyleSearchResult(
            examples=[item.example for item in selected],
            scores={item.example.id: round(item.score, 6) for item in selected},
            score_components={item.example.id: components_by_id[item.example.id] for item in selected},
            excluded=excluded,
        )

    @staticmethod
    def _requested_expression_tags(
        decision: BehaviorDecision | None,
    ) -> list[str]:
        if decision is None:
            return []
        affordance_ids = {item.id for item in decision.persona_affordances}
        return [
            tag
            for affordance_id, tag in (
                ("light_profanity_release", "profanity"),
            )
            if affordance_id in affordance_ids
            and tag not in decision.expression_caps.hard_disallowed
        ]

    @staticmethod
    def _eligible_v2(
        example: StyleExample,
        decision: BehaviorDecision | None,
        *,
        active_generation: str | None,
    ) -> bool:
        if example.review_status != "approved" or example.schema_review_status != "approved":
            return False
        if example.index_generation != active_generation or active_generation is None:
            return False
        if "style_runtime" not in example.runtime_scope or example.hidden_eval:
            return False
        if example.provenance_kind not in {"verbatim", "adapted"}:
            return False
        if example.payload_class not in {"reaction_only", "turn_local_stance"}:
            return False
        if example.source_speaker != "hanser" or not example.group_id:
            return False
        if (
            example.provenance_kind == "verbatim"
            and example.speaker_status not in {"audio_verified", "transcript_verified"}
        ):
            return False
        if (
            example.provenance_kind != "verbatim"
            and example.speaker_status != "not_applicable"
        ):
            return False
        if decision is not None:
            blocked = set(decision.expression_caps.hard_disallowed)
            if blocked.intersection(example.expression_tags):
                return False
        return True
