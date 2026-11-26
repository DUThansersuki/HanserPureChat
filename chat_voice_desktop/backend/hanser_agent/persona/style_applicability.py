from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..models import StyleApplicabilityPredicate, StyleExample
from .data_pipeline import replay_style_cleaning_operations
from .schemas import TurnSignals


_BOOL_FIELDS = {
    "explicit_stop",
    "disable_humor",
    "disable_profanity",
    "disable_innuendo",
    "disable_cutesy",
    "no_advice",
    "forced_agreement_request",
    "unresolved_reference",
    "unverified_shared_memory_claim",
    "quoted_or_hypothetical",
    "playful_frame",
    "distress",
    "tension",
}
_ENUM_VALUES: dict[str, set[str]] = {
    "user_emotion": {"neutral", "positive", "negative", "distressed", "angry", "unknown"},
    "humor_receptivity": {"welcome", "neutral", "avoid", "unknown"},
    "audience_age_status": {"adult", "minor", "unknown"},
    "dialogue_function": {"share", "confirm", "correct", "advice", "playful", "end", "other", "unknown"},
    "interaction_frame": {
        "praise", "achievement_share", "apology", "playful_blame",
        "serious_accusation", "other", "unknown",
    },
    "interaction_target": {"assistant", "current_user", "third_party", "unknown"},
}
_NARROW_DIALOGUE_FUNCTIONS = {"correct", "playful", "end"}
_CORROBORATING_BEHAVIOR_FIELDS = {
    "user_emotion", "playful_frame", "distress", "tension",
}


def validate_style_predicate_values(
    field: str,
    values: list[bool | str],
) -> str | None:
    """Validate a draft predicate without requiring a complete Style record."""

    if not values:
        return "applicability_predicate_empty"
    allowed: set[bool] | set[str] | None = (
        {True, False} if field in _BOOL_FIELDS else _ENUM_VALUES.get(field)
    )
    if allowed is None or any(
        type(value) is not type(next(iter(allowed))) or value not in allowed
        for value in values
    ):
        return "applicability_predicate_invalid"
    if "unknown" in values:
        return "applicability_unknown_not_assertable"
    return None


def has_specific_behavior_applicability(
    item: StyleExample | Mapping[str, Any],
) -> bool:
    """Whether behavior-only retrieval has a reviewed dialogue-policy key.

    Interaction frame/target observations remain applicability filters, but a
    single label is not sufficient retrieval evidence. Behavior-only matching
    needs either a narrow dialogue act or independent event, role, and state
    evidence, so one semantic-role error cannot open a narrow reaction.
    """

    get = item.get if isinstance(item, Mapping) else lambda key, default=None: getattr(item, key, default)
    if int(get("style_record_version", 2)) < 3:
        return True
    applicability = get("applicability") or {}
    predicates = (
        applicability.requires_all
        if hasattr(applicability, "requires_all")
        else applicability.get("requires_all", [])
    )
    required = {
        field: set(values)
        for field, values in (_predicate_parts(predicate) for predicate in predicates)
    }
    dialogue_values = required.get("dialogue_function", set())
    if dialogue_values and dialogue_values <= _NARROW_DIALOGUE_FUNCTIONS:
        return True

    has_role = "interaction_target" in required
    has_event = "interaction_frame" in required
    has_state = any(field in required for field in _CORROBORATING_BEHAVIOR_FIELDS)
    has_dialogue_context = "dialogue_function" in required and has_state
    return has_dialogue_context or (
        has_role and (has_event or "playful_frame" in required) and has_state
    )


def style_contract_exclusion_reason(
    item: StyleExample | Mapping[str, Any],
) -> str | None:
    """Validate the runtime portion of the versioned Style record contract."""

    get = item.get if isinstance(item, Mapping) else lambda key, default=None: getattr(item, key, default)
    version = int(get("style_record_version", 2))
    if version < 3:
        if not get("source_user_turn") or not get("source_response_turn"):
            return "source_turn_missing"
        return None
    if version != 3:
        return "style_record_version_unsupported"
    if not get("unit_id") or not get("source_episode_id"):
        return "source_unit_missing"
    if not get("source_response_turn"):
        return "source_response_missing"
    runtime_payload_kind = str(get("runtime_payload_kind", "legacy_full"))
    runtime_fragment = get("runtime_reaction_fragment")
    if not bool(get("runtime_payload_reviewed", False)):
        return "runtime_payload_unreviewed"
    if not list(get("behavior_tags", []) or []):
        return "runtime_reaction_pattern_missing"
    if runtime_payload_kind == "pattern_only":
        if runtime_fragment:
            return "pattern_only_has_fragment"
    elif runtime_payload_kind == "reviewed_fragment":
        if not runtime_fragment or str(runtime_fragment) not in str(get("character_response", "")):
            return "runtime_fragment_not_source_grounded"
        for predicate in list(get("applicability").requires_all if hasattr(get("applicability"), "requires_all") else (get("applicability") or {}).get("requires_all", [])):
            field, values = _predicate_parts(predicate)
            if field == "dialogue_function" and "correct" in values:
                return "correction_payload_must_be_pattern_only"
    else:
        return "runtime_payload_kind_unsupported"

    trigger_basis = get("trigger_basis")
    source_user_turn = get("source_user_turn")
    trigger_refs = list(get("trigger_evidence_refs", []) or [])
    if trigger_basis not in {"explicit", "restated", "inferred", "self_initiated", "unknown"}:
        return "trigger_basis_missing"
    if trigger_basis == "explicit":
        if not source_user_turn or not trigger_refs:
            return "explicit_trigger_evidence_missing"
    elif source_user_turn:
        return "inferred_trigger_in_source_user_turn"
    q_projection = get("q_projection")
    a_projection = get("a_projection")
    q_status = (
        q_projection.review_status
        if hasattr(q_projection, "review_status")
        else (q_projection or {}).get("review_status")
    )
    a_status = (
        a_projection.review_status
        if hasattr(a_projection, "review_status")
        else (a_projection or {}).get("review_status")
    )
    if (
        not source_user_turn
        and not str(get("retrieval_description", "")).strip()
        and q_status != "approved"
        and a_status != "approved"
    ):
        return "approved_retrieval_projection_missing"

    provenance = str(get("provenance_kind", ""))
    if provenance == "verbatim" and get("character_response") != get("source_response_turn"):
        operations = list(get("cleaning_operations", []) or [])
        replayed = replay_style_cleaning_operations(
            str(get("source_response_turn", "")), operations
        )
        if not operations or replayed is None or replayed != get("character_response"):
            return "verbatim_response_mismatch"

    transfer_scope = get("transfer_scope")
    applicability = get("applicability")
    if hasattr(applicability, "requires_all"):
        requires_all = applicability.requires_all
        forbids_any = applicability.forbids_any
    else:
        applicability = applicability or {}
        requires_all = applicability.get("requires_all", [])
        forbids_any = applicability.get("forbids_any", [])
    if transfer_scope == "reject":
        return "transfer_scope_rejected"
    if transfer_scope == "general" and requires_all:
        return "general_scope_has_requirements"
    if transfer_scope == "requires_context" and not requires_all:
        return "required_context_missing"
    if transfer_scope not in {"general", "requires_context"}:
        return "transfer_scope_missing"

    normalized_requires = [_predicate_parts(predicate) for predicate in requires_all]
    normalized_forbids = [_predicate_parts(predicate) for predicate in forbids_any]
    for field, values in [*normalized_requires, *normalized_forbids]:
        reason = validate_style_predicate_values(field, values)
        if reason is not None:
            return reason
    required_by_field: dict[str, set[bool | str]] = {}
    forbidden_by_field: dict[str, set[bool | str]] = {}
    for field, values in normalized_requires:
        current = set(values)
        required_by_field[field] = (
            required_by_field[field].intersection(current)
            if field in required_by_field else current
        )
    for field, values in normalized_forbids:
        forbidden_by_field.setdefault(field, set()).update(values)
    for field, required in required_by_field.items():
        if not required or not required.difference(forbidden_by_field.get(field, set())):
            return "applicability_predicate_contradiction"
    return None


def style_applicability_exclusion_reason(
    item: StyleExample | Mapping[str, Any],
    signals: TurnSignals | None,
) -> tuple[str | None, list[dict[str, object]]]:
    """Evaluate approved predicates with three-valued current-turn semantics."""

    get = item.get if isinstance(item, Mapping) else lambda key, default=None: getattr(item, key, default)
    if int(get("style_record_version", 2)) < 3:
        return None, []
    applicability = get("applicability")
    if isinstance(applicability, Mapping):
        requires_all = [StyleApplicabilityPredicate.model_validate(value) for value in applicability.get("requires_all", [])]
        forbids_any = [StyleApplicabilityPredicate.model_validate(value) for value in applicability.get("forbids_any", [])]
    else:
        requires_all = applicability.requires_all
        forbids_any = applicability.forbids_any
    checks: list[dict[str, object]] = []

    required_unknown = False
    required_conflict = False
    for predicate in requires_all:
        outcome, detail = _predicate_outcome(predicate, signals)
        checks.append({"kind": "required", **detail})
        required_unknown |= outcome == "unknown"
        required_conflict |= outcome == "false"
    if required_conflict:
        return "required_context_conflict", checks
    if required_unknown:
        return "required_context_unknown", checks

    for predicate in forbids_any:
        outcome, detail = _predicate_outcome(predicate, signals)
        checks.append({"kind": "forbidden", **detail})
        if outcome == "true":
            return "forbidden_context_match", checks
    return None, checks


def _predicate_parts(
    predicate: StyleApplicabilityPredicate | Mapping[str, Any],
) -> tuple[str, list[bool | str]]:
    if isinstance(predicate, Mapping):
        return str(predicate.get("field", "")), list(predicate.get("one_of", []) or [])
    return predicate.field, list(predicate.one_of)


def _predicate_outcome(
    predicate: StyleApplicabilityPredicate,
    signals: TurnSignals | None,
) -> tuple[str, dict[str, object]]:
    observation = signals.values.get(predicate.field) if signals is not None else None
    current: bool | str | None = None
    status = "unavailable"
    evidence_refs: list[str] = []
    if observation is not None:
        status = observation.status
        evidence_refs = list(observation.evidence_refs)
        if observation.status == "observed" and observation.confidence in {"medium", "high"}:
            current = observation.value
    if current is None or current == "unknown":
        outcome = "unknown"
    else:
        outcome = "true" if any(
            type(current) is type(expected) and current == expected
            for expected in predicate.one_of
        ) else "false"
    return outcome, {
        "field": predicate.field,
        "expected": list(predicate.one_of),
        "current": current,
        "status": status,
        "outcome": outcome,
        "evidence_refs": evidence_refs,
    }
