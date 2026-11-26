from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..models import StyleExample
from .schemas import BehaviorDecision
from .style_applicability import style_contract_exclusion_reason


def style_exclusion_reason(
    item: StyleExample | Mapping[str, Any],
    *,
    active_generation: str,
    min_quality: float = 0.0,
    hidden_groups: set[str] | None = None,
    decision: BehaviorDecision | None = None,
) -> str | None:
    """Return the first stable runtime eligibility failure, or ``None``.

    Runtime retrieval and frozen-candidate evaluation call this same function so
    approval, provenance and expression boundaries cannot drift apart.
    """

    hidden_groups = hidden_groups or set()
    get = item.get if isinstance(item, Mapping) else lambda key, default=None: getattr(item, key, default)

    if str(get("index_generation", "")) != active_generation:
        return "generation_mismatch"
    if get("review_status") != "approved":
        return "outer_review_not_approved"
    if get("schema_review_status") != "approved":
        return "schema_review_not_approved"
    if float(get("quality_score", 0.0) or 0.0) < min_quality:
        return "below_min_quality"
    source_tier = str(get("source_tier", ""))
    source_type = str(get("source_type", ""))
    if (source_tier, source_type) not in {("primary", "real"), ("secondary", "real")}:
        return "source_not_runtime_eligible"
    if get("source_speaker") != "hanser":
        return "source_speaker_mismatch"
    attribution_status = get("attribution_status")
    if attribution_status is not None and attribution_status != "verified":
        return "speaker_attribution_not_verified"
    contract_reason = style_contract_exclusion_reason(item)
    if contract_reason is not None:
        return contract_reason
    runtime_scope = {str(value) for value in (get("runtime_scope", []) or [])}
    if "style_runtime" not in runtime_scope:
        return "runtime_scope_missing"
    group_id = str(get("group_id", "") or "")
    if not group_id:
        return "group_missing"
    if bool(get("hidden_eval", False)) or group_id in hidden_groups:
        return "hidden_eval_group"
    provenance = str(get("provenance_kind", ""))
    speaker_status = str(get("speaker_status", ""))
    if provenance == "verbatim":
        if speaker_status not in {"audio_verified", "transcript_verified"}:
            return "speaker_unverified"
    elif provenance == "adapted":
        if speaker_status != "not_applicable":
            return "adapted_speaker_status_invalid"
    else:
        return "provenance_not_runtime_eligible"
    if get("payload_class") not in {"reaction_only", "turn_local_stance"}:
        return "payload_not_runtime_safe"

    expression_tags = {str(value) for value in (get("expression_tags", []) or [])}
    if "innuendo" in expression_tags and get("audience", "unknown") == "unknown":
        return "high_risk_audience_unknown"
    if decision is not None:
        blocked = set(decision.expression_caps.hard_disallowed)
        if blocked.intersection(expression_tags):
            return "expression_hard_blocked"
    return None
