from __future__ import annotations

import hashlib

from ..persona.schemas import BehaviorDecision
from .performance import (
    AllowedPerformance,
    Delivery,
    PerformanceConstraints,
    PerformanceIntent,
    json_identity,
)


_HUMOR_DELIVERIES = {
    Delivery.AMUSED,
    Delivery.TEASING,
    Delivery.ANNOYED_PLAYFUL,
    Delivery.DEADPAN,
}
_TEASING_DELIVERIES = {Delivery.TEASING, Delivery.ANNOYED_PLAYFUL}


class PerformanceConstraintAdapter:
    """Versioned translation from integer Persona caps to performance limits."""

    revision = "performance_constraints_1"

    def convert(self, decision: BehaviorDecision) -> PerformanceConstraints:
        disallowed = set(decision.expression_caps.hard_disallowed)
        forbidden: set[Delivery] = set()
        maximums: dict[Delivery, float] = {}

        teasing_level = decision.expression_caps.hard_intensity_limits.get("teasing")
        if teasing_level == 0:
            forbidden.update(_TEASING_DELIVERIES)
        elif teasing_level == 1:
            maximums.update({delivery: 0.35 for delivery in _TEASING_DELIVERIES})
        elif teasing_level == 2:
            maximums.update({delivery: 0.55 for delivery in _TEASING_DELIVERIES})

        if "humor" in disallowed:
            forbidden.update(_HUMOR_DELIVERIES)
        if "teasing" in disallowed:
            forbidden.update(_TEASING_DELIVERIES)

        if "style_in_factual_mode" in disallowed:
            forbidden.update(
                delivery
                for delivery in Delivery
                if delivery not in {Delivery.NEUTRAL, Delivery.SERIOUS}
            )
            maximums[Delivery.SERIOUS] = 0.35

        refs = list(
            dict.fromkeys(
                [
                    self.revision,
                    *decision.boundary_ids,
                    *(item.requirement_id for item in decision.must_not),
                ]
            )
        )
        return PerformanceConstraints(
            forbidden_delivery=sorted(forbidden, key=lambda item: item.value),
            max_intensity_by_delivery=maximums,
            forbidden_features=sorted(disallowed),
            constraint_refs=refs,
        )


class PerformancePolicyResolver:
    """Apply the already-resolved Persona boundary without re-reading user text."""

    revision = "performance_policy_1"

    def __init__(self, adapter: PerformanceConstraintAdapter | None = None):
        self.adapter = adapter or PerformanceConstraintAdapter()

    def resolve(
        self,
        requested: PerformanceIntent | None,
        decision: BehaviorDecision | None,
    ) -> AllowedPerformance:
        if decision is None:
            return AllowedPerformance(
                decisions=["legacy_without_behavior_decision:neutral"],
            )

        constraints = self.adapter.convert(decision)
        digest = hashlib.sha256(
            json_identity(constraints.model_dump(mode="json")).encode("utf-8")
        ).hexdigest()[:16]
        constraints_ref = f"{self.adapter.revision}:{digest}"
        intent = requested or PerformanceIntent(
            delivery=Delivery.NEUTRAL,
            intensity=0.2,
        )
        decisions: list[str] = []
        delivery = intent.delivery
        intensity = intent.intensity

        if requested is None:
            decisions.append("missing_intent:neutral")
        if delivery in constraints.forbidden_delivery:
            decisions.append(f"delivery_forbidden:{delivery.value}")
            delivery = Delivery.NEUTRAL
            intensity = 0.2

        maximum = constraints.max_intensity_by_delivery.get(delivery)
        if maximum is not None and intensity > maximum:
            decisions.append(f"intensity_capped:{delivery.value}:{maximum:.2f}")
            intensity = maximum

        return AllowedPerformance(
            delivery=delivery,
            intensity=intensity,
            forbidden_features=constraints.forbidden_features,
            constraints_ref=constraints_ref,
            decisions=decisions,
        )
