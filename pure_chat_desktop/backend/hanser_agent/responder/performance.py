from __future__ import annotations

import json
import math
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


PERFORMANCE_SCHEMA_VERSION = "1.1"


class Delivery(StrEnum):
    NEUTRAL = "neutral"
    GENTLE = "gentle"
    CONCERNED = "concerned"
    SERIOUS = "serious"
    SOFT_SURPRISED = "soft_surprised"
    EXCITED = "excited"
    AMUSED = "amused"
    TEASING = "teasing"
    DEADPAN = "deadpan"
    ANNOYED_SOFT = "annoyed_soft"
    ANNOYED_PLAYFUL = "annoyed_playful"
    EMBARRASSED = "embarrassed"
    HESITANT = "hesitant"


class OutputPreferences(BaseModel):
    """Consumer choices frozen at the chat request boundary."""

    model_config = ConfigDict(extra="forbid")

    text: bool = True
    speech: bool = False
    dynamic_live2d: bool = False
    offline_performance: bool = False

    @model_validator(mode="after")
    def text_is_mandatory(self) -> "OutputPreferences":
        if not self.text:
            raise ValueError("text output cannot be disabled")
        return self

    def requests_performance(self) -> bool:
        return self.speech or self.dynamic_live2d or self.offline_performance


class PerformanceIntent(BaseModel):
    """The one optional delivery decision emitted with the response text."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"] = PERFORMANCE_SCHEMA_VERSION
    delivery: Delivery
    intensity: float = Field(ge=0.0, le=1.0)


class PerformanceParseResult(BaseModel):
    intent: PerformanceIntent | None = None
    degraded_reasons: list[str] = Field(default_factory=list)
    ignored_fields: list[str] = Field(default_factory=list)


class PerformanceConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"] = PERFORMANCE_SCHEMA_VERSION
    forbidden_delivery: list[Delivery] = Field(default_factory=list)
    max_intensity_by_delivery: dict[Delivery, float] = Field(default_factory=dict)
    forbidden_features: list[str] = Field(default_factory=list)
    constraint_refs: list[str] = Field(default_factory=list)


class AllowedPerformance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"] = PERFORMANCE_SCHEMA_VERSION
    delivery: Delivery = Delivery.NEUTRAL
    intensity: float = Field(default=0.2, ge=0.0, le=1.0)
    forbidden_features: list[str] = Field(default_factory=list)
    constraints_ref: str = "constraints:legacy-neutral"
    decisions: list[str] = Field(default_factory=list)


class ParsedResponderPayload(BaseModel):
    semantic_text: str
    raw_performance: object | None = None
    ignored_fields: list[str] = Field(default_factory=list)


class SpeechTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"] = PERFORMANCE_SCHEMA_VERSION
    status: Literal["eligible", "unavailable", "no_speakable_content"]
    reason: str | None = Field(default=None, exclude_if=lambda value: value is None)


class ReplySnapshot(BaseModel):
    """Canonical committed reply consumed by rendering services."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"] = PERFORMANCE_SCHEMA_VERSION
    request_id: str
    reply_id: str
    user_id: str
    conversation_id: str
    semantic_text: str
    display_text: str
    performance: PerformanceIntent | None = None
    allowed_performance: AllowedPerformance
    output_preferences: OutputPreferences
    allow_tts: bool
    language: str
    constraints_ref: str
    render_profile_revision: str
    text_source: Literal["validated_semantic", "legacy_text_source"]
    persona_trace: dict[str, object] = Field(default_factory=dict)


def parse_responder_payload(raw_text: str) -> ParsedResponderPayload:
    """Parse only the strict top-level text; keep performance field-tolerant."""

    value = json.loads(raw_text)
    if not isinstance(value, dict):
        raise ValueError("structured responder output must be a JSON object")
    semantic_text = value.get("semantic_text")
    if not isinstance(semantic_text, str) or not semantic_text.strip():
        raise ValueError("structured responder output requires non-empty semantic_text")
    ignored = sorted(str(key) for key in value if key not in {"semantic_text", "performance"})
    return ParsedResponderPayload(
        semantic_text=semantic_text,
        raw_performance=value.get("performance"),
        ignored_fields=ignored,
    )


def parse_performance(raw: object | None) -> PerformanceParseResult:
    """Degrade a malformed sidecar without invalidating valid response language."""

    if raw is None:
        return PerformanceParseResult(degraded_reasons=["performance_missing"])
    if not isinstance(raw, dict):
        return PerformanceParseResult(degraded_reasons=["performance_not_object"])

    ignored = sorted(
        str(key) for key in raw if key not in {"schema_version", "delivery", "intensity"}
    )
    reasons = [f"performance_ignored_field:{key}" for key in ignored]
    version = raw.get("schema_version")
    if version != PERFORMANCE_SCHEMA_VERSION:
        reasons.append("performance_schema_unsupported")
        return PerformanceParseResult(
            degraded_reasons=reasons,
            ignored_fields=ignored,
        )

    delivery_value = raw.get("delivery")
    try:
        delivery = Delivery(delivery_value) if isinstance(delivery_value, str) else Delivery.NEUTRAL
    except ValueError:
        delivery = Delivery.NEUTRAL
    if delivery_value != delivery.value:
        reasons.append("performance_delivery_invalid")

    intensity_value = raw.get("intensity")
    if (
        isinstance(intensity_value, bool)
        or not isinstance(intensity_value, (int, float))
        or not math.isfinite(float(intensity_value))
        or not 0.0 <= float(intensity_value) <= 1.0
    ):
        intensity = 0.2
        reasons.append("performance_intensity_invalid")
    else:
        intensity = float(intensity_value)

    return PerformanceParseResult(
        intent=PerformanceIntent(delivery=delivery, intensity=intensity),
        degraded_reasons=reasons,
        ignored_fields=ignored,
    )


def effective_preferences(
    requested: OutputPreferences,
    *,
    structured_enabled: bool,
    speech_enabled: bool,
    dynamic_live2d_enabled: bool,
    offline_export_enabled: bool,
) -> OutputPreferences:
    """Resolve product feature switches once at the request boundary."""

    return OutputPreferences(
        text=True,
        speech=requested.speech and speech_enabled,
        dynamic_live2d=requested.dynamic_live2d and dynamic_live2d_enabled,
        offline_performance=(
            requested.offline_performance and offline_export_enabled
        ),
    ) if structured_enabled else OutputPreferences()


def json_identity(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
