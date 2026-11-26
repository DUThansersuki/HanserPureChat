from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..models import MemoryItem
from .schemas import ExplicitPreferenceEvent


def infer_expression_permissions(
    events: Sequence[ExplicitPreferenceEvent],
    *,
    current_message_id: str | None = None,
    persistent_preferences: Sequence[MemoryItem] = (),
    explicit_overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Merge extracted events and durable preferences without rescanning source text."""

    permissions: dict[str, str] = {}
    for memory in sorted(persistent_preferences, key=lambda item: item.created_at):
        if (
            memory.status == "active"
            and memory.predicate == "expression_permission"
            and memory.object_value in {"allow", "deny", "unknown"}
        ):
            feature = _memory_feature(memory)
            if feature:
                permissions[feature] = str(memory.object_value)

    topic_permissions: dict[str, str] = {}
    for event in sorted(events, key=lambda item: item.created_at):
        key = _event_permission_key(event)
        if event.scope == "current_topic":
            if event.decision == "unknown":
                topic_permissions.pop(key, None)
            else:
                topic_permissions[key] = event.decision
            continue
        if (
            event.scope in {"conversation", "user"}
            or event.source_message_id == current_message_id
        ):
            permissions[key] = event.decision
            topic_permissions.pop(key, None)
            # Explicit restoration of a feature also restores its current-topic
            # deictic target; unrelated durable object restrictions remain.
            if event.decision == "allow" and event.target is None:
                for target in ("这件事", "这个梗", "这个话题"):
                    topic_permissions.pop(f"target:{event.feature}:{target}", None)

    permissions.update(topic_permissions)

    for feature, value in (explicit_overrides or {}).items():
        if value in {"allow", "deny", "unknown"}:
            permissions[str(feature)] = str(value)
    return permissions


def _memory_feature(memory: MemoryItem) -> str | None:
    prefix = "preference:expression:"
    if not memory.memory_key.startswith(prefix):
        return None
    parts = memory.memory_key[len(prefix):].split(":", 1)
    feature = parts[0]
    target = parts[1] if len(parts) > 1 else "*"
    if not feature:
        return None
    return feature if target == "*" else f"target:{feature}:{target}"


def _event_permission_key(event: ExplicitPreferenceEvent) -> str:
    if event.target:
        target = "".join(event.target.split()).casefold()
        return f"target:{event.feature}:{target}"
    return event.feature
