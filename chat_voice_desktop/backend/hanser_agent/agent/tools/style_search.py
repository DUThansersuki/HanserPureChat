from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from pydantic import BaseModel, Field

from ... import db
from ...config import Settings
from ...models import DialoguePlan, StyleExample
from ...persona.data_pipeline import STYLE_COLLECTION, label_style, style_row_to_model
from ...persona.schemas import BehaviorDecision, ExpressionObservation, TurnSignals
from ...persona.style_applicability import (
    has_specific_behavior_applicability,
    style_applicability_exclusion_reason,
)
from ...persona.style_eligibility import style_exclusion_reason
from ...persona.style_ranking import style_score_components
from ...persona.style_projection import (
    a_projection_behavior_ids,
    a_projection_compatible,
    build_response_plan_features,
)
from ...retrieval.style_lexical import StyleLexicalIndex, StyleLexicalMatch, style_terms


class StyleSearchResult(BaseModel):
    examples: list[StyleExample] = Field(default_factory=list)
    scores: dict[str, float] = Field(default_factory=dict)
    score_components: dict[str, dict[str, float]] = Field(default_factory=dict)
    match_details: dict[str, dict[str, object]] = Field(default_factory=dict)
    excluded: dict[str, str] = Field(default_factory=dict)
    empty_reason: str | None = None
    candidate_counts: dict[str, int] = Field(default_factory=dict)
    exclusion_counts: dict[str, int] = Field(default_factory=dict)
    generation: str | None = None
    revision: int | None = None
    retrieved_style_ids: list[str] = Field(default_factory=list)
    matched_by: dict[str, list[str]] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _CorpusItem:
    example: StyleExample
    content_fields: dict[str, str]
    primary_terms: frozenset[str]


@dataclass(frozen=True, slots=True)
class _ScoredExample:
    score: float
    example: StyleExample
    components: dict[str, float]


class StyleSearchTool:
    """Retrieve applicable, reviewed reactions for style only, never facts."""

    name = "style_search"
    description = "检索审核过的真实表达样例及显式适配样例，仅用于风格。"
    _FACT_PAYLOAD = re.compile(
        r"(?:我|憨憨|我们).{0,12}(?:喜欢|去过|去|来过|有|没有|没|看过|吃|喝|"
        r"唱|录|配|买|住|工作|出差|过敏|嗓子|小时候|以前|上次|现在|今天|"
        r"最近|打算|准备|觉得|记得|知道)"
    )
    _LOW_SIGNAL_MATCHES = {
        "一个", "一件", "一件事", "事情", "故事", "之前", "以前", "以后", "最近",
        "现在", "今天", "这个", "那个", "可以", "也可", "的话", "的一", "什么", "怎么",
        "讲讲", "说说", "聊聊", "谈谈", "你的", "你嘛",
    }
    _MIN_CONTENT_COVERAGE = 0.40
    def __init__(
        self,
        *,
        settings: Settings,
        strict_v2: bool = False,
        pinned_generation: str | None = None,
    ):
        self.settings = settings
        self.strict_v2 = strict_v2
        self.pinned_generation = pinned_generation
        self._corpus_key: tuple[bool, str | None, int | None, float] | None = None
        self._corpus: dict[str, _CorpusItem] = {}
        self._lexical_index: StyleLexicalIndex | None = None
        self._static_exclusion_counts: dict[str, int] = {}

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
        if not self.settings.style.enabled:
            return StyleSearchResult(empty_reason="disabled")
        if plan.response_mode == "factual":
            return StyleSearchResult(empty_reason="factual_mode")

        v2_active = self.strict_v2 or turn_signals is not None or behavior_decision is not None
        labels = label_style(message, "") if turn_signals is None else {}
        scenes = self._scene_features(labels, turn_signals, plan)
        active_generation: str | None = None
        active_revision: int | None = None
        with db.connect(self.settings.db_path) as conn:
            if v2_active:
                active = conn.execute(
                    "SELECT generation, revision FROM active_index_generations WHERE collection = ?",
                    (STYLE_COLLECTION,),
                ).fetchone()
                if active is None:
                    return StyleSearchResult(empty_reason="generation_unavailable")
                active_generation = str(active["generation"])
                active_revision = int(active["revision"])
                if self.pinned_generation is not None and active_generation != self.pinned_generation:
                    return StyleSearchResult(
                        excluded={"__generation__": "active_generation_incompatible_with_persona_package"},
                        empty_reason="generation_incompatible",
                        generation=active_generation,
                        revision=active_revision,
                    )
            corpus_key = (v2_active, active_generation, active_revision, self.settings.style.min_quality)
            if corpus_key != self._corpus_key:
                clause = "AND index_generation = ?" if v2_active else ""
                params: tuple[object, ...] = (
                    (self.settings.style.min_quality, active_generation)
                    if v2_active else (self.settings.style.min_quality,)
                )
                rows = conn.execute(
                    f"SELECT * FROM style_examples WHERE review_status = 'approved' "
                    f"AND quality_score >= ? {clause}",
                    params,
                ).fetchall()
                loaded: dict[str, _CorpusItem] = {}
                static_exclusions: Counter[str] = Counter()
                for row in rows:
                    example = style_row_to_model(row)
                    if v2_active:
                        static_reason = style_exclusion_reason(
                            example,
                            active_generation=active_generation or "",
                            min_quality=self.settings.style.min_quality,
                        )
                        if static_reason is not None:
                            static_exclusions[static_reason] += 1
                            continue
                    q_projection = example.q_projection
                    if q_projection is not None and q_projection.review_status == "approved":
                        content_fields = {
                            "trigger_text": (
                                example.source_user_turn
                                if q_projection.trigger_text_ref else ""
                            ),
                            "retrieval_description": q_projection.scene_description,
                        }
                    elif example.projection_schema_version is None:
                        content_fields = {
                            "trigger_text": example.source_user_turn or example.user_context,
                            "retrieval_description": example.retrieval_description,
                        }
                    else:
                        content_fields = {"trigger_text": "", "retrieval_description": ""}
                    loaded[example.id] = _CorpusItem(
                        example,
                        content_fields,
                        frozenset(style_terms(
                            " ".join(content_fields.values()),
                            self.settings.userdict_path,
                        )),
                    )
                self._corpus = loaded
                self._lexical_index = StyleLexicalIndex.build(
                    {item_id: item.content_fields for item_id, item in loaded.items()},
                    userdict_path=self.settings.userdict_path,
                )
                self._static_exclusion_counts = dict(static_exclusions)
                self._corpus_key = corpus_key
        corpus = self._corpus
        if not corpus:
            return StyleSearchResult(
                empty_reason="no_eligible_corpus",
                exclusion_counts=self._static_exclusion_counts,
                generation=active_generation,
                revision=active_revision,
            )

        recent_ids = set(recent_example_ids or [])
        excluded: dict[str, str] = {}
        applicability_details: dict[str, list[dict[str, object]]] = {}
        applicable_corpus: dict[str, _CorpusItem] = {}
        for item_id, item in corpus.items():
            reason = style_exclusion_reason(
                item.example,
                active_generation=active_generation or "",
                min_quality=self.settings.style.min_quality,
                decision=behavior_decision,
            ) if v2_active else None
            if reason is None:
                reason, details = style_applicability_exclusion_reason(
                    item.example, turn_signals
                )
                if details:
                    applicability_details[item_id] = details
            if reason is not None:
                excluded[item_id] = reason
            else:
                applicable_corpus[item_id] = item

        matches = self._lexical_index.score(message) if self._lexical_index else {}
        pool_size = self.settings.style.candidate_pool_size
        channel_size = max(1, pool_size // 2)
        content_candidates = sorted(
            (pair for pair in matches.items() if pair[0] in applicable_corpus),
            key=lambda pair: (-pair[1].raw_score, -pair[1].coverage, pair[0]),
        )
        content_ids = [item_id for item_id, _ in content_candidates[:channel_size]]
        response_features = build_response_plan_features(
            plan, turn_signals, behavior_decision
        )
        affordances = response_features.reaction_weights
        behavior_candidates = [
            item for item in applicable_corpus.values()
            if a_projection_behavior_ids(item.example).intersection(affordances)
            and a_projection_compatible(item.example, response_features)
        ]
        behavior_candidates.sort(key=lambda item: (
            -sum(
                affordances[tag]
                for tag in a_projection_behavior_ids(item.example)
                if tag in affordances
            ),
            item.example.id in recent_ids,
            -item.example.quality_score,
            -item.example.authenticity_score,
            item.example.id,
        ))
        behavior_ids = [item.example.id for item in behavior_candidates[:channel_size]]
        ids = list(dict.fromkeys([*content_ids, *behavior_ids]))[:pool_size]
        route_by_id = {
            item_id: [
                route for route, route_ids in (("Q", content_ids), ("A", behavior_ids))
                if item_id in route_ids
            ]
            for item_id in ids
        }

        query_tokens = set(style_terms(message, self.settings.userdict_path))
        scored: list[_ScoredExample] = []
        components_by_id: dict[str, dict[str, float]] = {}
        match_details: dict[str, dict[str, object]] = {}
        for item_id in ids:
            item = applicable_corpus[item_id]
            example = item.example
            if len(example.character_response) > 240:
                excluded[item_id] = "response_too_long"
                continue
            if not v2_active and (
                self._FACT_PAYLOAD.search(example.character_response)
                or (example.source_type == "real" and any(
                    token in example.character_response for token in ("我", "我们", "憨憨")
                ))
            ):
                excluded[item_id] = "legacy_first_person_fact_guard"
                continue

            match = matches.get(item_id)
            meaningful = self._content_match_is_admissible(
                message, query_tokens, item.primary_terms, item.content_fields, match
            )
            reaction_ids = a_projection_behavior_ids(example)
            behavior_weight = sum(
                affordances[tag] for tag in reaction_ids if tag in affordances
            )
            if not meaningful:
                if behavior_weight <= 0:
                    excluded[item_id] = "no_applicable_signal"
                    continue
                if not has_specific_behavior_applicability(example):
                    excluded[item_id] = "behavior_match_too_broad"
                    continue
            components = style_score_components(
                content_relevance=self._content_relevance(match) if meaningful else 0.0,
                quality_score=example.quality_score,
                authenticity_score=example.authenticity_score,
                response_mode_match=example.response_mode == plan.response_mode,
                behavior_tags=set(example.behavior_tags),
                decision=behavior_decision or BehaviorDecision(),
                expression_tags=set(example.expression_tags),
                observations=observations,
                repetition_penalty=repetition_penalty,
                scene_match=example.scene in scenes,
                length_match=example.answer_length == plan.target_length,
                synthetic=example.source_type == "synthetic",
                recently_used=example.id in recent_ids,
            )
            score = sum(components.values())
            components_by_id[item_id] = {key: round(value, 6) for key, value in components.items()}
            if match is not None:
                match_details[item_id] = {
                    "raw_bm25": round(match.raw_score, 6),
                    "matched_terms": list(match.meaningful_terms),
                    "auxiliary_bigrams": list(match.auxiliary_bigrams),
                    "coverage": round(match.coverage, 6),
                    "field_scores": {key: round(value, 6) for key, value in match.field_scores.items()},
                }
            if item_id in applicability_details:
                match_details.setdefault(item_id, {})["applicability"] = applicability_details[item_id]
            match_details.setdefault(item_id, {})["matched_by"] = route_by_id[item_id]
            scored.append(_ScoredExample(score, example, components))

        selected: list[_ScoredExample] = []
        seen_groups: set[str] = set()
        non_verbatim = 0
        seen_units: set[str] = set()
        remaining = list(scored)
        while remaining and len(selected) < self.settings.style.top_k:
            item = min(remaining, key=lambda candidate: (
                -(candidate.score - (0.10 if candidate.example.group_id in seen_groups else 0.0)),
                candidate.example.id,
            ))
            remaining.remove(item)
            example = item.example
            stable_unit = example.unit_id or example.id
            if stable_unit in seen_units:
                excluded[example.id] = "duplicate_source_unit"
                continue
            is_non_verbatim = example.provenance_kind == "adapted"
            if v2_active and is_non_verbatim and non_verbatim >= 1:
                excluded[example.id] = "non_verbatim_limit"
                continue
            penalty = -0.10 if example.group_id and example.group_id in seen_groups else 0.0
            if penalty:
                components = {**item.components, "same_group_penalty": penalty}
                item = _ScoredExample(item.score + penalty, example, components)
                components_by_id[example.id] = {
                    key: round(value, 6) for key, value in components.items()
                }
            selected.append(item)
            seen_units.add(stable_unit)
            non_verbatim += int(is_non_verbatim)
            if example.group_id:
                seen_groups.add(example.group_id)

        exclusion_counts = dict(
            Counter(self._static_exclusion_counts) + Counter(excluded.values())
        )
        empty_reason = None
        if not selected:
            empty_reason = (
                "all_expression_blocked"
                if exclusion_counts and set(exclusion_counts) == {"expression_hard_blocked"}
                else "no_applicable_candidate"
            )
        debug_ids = [
            item.example.id
            for item in sorted(scored, key=lambda candidate: (-candidate.score, candidate.example.id))[:8]
        ]
        return StyleSearchResult(
            examples=[
                item.example.model_copy(update={
                    "matched_by": route_by_id.get(item.example.id, [])
                })
                for item in selected
            ],
            scores={item.example.id: round(item.score, 6) for item in selected},
            score_components={item.example.id: components_by_id[item.example.id] for item in selected},
            match_details={item_id: match_details.get(item_id, {}) for item_id in debug_ids},
            excluded=excluded,
            empty_reason=empty_reason,
            candidate_counts={
                "eligible_corpus": len(corpus),
                "applicable_corpus": len(applicable_corpus),
                "static_excluded": sum(self._static_exclusion_counts.values()),
                "content_channel": len(content_ids),
                "content_channel_overflow": max(0, len(content_candidates) - len(content_ids)),
                "behavior_channel": len(behavior_ids),
                "behavior_channel_overflow": max(0, len(behavior_candidates) - len(behavior_ids)),
                "merged": len(ids),
                "applicable": len(scored), "selected": len(selected),
            },
            exclusion_counts=exclusion_counts,
            generation=active_generation,
            revision=active_revision,
            retrieved_style_ids=ids,
            matched_by={
                item.example.id: route_by_id.get(item.example.id, [])
                for item in selected
            },
        )

    @classmethod
    def _meaningful_content_overlap(cls, query_tokens: set[str], example_tokens: set[str]) -> bool:
        return any(
            len(token) >= 2 and token not in cls._LOW_SIGNAL_MATCHES
            for token in query_tokens.intersection(example_tokens)
        )

    @classmethod
    def _content_match_is_admissible(
        cls,
        message: str,
        query_tokens: set[str],
        primary_terms: frozenset[str],
        fields: dict[str, str],
        match: StyleLexicalMatch | None,
    ) -> bool:
        return bool(
            match is not None
            and match.coverage >= cls._MIN_CONTENT_COVERAGE
            and cls._meaningful_content_overlap(
                query_tokens, set(match.meaningful_terms).intersection(primary_terms)
            )
            and cls._boundary_compatible(message, fields)
        )

    @staticmethod
    def _boundary_compatible(message: str, fields: dict[str, str]) -> bool:
        if "".join(message.split()).strip("！!。,.，~～") != "你好":
            return True
        combined = " ".join(fields.values())
        return not ("你好久" in combined and not re.search(
            r"(?:^|[\s，。！？!?])你好(?:[\s，。！？!?]|$)", combined
        ))

    @staticmethod
    def _content_relevance(match: StyleLexicalMatch | None) -> float:
        if match is None or match.raw_score <= 0:
            return 0.0
        return min(1.0, (match.raw_score / (match.raw_score + 2.0)) * 0.7 + match.coverage * 0.3)

    @staticmethod
    def _scene_features(labels: dict[str, object], signals: TurnSignals | None, plan: DialoguePlan) -> set[str]:
        features = {str(labels["scene"])} if labels.get("scene") else set()
        if signals is not None:
            emotion = signals.get("user_emotion")
            if (
                emotion is not None
                and emotion.status == "observed"
                and emotion.confidence in {"medium", "high"}
                and emotion.value in {"negative", "distressed", "angry"}
            ):
                features.update({"emotional_support", "comfort", "listening"})
            function = signals.get("dialogue_function")
            if (
                function is not None
                and function.status == "observed"
                and function.confidence in {"medium", "high"}
                and isinstance(function.value, str)
            ):
                features.add(function.value)
                if function.value == "end":
                    features.update({"closing", "natural_close"})
                elif function.value == "playful":
                    features.update({"teasing", "playful"})
        if plan.dialogue_function != "unknown":
            features.add(plan.dialogue_function)
        return features
