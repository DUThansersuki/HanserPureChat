from __future__ import annotations

from dataclasses import dataclass

from ..models import DialoguePlan, StyleAProjection, StyleExample
from .schemas import BehaviorDecision, TurnSignals


@dataclass(frozen=True, slots=True)
class ResponsePlanFeatures:
    """Local, soft A-side query derived from already-computed turn policy."""

    reaction_options: tuple[tuple[str, float], ...]
    stance: str | None
    continuation_policy: str
    interaction_frame: str | None
    interaction_target: str | None

    @property
    def reaction_weights(self) -> dict[str, float]:
        return dict(self.reaction_options)


def build_response_plan_features(
    plan: DialoguePlan,
    signals: TurnSignals | None,
    decision: BehaviorDecision | None,
) -> ResponsePlanFeatures:
    """Keep all weighted safe reaction options; do not invent a single act."""

    options = tuple(
        (item.id, float(item.weight))
        for item in (decision.persona_affordances if decision else [])
        if item.id != "direct_natural_reply" and item.weight > 0
    )
    return ResponsePlanFeatures(
        reaction_options=options,
        stance=(decision.stance if decision else None),
        continuation_policy=plan.continuation_policy,
        interaction_frame=_observed(signals, "interaction_frame"),
        interaction_target=_observed(signals, "interaction_target"),
    )


def a_projection_behavior_ids(example: StyleExample) -> set[str]:
    projection: StyleAProjection | None = example.a_projection
    if projection is not None and projection.review_status == "approved":
        return set(projection.behavior_ids)
    # Backward-compatible v3 candidates use the same reviewed behavior vocabulary.
    if example.projection_schema_version is None:
        return set(example.behavior_tags)
    return set()


def a_projection_compatible(
    example: StyleExample,
    features: ResponsePlanFeatures,
) -> bool:
    projection = example.a_projection
    if projection is None or projection.review_status != "approved":
        return example.projection_schema_version is None
    actual = {
        "stance": features.stance,
        "continuation_policy": features.continuation_policy,
        "interaction_frame": features.interaction_frame,
        "interaction_target": features.interaction_target,
    }
    for field, expected in projection.compatibility_features.items():
        current = actual.get(field)
        if current is not None and current != "unknown" and current != expected:
            return False
    return True


def _observed(signals: TurnSignals | None, field: str) -> str | None:
    observation = signals.get(field) if signals is not None else None
    if (
        observation is not None
        and observation.status == "observed"
        and observation.confidence in {"medium", "high"}
        and isinstance(observation.value, str)
    ):
        return observation.value
    return None
