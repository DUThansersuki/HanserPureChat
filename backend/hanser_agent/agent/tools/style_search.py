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
    ):
        self.settings = settings
        self.embedder = embedder
        self.vector_store = vector_store
        self.strict_v2 = strict_v2

    async def search(
        self,
        message: str,
        plan: DialoguePlan,
        *,
        turn_signals: TurnSignals | None = None,
        behavior_decision: BehaviorDecision | None = None,
        observations: ExpressionObservation | None = None,
        recent_example_ids: list[str] | None = None,
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
        if not hits:
            return StyleSearchResult()
        ids = [int(item.item_id) for item in hits]
        placeholders = ",".join("?" for _ in ids)
        with db.connect(self.settings.db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM style_examples WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
        by_id = {int(row["id"]): row for row in rows}
        dense_scores = {int(item.item_id): item.score for item in hits}
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
            components = {
                "dense": 0.55 * dense_scores[item_id],
                "quality": 0.2 * example.quality_score,
                "authenticity": 0.15 * example.authenticity_score,
                "scene": 0.12 if example.scene == scene else 0.0,
                "mode": 0.08 if example.response_mode == plan.response_mode else 0.0,
                "length": 0.05 if example.answer_length == plan.target_length else 0.0,
                "behavior": self._behavior_bonus(example, behavior_decision),
                "source_penalty": -0.12 if example.source_type == "synthetic" else 0.0,
                "recent_example_penalty": (
                    -self.settings.style.min_quality * 0.25 if example.id in recent_ids else 0.0
                ),
                "expression_penalty": -self._expression_penalty(example, observations),
            }
            score = sum(components.values())
            components_by_id[example.id] = {
                key: round(value, 6) for key, value in components.items()
            }
            scored.append(_ScoredExample(score=score, example=example))
        selected: list[_ScoredExample] = []
        non_verbatim = 0
        seen_groups: set[str] = set()
        for item in sorted(scored, key=lambda candidate: candidate.score, reverse=True):
            example = item.example
            is_non_verbatim = example.provenance_kind in {"adapted", "designed", "generated"}
            if v2_active and is_non_verbatim and non_verbatim >= 1:
                excluded[example.id] = "non_verbatim_limit"
                continue
            if v2_active and example.group_id and example.group_id in seen_groups:
                excluded[example.id] = "same_group_diversity"
                continue
            selected.append(item)
            non_verbatim += int(is_non_verbatim)
            if example.group_id:
                seen_groups.add(example.group_id)
            if len(selected) >= self.settings.style.top_k:
                break
        return StyleSearchResult(
            examples=[item.example for item in selected],
            scores={item.example.id: round(item.score, 6) for item in selected},
            score_components={item.example.id: components_by_id[item.example.id] for item in selected},
            excluded=excluded,
        )

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
        if example.provenance_kind not in {"verbatim", "adapted", "designed", "generated"}:
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

    @staticmethod
    def _behavior_bonus(
        example: StyleExample,
        decision: BehaviorDecision | None,
    ) -> float:
        if decision is None or not example.behavior_tags:
            return 0.0
        wanted = {item.id for item in decision.persona_affordances}
        matches = len(wanted.intersection(example.behavior_tags))
        return min(0.16, 0.08 * matches)

    @staticmethod
    def _expression_penalty(
        example: StyleExample,
        observations: ExpressionObservation | None,
    ) -> float:
        if observations is None:
            return 0.0
        penalty = 0.0
        for tag in set(example.expression_tags):
            item = observations.features.get(tag)
            if item is not None and item.weighted_rate is not None:
                penalty += 0.12 * item.weighted_rate
        return min(0.24, penalty)
