from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SignalName = Literal[
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
    "user_emotion",
    "playful_frame",
    "distress",
    "tension",
    "humor_receptivity",
    "audience_age_status",
    "dialogue_function",
]
SignalSource = Literal["rule", "planner", "explicit_setting"]
SignalConfidence = Literal["high", "medium", "low", "unknown"]
SignalStatus = Literal["observed", "unavailable", "invalid", "conflicted"]
SignalScope = Literal["current_turn", "current_topic", "conversation", "user"]
PreferenceFeature = Literal[
    "humor", "teasing", "profanity", "innuendo", "cutesy", "address", "advice"
]


class ExplicitPreferenceEvent(BaseModel):
    """Auditable user expression; this is the sole lexical permission contract."""

    model_config = ConfigDict(extra="forbid")

    feature: PreferenceFeature
    decision: Literal["allow", "deny", "unknown"]
    subject: Literal["current_user"] = "current_user"
    scope: SignalScope
    target: str | None = None
    source_message_id: str
    evidence_text: str
    evidence_start: int = Field(ge=0)
    evidence_end: int = Field(ge=0)
    created_at: datetime
    expires_at: datetime | None = None
    revoke_condition: str | None = None


class SignalObservation(BaseModel):
    """One provenance-bearing observation; confidence is categorical, not a probability."""

    model_config = ConfigDict(extra="forbid")

    name: SignalName
    value: bool | str | None = None
    source: SignalSource
    confidence: SignalConfidence = "unknown"
    evidence_refs: list[str] = Field(default_factory=list, max_length=8)
    scope: SignalScope = "current_turn"
    status: SignalStatus = "unavailable"
    model_reported_confidence: str | None = None
    conflict_refs: list[str] = Field(default_factory=list, max_length=8)
    hard_rule_eligible: bool = False


class TurnSignals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 2
    values: dict[str, SignalObservation] = Field(default_factory=dict)
    degraded_reasons: list[str] = Field(default_factory=list)
    preference_events: list[ExplicitPreferenceEvent] = Field(default_factory=list)

    def get(self, name: SignalName) -> SignalObservation | None:
        return self.values.get(name)

    def observed_bool(self, name: SignalName) -> bool | None:
        item = self.get(name)
        if item is None or item.status != "observed" or not isinstance(item.value, bool):
            return None
        return item.value

    def hard_bool(self, name: SignalName) -> bool | None:
        item = self.get(name)
        if (
            item is None
            or not item.hard_rule_eligible
            or item.status != "observed"
            or not isinstance(item.value, bool)
        ):
            return None
        return item.value


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    instruction: str
    source: Literal["boundary", "task", "explicit_user", "setting"]
    basis_refs: list[str] = Field(default_factory=list)
    scope: SignalScope = "current_turn"


class StanceGuidance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestion: Literal[
        "open",
        "agree",
        "partial_agree",
        "disagree",
        "uncertain",
        "preference_difference",
        "playful_reframe",
    ] = "open"
    basis_refs: list[str] = Field(default_factory=list)
    strength: Literal["weak", "medium", "strong"] = "weak"


class PersonaAffordance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    weight: float = Field(ge=0.0, le=1.0)
    basis_refs: list[str] = Field(default_factory=list)
    guidance: str = ""


class BehaviorSoftMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response_modes: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    dialogue_functions: list[str] = Field(default_factory=list)


class BehaviorCardPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    focus: str
    avoid: list[str] = Field(default_factory=list)


class BehaviorPrior(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prior_id: str
    status: Literal["candidate", "validated", "released", "retired"]
    basis: dict[str, object]
    soft_match: BehaviorSoftMatch
    downweight_when: list[str] = Field(default_factory=list)
    persona_affordances: list[PersonaAffordance] = Field(default_factory=list)
    soft_preferences: BehaviorCardPreferences
    boundary_refs: list[str] = Field(default_factory=list)
    evaluation_tags: list[str] = Field(default_factory=list)


class ExpressionCaps(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hard_disallowed: list[str] = Field(default_factory=list)
    hard_intensity_limits: dict[str, int] = Field(default_factory=dict)


class SoftPreference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preference_id: str
    instruction: str
    weight: float = Field(ge=0.0, le=1.0)
    basis_refs: list[str] = Field(default_factory=list)


class BehaviorDecision(BaseModel):
    """Hard requirements plus optional behavioral affordances, never an action script."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 2
    must_do: list[Requirement] = Field(default_factory=list)
    must_not: list[Requirement] = Field(default_factory=list)
    stance: StanceGuidance = Field(default_factory=StanceGuidance)
    persona_affordances: list[PersonaAffordance] = Field(default_factory=list)
    expression_caps: ExpressionCaps = Field(default_factory=ExpressionCaps)
    soft_preferences: list[SoftPreference] = Field(default_factory=list)
    selected_prior_ids: list[str] = Field(default_factory=list)
    boundary_ids: list[str] = Field(default_factory=list)
    signal_refs: list[str] = Field(default_factory=list)


class SettingTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    descriptive: object | None = None
    requested: object | None = None
    effective: object
    source: str
    reason: str = ""


class EffectivePersonaSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    profile_id: str = "balanced_candidate"
    humor_initiative: float = Field(default=0.35, ge=0.0, le=0.6)
    teasing_intensity: int = Field(default=1, ge=0, le=2)
    meme_affinity: float = Field(default=0.30, ge=0.0, le=0.6)
    profanity_level: int = Field(default=1, ge=0, le=2)
    innuendo_level: int = Field(default=1, ge=0, le=1)
    cutesy_bias: float = Field(default=0.10, ge=0.0, le=0.25)
    warmth: float = Field(default=0.55, ge=0.25, le=0.8)
    candor: float = Field(default=0.60, ge=0.3, le=0.8)
    reply_length: Literal["short", "adaptive", "detailed"] = "adaptive"
    address_bias: float = Field(default=0.15, ge=0.0, le=0.3)
    display_punctuation: Literal["legacy_sparse", "natural"] = "legacy_sparse"
    repetition_penalty: float = Field(default=0.30, ge=0.0, le=0.6)
    stacking_aversion: Literal["low", "medium", "high"] = "medium"
    observation_turns: int = Field(default=6, ge=3, le=12)
    recency_decay: float = Field(default=0.7, ge=0.4, le=0.9)
    trace: dict[str, SettingTrace] = Field(default_factory=dict)


class FeatureObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature: Literal["meme", "profanity", "innuendo", "cutesy", "strong_marker"]
    value: Literal["present", "absent", "unknown"]
    weighted_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    observed_turns: int = Field(default=0, ge=0)
    unknown_turns: int = Field(default=0, ge=0)
    detector_version: str = "lexical_v1"


class ExpressionObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_turns: int = Field(ge=0)
    features: dict[str, FeatureObservation] = Field(default_factory=dict)
    recent_example_ids: list[str] = Field(default_factory=list)


class PersonaPackageFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    role: str


class PersonaPackageManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_id: str
    schema_version: int = 2
    applicable_period: str = "unspecified"
    applicable_scenes: list[str] = Field(default_factory=lambda: ["default_text_chat"])
    evidence_version: str
    examples_version: str
    default_profile: str = "balanced_candidate"
    compatible_compiler_versions: list[str]
    build_status: Literal["candidate", "validated", "released"] = "candidate"
    parent_package_id: str | None = None
    style_generation: str | None = None
    detector_version: str = "unknown"
    signal_schema_version: int = 1
    style_schema_version: int = 1
    files: list[PersonaPackageFile]

    @model_validator(mode="after")
    def unique_paths(self) -> "PersonaPackageManifest":
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("manifest files contain duplicate paths")
        return self


class PersonaSnapshot(BaseModel):
    version: str
    package_id: str = "legacy"
    schema_version: int = 1
    compiler_version: str = "unknown"
    source_sha256: str = ""
    render_sha256: str = ""
    response_mode: str
    core: str
    voice: str
    behavior: str
    boundaries: str
    style_rules: list[str] = Field(default_factory=list)
    relationship_context: str | None = None
    scene_context: str | None = None
    boundary_ids: list[str] = Field(default_factory=list)
    selected_prior_ids: list[str] = Field(default_factory=list)
    settings_sha256: str = ""

    def render_base(self) -> str:
        blocks = [
            f"[CORE PERSONA]\n{self.core}",
            f"[VOICE RULES]\n{self.voice}",
            f"[BEHAVIOR GUIDANCE]\n{self.behavior}",
            f"[BOUNDARIES]\n{self.boundaries}",
        ]
        if self.style_rules:
            blocks.append(
                "[STYLE RULES]\n"
                + "\n".join(f"- {rule}" for rule in self.style_rules)
            )
        return "\n\n".join(blocks)

    def render(self) -> str:
        blocks = [self.render_base()]
        if self.relationship_context:
            blocks.append(f"[RELATIONSHIP STATE]\n{self.relationship_context}")
        if self.scene_context:
            blocks.append(f"[SCENE STATE]\n{self.scene_context}")
        return "\n\n".join(blocks)

    def computed_render_sha256(self) -> str:
        return hashlib.sha256(self.render().encode("utf-8")).hexdigest()
