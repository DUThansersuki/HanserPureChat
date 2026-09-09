from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .schemas import BehaviorDecision, ExpressionObservation


def style_score_components(
    *,
    dense_score: float,
    quality_score: float,
    authenticity_score: float,
    response_mode_match: bool,
    behavior_tags: set[str],
    decision: BehaviorDecision,
    expression_tags: set[str],
    observations: ExpressionObservation | None,
    repetition_penalty: float,
    scene_match: bool = False,
    length_match: bool = False,
    synthetic: bool = False,
    recently_used: bool = False,
) -> dict[str, float]:
    weights = {item.id: item.weight for item in decision.persona_affordances}
    behavior_weight = sum(weights[tag] for tag in behavior_tags if tag in weights)
    return {
        "dense": 0.55 * dense_score,
        "quality": 0.20 * quality_score,
        "authenticity": 0.15 * authenticity_score,
        "scene": 0.12 if scene_match else 0.0,
        "mode": 0.08 if response_mode_match else 0.0,
        "length": 0.05 if length_match else 0.0,
        "behavior": min(0.18, 0.12 * behavior_weight),
        "expression_opportunity": _expression_opportunity_bonus(
            expression_tags, weights
        ),
        "source_penalty": -0.12 if synthetic else 0.0,
        "recent_example_penalty": -0.5 * repetition_penalty if recently_used else 0.0,
        "expression_penalty": -_expression_penalty(
            expression_tags, observations, repetition_penalty
        ),
    }


def _expression_opportunity_bonus(
    expression_tags: set[str],
    affordance_weights: Mapping[str, float],
) -> float:
    requested = {
        "profanity": affordance_weights.get("light_profanity_release", 0.0),
    }
    return min(
        0.35,
        sum(0.35 for tag, weight in requested.items() if tag in expression_tags and weight > 0),
    )


def rank_fixed_style_candidates(
    candidates: Sequence[Mapping[str, Any]],
    decision: BehaviorDecision,
    *,
    response_mode: str,
    active_generation: str,
    hidden_groups: set[str] | None = None,
    observations: ExpressionObservation | None = None,
    recent_example_ids: set[str] | None = None,
    repetition_penalty: float = 0.30,
    top_k: int = 3,
) -> dict[str, object]:
    """Pure candidate-fixture ranking used for deterministic diagnostics.

    Real runtime dense retrieval happens elsewhere. This function deliberately
    accepts frozen dense scores so offline tests never construct an embedder.
    """

    hidden_groups = hidden_groups or set()
    blocked = set(decision.expression_caps.hard_disallowed)
    recent_example_ids = recent_example_ids or set()
    excluded: dict[str, str] = {}
    scored: list[tuple[float, str, Mapping[str, Any], dict[str, float]]] = []
    for item in candidates:
        item_id = str(item["id"])
        reason = _hard_exclusion(
            item,
            active_generation=active_generation,
            hidden_groups=hidden_groups,
            blocked=blocked,
        )
        if reason is not None:
            excluded[item_id] = reason
            continue
        behavior_tags = {str(value) for value in item.get("behavior_tags", [])}
        expression_tags = {str(value) for value in item.get("expression_tags", [])}
        components = style_score_components(
            dense_score=float(item.get("dense_score", 0.0)),
            quality_score=float(item.get("quality_score", 0.0)),
            authenticity_score=float(item.get("authenticity_score", 0.0)),
            response_mode_match=item.get("response_mode") == response_mode,
            behavior_tags=behavior_tags,
            decision=decision,
            expression_tags=expression_tags,
            observations=observations,
            repetition_penalty=repetition_penalty,
            recently_used=item_id in recent_example_ids,
        )
        score = sum(components.values())
        scored.append((score, item_id, item, components))

    selected: list[dict[str, object]] = []
    seen_groups: set[str] = set()
    non_verbatim = 0
    remaining = list(scored)
    while remaining and len(selected) < top_k:
        ranked = sorted(
            remaining,
            key=lambda row: (-(row[0] - (0.12 if str(row[2].get("group_id", "")) in seen_groups else 0.0)), row[1]),
        )
        score, item_id, item, components = ranked[0]
        remaining.remove(ranked[0])
        group_id = str(item.get("group_id", ""))
        provenance = str(item.get("provenance_kind", ""))
        is_non_verbatim = provenance in {"adapted", "designed", "generated"}
        if is_non_verbatim and non_verbatim >= 1:
            excluded[item_id] = "non_verbatim_limit"
            continue
        group_penalty = -0.12 if group_id in seen_groups else 0.0
        components = {**components, "same_group_penalty": group_penalty}
        score += group_penalty
        selected.append(
            {
                "id": item_id,
                "score": round(score, 6),
                "score_components": {
                    name: round(value, 6) for name, value in components.items()
                },
            }
        )
        seen_groups.add(group_id)
        non_verbatim += int(is_non_verbatim)
    return {"selected": selected, "excluded": excluded}


def _hard_exclusion(
    item: Mapping[str, Any],
    *,
    active_generation: str,
    hidden_groups: set[str],
    blocked: set[str],
) -> str | None:
    if str(item.get("index_generation")) != active_generation:
        return "generation_mismatch"
    if item.get("review_status") != "approved" or item.get("schema_review_status") != "approved":
        return "not_v2_approved"
    provenance = str(item.get("provenance_kind", ""))
    if provenance == "verbatim" and item.get("speaker_status") not in {"audio_verified", "transcript_verified"}:
        return "speaker_unverified"
    if provenance == "adapted" and item.get("speaker_status") != "not_applicable":
        return "adapted_speaker_status_invalid"
    if provenance not in {"verbatim", "adapted"}:
        return "provenance_not_runtime_eligible"
    if str(item.get("group_id", "")) in hidden_groups:
        return "hidden_eval_group"
    if item.get("payload_class") not in {"reaction_only", "turn_local_stance"}:
        return "payload_not_runtime_safe"
    if blocked.intersection(str(value) for value in item.get("expression_tags", [])):
        return "expression_hard_blocked"
    return None


def _expression_penalty(
    expression_tags: set[str],
    observations: ExpressionObservation | None,
    repetition_penalty: float,
) -> float:
    if observations is None:
        return 0.0
    total = 0.0
    for tag in expression_tags:
        item = observations.features.get(tag)
        if item is not None and item.weighted_rate is not None:
            total += repetition_penalty * 0.4 * item.weighted_rate
    return min(repetition_penalty * 0.8, total)
