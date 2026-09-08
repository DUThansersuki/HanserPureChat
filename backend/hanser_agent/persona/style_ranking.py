from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .schemas import BehaviorDecision, ExpressionObservation


def rank_fixed_style_candidates(
    candidates: Sequence[Mapping[str, Any]],
    decision: BehaviorDecision,
    *,
    response_mode: str,
    active_generation: str,
    hidden_groups: set[str] | None = None,
    observations: ExpressionObservation | None = None,
    top_k: int = 3,
) -> dict[str, object]:
    """Pure candidate-fixture ranking used for deterministic diagnostics.

    Real runtime dense retrieval happens elsewhere. This function deliberately
    accepts frozen dense scores so offline tests never construct an embedder.
    """

    hidden_groups = hidden_groups or set()
    blocked = set(decision.expression_caps.hard_disallowed)
    wanted = {item.id for item in decision.persona_affordances}
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
        components = {
            "dense": 0.55 * float(item.get("dense_score", 0.0)),
            "quality": 0.20 * float(item.get("quality_score", 0.0)),
            "authenticity": 0.15 * float(item.get("authenticity_score", 0.0)),
            "mode": 0.08 if item.get("response_mode") == response_mode else 0.0,
            "behavior": min(0.16, 0.08 * len(wanted.intersection(behavior_tags))),
            "expression_penalty": -_expression_penalty(expression_tags, observations),
        }
        score = sum(components.values())
        scored.append((score, item_id, item, components))

    selected: list[dict[str, object]] = []
    seen_groups: set[str] = set()
    non_verbatim = 0
    for score, item_id, item, components in sorted(scored, key=lambda row: (-row[0], row[1])):
        group_id = str(item.get("group_id", ""))
        provenance = str(item.get("provenance_kind", ""))
        is_non_verbatim = provenance in {"adapted", "designed", "generated"}
        if group_id in seen_groups:
            excluded[item_id] = "same_group_diversity"
            continue
        if is_non_verbatim and non_verbatim >= 1:
            excluded[item_id] = "non_verbatim_limit"
            continue
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
        if len(selected) >= top_k:
            break
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
    if item.get("speaker_status") not in {"audio_verified", "transcript_verified"}:
        return "speaker_unverified"
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
) -> float:
    if observations is None:
        return 0.0
    total = 0.0
    for tag in expression_tags:
        item = observations.features.get(tag)
        if item is not None and item.weighted_rate is not None:
            total += 0.12 * item.weighted_rate
    return min(0.24, total)
