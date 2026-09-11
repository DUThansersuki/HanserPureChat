from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SCHEMA_VERSION = "1.1"


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
    model_config = ConfigDict(extra="forbid")

    text: bool = True
    speech: bool = False
    dynamic_live2d: bool = False
    offline_performance: bool = False


class PerformanceIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery: Delivery = Delivery.NEUTRAL
    intensity: float = Field(default=0.2, ge=0.0, le=1.0)


class AllowedPerformance(PerformanceIntent):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    forbidden_features: list[str] = Field(default_factory=list)
    constraints_ref: str
    decisions: list[str] = Field(default_factory=list)


class ReplySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"] = SCHEMA_VERSION
    request_id: str
    reply_id: str
    user_id: str
    conversation_id: str
    semantic_text: str
    display_text: str
    performance: dict[str, object] | None = None
    allowed_performance: AllowedPerformance
    output_preferences: OutputPreferences
    allow_tts: bool
    language: str
    constraints_ref: str
    render_profile_revision: str
    text_source: Literal["validated_semantic", "legacy_text_source"]


class SourceMapEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_span: tuple[int, int]
    speech_span: tuple[int, int]
    operation: Literal["copy", "replace", "omit"]
    precision: Literal["exact", "rule_based", "best_effort"]
    rule_id: str
    rule_version: str


class EngineMapEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speech_span: tuple[int, int]
    engine_span: tuple[int, int]
    operation: Literal["copy", "replace", "omit"] = "copy"
    precision: Literal["exact", "rule_based", "best_effort"] = "exact"


class SegmentFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codepoint_length: int = Field(ge=0)
    utterance_type: Literal["question", "statement", "mixed"]
    rhythm_type: str = "unknown"


class SpeechSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"] = SCHEMA_VERSION
    reply_id: str
    segment_id: str
    index: int = Field(ge=0)
    semantic_spans: list[tuple[int, int]]
    speech_span: tuple[int, int]
    speech_text: str
    language: str
    allowed_performance: PerformanceIntent
    features: SegmentFeatures
    target_gap_ms: int = Field(default=180, ge=0, le=5000)
    mapping_revision: str


class SpeechPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"] = SCHEMA_VERSION
    reply_id: str
    semantic_text: str
    speech_text: str
    source_map: list[SourceMapEntry]
    segments: list[SpeechSegment]
    display_only_spans: list[tuple[int, int]] = Field(default_factory=list)
    degraded_reasons: list[str] = Field(default_factory=list)
    mapping_revision: str


class VoiceAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    asset_revision: str
    speaker_id: str
    kind: Literal["identity_reference", "style_prompt"]
    path: str
    source_path: str | None = None
    source_sha256: str | None = None
    transcript: str | None = None
    review_status: Literal["candidate", "approved", "rejected"]
    approved_languages: list[str] = Field(default_factory=list)
    family: str = "neutral"
    intensity_band: str = "low"
    feature_tags: list[str] = Field(default_factory=list)
    preferred_length_range: tuple[int, int] | None = None
    utterance_types: list[str] = Field(default_factory=list)
    rhythm_tags: list[str] = Field(default_factory=list)


class VoicePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clone_mode: Literal["reference", "ref_continuation"]
    identity_reference: VoiceAsset
    style_prompt: VoiceAsset | None = None
    control_text: str | None = None
    family: str
    intensity_band: str
    voice_intensity_mode: Literal["discrete", "not_controllable"]
    degraded_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def prompt_and_control_are_exclusive(self) -> "VoicePlan":
        if self.style_prompt is not None and self.control_text is not None:
            raise ValueError("style prompt and control text cannot be combined")
        return self


class EngineTextPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"] = SCHEMA_VERSION
    speech_text: str
    engine_text: str
    speech_to_engine: list[EngineMapEntry]
    control_spans: list[tuple[int, int]] = Field(default_factory=list)
    spoken_engine_spans: list[tuple[int, int]]
    adapter_revision: str
    internal_transform: Literal["unverified"] = "unverified"


class AudioArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    relative_path: str
    sample_rate: int = Field(gt=0)
    channels: Literal[1]
    sample_count: int = Field(gt=0)
    native_sample_rate: int = Field(gt=0)
    qa_status: Literal["passed", "failed"]


class AmplitudeCue(BaseModel):
    start_sample: int = Field(ge=0)
    end_sample: int = Field(gt=0)
    value: float = Field(ge=0.0, le=1.0)


class Timeline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    revision: str
    clock: Literal["audio"] = "audio"
    sample_rate: int = Field(gt=0)
    pause_after_samples: int = Field(ge=0)
    mouth_precision: Literal["amplitude", "rhubarb", "closed"]
    subtitle_precision: Literal["segment", "word", "character"] = "segment"
    mouth: list[AmplitudeCue] = Field(default_factory=list)
    expression: dict[str, object] = Field(default_factory=dict)


class SegmentPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"] = SCHEMA_VERSION
    job_id: str
    reply_id: str
    rendition_id: str
    segment_id: str
    index: int
    audio: AudioArtifact
    segment_duration_samples: int
    timeline: Timeline
    qa: dict[str, str]
    degraded_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def duration_is_audio_plus_gap(self) -> "SegmentPackage":
        expected = self.audio.sample_count + self.timeline.pause_after_samples
        if self.segment_duration_samples != expected:
            raise ValueError("segment duration must equal audio samples plus planned gap")
        return self


class AudioTurnPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"] = SCHEMA_VERSION
    job_id: str
    reply_id: str
    rendition_id: str
    status: Literal["completed", "failed", "cancelled"]
    partial: bool = False
    segments: list[SegmentPackage]
    total_duration_samples: int = Field(ge=0)
    timeline_sample_rate: int = Field(default=48000, gt=0)
    source_map: list[SourceMapEntry]
    requested: dict[str, object] | None = None
    allowed: AllowedPerformance
    voice_resolved: list[dict[str, object]] = Field(default_factory=list)
    visual_resolved: dict[str, object] | None = None
    profile_revision: str
    model_revision: str | None = None
    compiler_revision: str


class VisualTurnPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"] = SCHEMA_VERSION
    plan_id: str
    reply_id: str
    epoch: int = Field(ge=0)
    clock: Literal["presentation"] = "presentation"
    audio_present: Literal[False] = False
    preset: str
    weight: float = Field(ge=0.0, le=1.0)
    motion: str = "none"
    attack_ms: int = Field(ge=0)
    hold_ms: int = Field(ge=0)
    release_ms: int = Field(ge=0)
    rig_revision: str
    degraded_reasons: list[str] = Field(default_factory=list)


class VisualPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: str
    snapshot: ReplySnapshot
    epoch: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def owner_matches_snapshot(self) -> "VisualPlanRequest":
        if self.owner != self.snapshot.user_id:
            raise ValueError("visual plan owner must match reply snapshot owner")
        if not (
            self.snapshot.output_preferences.dynamic_live2d
            or self.snapshot.output_preferences.offline_performance
        ):
            raise ValueError("reply snapshot has no visual consumer")
        return self


class VoiceJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: str
    snapshot: ReplySnapshot
    mode: Literal["interactive", "offline"] = "interactive"
    rendition_id: str = "default"
    variant_salt: str | None = None

    @model_validator(mode="after")
    def owner_matches_snapshot(self) -> "VoiceJobRequest":
        if self.owner != self.snapshot.user_id:
            raise ValueError("job owner must match reply snapshot owner")
        if not self.snapshot.allow_tts or not self.snapshot.output_preferences.speech:
            raise ValueError("reply snapshot is not eligible for speech")
        return self


class JobStatus(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    GENERATING = "generating"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class JobEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"] = SCHEMA_VERSION
    job_id: str
    reply_id: str
    rendition_id: str
    sequence: int = Field(ge=1)
    type: Literal[
        "turn.started",
        "segment.ready",
        "turn.completed",
        "turn.failed",
        "turn.cancelled",
    ]
    segment: SegmentPackage | None = None
    reason: str | None = None
    partial: bool = False


class JobSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"] = SCHEMA_VERSION
    job_id: str
    reply_id: str
    rendition_id: str
    owner: str
    status: JobStatus
    mode: Literal["interactive", "offline"]
    last_sequence: int = Field(default=0, ge=0)
    ready_segments: list[SegmentPackage] = Field(default_factory=list)
    partial: bool = False
    terminal_reason: str | None = None


class PlaybackReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    playback_id: str
    epoch: int = Field(ge=0)
    event_id: str
    status: Literal["started", "progress", "paused", "completed", "interrupted"]
    segment_id: str
    phase: Literal["audio", "gap"]
    sample_offset: int = Field(ge=0)
