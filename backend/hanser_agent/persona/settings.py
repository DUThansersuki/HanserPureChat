from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schemas import EffectivePersonaSettings, SettingTrace


class ParameterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default: bool | int | float | str
    minimum: int | float | None = None
    maximum: int | float | None = None
    allowed: list[str] = Field(default_factory=list)
    visibility: Literal["user", "owner"]
    kind: Literal[
        "hard_cap", "soft_weight", "target_rate", "presentation", "observation"
    ]
    help: str

    @model_validator(mode="after")
    def validate_default(self) -> "ParameterSpec":
        if self.allowed and self.default not in self.allowed:
            raise ValueError(f"default {self.default!r} is not in allowed values")
        if isinstance(self.default, (int, float)) and not isinstance(self.default, bool):
            if self.minimum is not None and self.default < self.minimum:
                raise ValueError("default is below minimum")
            if self.maximum is not None and self.default > self.maximum:
                raise ValueError("default is above maximum")
        return self


class HardBoundaryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adult_humor_requires: list[str]
    never_relaxed_by: list[str]
    explicit_disable_is_hard: bool = True
    recent_repetition_is_hard: bool = False


class ExpressionPolicyFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    profile_id: str = "balanced_candidate"
    parameters: dict[str, ParameterSpec]
    hard_boundaries: HardBoundaryPolicy


class ParameterEffect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: bool | int | float | str
    reason: str


class ProductOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    override_id: str
    target_trait: str
    scope: str
    basis: Literal["owner_preference"]
    authority_ref: str
    descriptive_status: str
    operation: Literal["reduce_prior", "increase_prior", "set_boundary", "preserve"]
    requested_effect: str
    reason: str
    status: Literal["candidate", "validated", "released", "retired"]
    evaluation_tags: list[str] = Field(default_factory=list)
    parameter_effects: dict[str, ParameterEffect] = Field(default_factory=dict)


class ProductOverrideFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    overrides: list[ProductOverride]


def load_effective_settings(
    expression_policy_path: str | Path,
    product_overrides_path: str | Path,
    *,
    lifecycle: Literal["preview", "production"] = "preview",
) -> EffectivePersonaSettings:
    """Resolve candidate defaults and explicit product overrides without user input or IO side effects."""

    expression_path = Path(expression_policy_path)
    override_path = Path(product_overrides_path)
    expression = ExpressionPolicyFile.model_validate(
        yaml.safe_load(expression_path.read_text(encoding="utf-8")) or {}
    )
    overrides = ProductOverrideFile.model_validate(
        yaml.safe_load(override_path.read_text(encoding="utf-8")) or {}
    )

    allowed_names = set(EffectivePersonaSettings.model_fields) - {
        "schema_version",
        "profile_id",
        "trace",
    }
    unknown_specs = set(expression.parameters) - allowed_names
    if unknown_specs:
        raise ValueError(f"unknown expression parameters: {sorted(unknown_specs)}")
    missing_specs = allowed_names - set(expression.parameters)
    if missing_specs:
        raise ValueError(f"missing expression parameters: {sorted(missing_specs)}")

    values: dict[str, Any] = {
        name: spec.default for name, spec in expression.parameters.items()
    }
    trace: dict[str, SettingTrace] = {
        name: SettingTrace(
            descriptive=None,
            requested=spec.default,
            effective=spec.default,
            source=f"{expression_path.name}:default",
            reason="candidate engineering default; not a measured persona frequency",
        )
        for name, spec in expression.parameters.items()
    }

    active_statuses = (
        {"candidate", "validated"}
        if lifecycle == "preview"
        else {"released"}
    )
    active_overrides = [item for item in overrides.overrides if item.status in active_statuses]
    ids = [item.override_id for item in active_overrides]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate active product override id")
    overridden_parameters: dict[str, str] = {}
    for override in active_overrides:
        for name, effect in override.parameter_effects.items():
            if name not in expression.parameters:
                raise ValueError(
                    f"override {override.override_id!r} targets unknown parameter {name!r}"
                )
            previous = overridden_parameters.get(name)
            if previous is not None:
                raise ValueError(
                    f"active product overrides {previous!r} and {override.override_id!r} "
                    f"both target {name!r}; resolve the conflict explicitly"
                )
            _validate_against_spec(name, effect.value, expression.parameters[name])
            overridden_parameters[name] = override.override_id
            values[name] = effect.value
            trace[name] = SettingTrace(
                descriptive=None,
                requested=effect.value,
                effective=effect.value,
                source=override.override_id,
                reason=effect.reason,
            )

    return EffectivePersonaSettings(
        profile_id=expression.profile_id,
        trace=trace,
        **values,
    )


def _validate_against_spec(name: str, value: object, spec: ParameterSpec) -> None:
    if spec.allowed and value not in spec.allowed:
        raise ValueError(f"{name}={value!r} is not one of {spec.allowed!r}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if spec.minimum is not None and value < spec.minimum:
            raise ValueError(f"{name}={value!r} is below {spec.minimum}")
        if spec.maximum is not None and value > spec.maximum:
            raise ValueError(f"{name}={value!r} is above {spec.maximum}")
