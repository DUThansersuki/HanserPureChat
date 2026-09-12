from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

class DialoguePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal[
        "chitchat",
        "wiki_fact",
        "followup_fact",
        "user_memory",
        "relationship",
        "mixed",
        "unknown",
    ]

    need_wiki: bool

    need_memory: bool = True

    need_style_examples: bool = True

    memory_query: str | None = None

    style_query: str | None = None

    standalone_query: str = Field(min_length=1)

    keywords: list[str] = Field(
        max_length=5,
    )

    response_mode: Literal[
        "casual",
        "factual",
        "emotional",
        "playful",
        "storytelling",
    ]

    fact_sensitivity: Literal[
        "low",
        "medium",
        "high",
    ]

    target_length: Literal[
        "short",
        "medium",
        "long",
    ]

    # Optional, field-tolerant semantic observations from the same Planner call.
    # Values are parsed independently by persona.signals; keeping this raw here
    # prevents one malformed optional signal from invalidating fact routing.
    persona_signals: dict[str, object] = Field(default_factory=dict)

    # Runtime provenance. The planner output is never trusted for this field;
    # DialoguePlanner overwrites it after model/fallback handling.
    degraded_reasons: list[str] = Field(default_factory=list)


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str
    message_id: str | None = Field(default=None, exclude=True)
    created_at: datetime | None = Field(default=None, exclude=True)
    style_example_ids: list[str] = Field(default_factory=list, exclude=True)


class SearchResult(BaseModel):
    id: int
    filename: str
    filepath: str
    hits: int
    matched: list[str] = Field(default_factory=list)
    snippet: str = ""
    score: float = 0.0


class WikiEvidence(BaseModel):
    source_id: str
    document_id: int
    chunk_id: int | None = None
    chunk_index: int | None = None
    filename: str
    text: str
    retrieval_score: float
    rerank_score: float | None = None
    source_type: Literal["wiki", "transcript", "document"] = "document"
    source_date: str | None = None


class StyleExample(BaseModel):
    id: str
    user_context: str
    character_response: str
    context_before: str = ""
    context_after: str = ""
    scene: str
    speech_act: str
    tone: list[str] = Field(default_factory=list)
    relationship_level: str | None = None
    energy: float | None = None
    teasing_level: float | None = None
    answer_length: Literal["short", "medium", "long"]
    response_mode: Literal[
        "casual",
        "factual",
        "emotional",
        "playful",
        "storytelling",
    ]
    source_type: Literal["real", "synthetic"]
    source_ref: str | None = None
    authenticity_score: float
    quality_score: float
    review_status: Literal["pending", "approved", "rejected", "quarantined"] = "pending"
    source_tier: Literal["primary", "secondary", "synthetic", "unknown"] = "unknown"
    source_document_id: int | None = None
    source_start_line: int | None = None
    source_end_line: int | None = None
    source_start_char: int | None = None
    source_end_char: int | None = None
    source_speaker: str | None = None
    source_user_turn: str | None = None
    source_response_turn: str | None = None
    cleaning_operations: list[str] = Field(default_factory=list)
    reviewer_id: str | None = None
    review_notes: str | None = None
    index_generation: str | None = None
    provenance_kind: Literal[
        "verbatim", "adapted", "designed", "generated", "legacy_unknown"
    ] = "legacy_unknown"
    payload_class: Literal[
        "reaction_only",
        "turn_local_stance",
        "past_or_current_autobiography",
        "third_party_fact",
        "fiction_or_quote",
        "unreviewed",
    ] = "unreviewed"
    schema_review_status: Literal["pending", "approved", "rejected"] = "pending"
    behavior_tags: list[str] = Field(default_factory=list)
    expression_tags: list[str] = Field(default_factory=list)
    audience: Literal["individual", "audience", "mixed", "unknown"] = "unknown"
    group_id: str | None = None
    speaker_status: Literal[
        "audio_verified",
        "transcript_verified",
        "unverified",
        "not_applicable",
    ] = "unverified"
    runtime_scope: list[str] = Field(default_factory=list)
    hidden_eval: bool = False


MemoryType = Literal[
    "user_fact",
    "user_preference",
    "shared_event",
    "relationship_event",
    "promise",
    "shared_joke",
    "episode",
    "unresolved_thread",
]

MemoryStatus = Literal["active", "superseded", "deleted", "closed"]

MemoryAssertionType = Literal[
    "user_assertion",
    "hypothetical",
    "question",
    "negated_assertion",
    "correction",
    "confirmed_conversation_event",
    "user_instruction",
]
MemoryPolarity = Literal["positive", "negative", "neutral"]
MemoryValidity = Literal["asserted", "verified", "unverified", "rejected"]
MemorySourceKind = Literal["user_message", "human_edit", "legacy_import"]
AddressKind = Literal["personal_name", "nickname", "fan_identity"]
MemoryScope = Literal["global", "conversation", "turn"]


class MemoryItem(BaseModel):
    id: str
    user_id: str
    conversation_id: str | None = None
    type: MemoryType
    memory_key: str = ""
    content: str
    importance: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    source_message_ids: list[str] = Field(default_factory=list)
    assertion_type: MemoryAssertionType = "user_assertion"
    polarity: MemoryPolarity = "neutral"
    validity: MemoryValidity = "asserted"
    subject: str | None = None
    predicate: str | None = None
    object_value: str | None = None
    revision_of_id: str | None = None
    source_kind: MemorySourceKind = "user_message"
    memory_scope: MemoryScope = "global"
    address_kind: AddressKind | None = None
    context_tags: list[str] = Field(default_factory=list)
    address_priority: float = Field(default=0.0, ge=0.0, le=1.0)
    created_at: datetime
    last_accessed_at: datetime | None = None
    status: MemoryStatus = "active"


class MemoryCandidate(BaseModel):
    user_id: str
    conversation_id: str
    type: MemoryType
    memory_key: str
    content: str
    importance: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    source_message_ids: list[str] = Field(min_length=1)
    assertion_type: MemoryAssertionType = "user_assertion"
    polarity: MemoryPolarity = "neutral"
    validity: MemoryValidity = "asserted"
    subject: str | None = None
    predicate: str | None = None
    object_value: str | None = None
    source_kind: MemorySourceKind = "user_message"
    memory_scope: MemoryScope = "global"
    address_kind: AddressKind | None = None
    context_tags: list[str] = Field(default_factory=list)
    address_priority: float = Field(default=0.0, ge=0.0, le=1.0)


class MemoryDecision(BaseModel):
    candidate: MemoryCandidate
    accepted: bool
    reason: str


class ConversationSummary(BaseModel):
    conversation_id: str
    content: str
    through_message_index: int
    updated_at: datetime


class MemoryPatch(BaseModel):
    content: str | None = None
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def require_change(self) -> "MemoryPatch":
        if not self.model_fields_set or all(
            getattr(self, field_name) is None
            for field_name in ("content", "importance", "confidence")
        ):
            raise ValueError("至少提供一个需要修改的字段")
        return self


class RelationshipState(BaseModel):
    familiarity: float = Field(default=0.1, ge=0.0, le=1.0)
    warmth: float = Field(default=0.5, ge=0.0, le=1.0)
    trust: float = Field(default=0.4, ge=0.0, le=1.0)
    teasing_permission: float = Field(default=0.2, ge=0.0, le=1.0)
    shared_context_density: float = Field(default=0.0, ge=0.0, le=1.0)
    recent_tension: float = Field(default=0.0, ge=0.0, le=1.0)


class SceneState(BaseModel):
    current_topic: str | None = None
    mood: str = "neutral"
    energy: float = Field(default=0.5, ge=0.0, le=1.0)
    response_tempo: str = "normal"
    emotional_context: str | None = None
    unresolved_threads: list[str] = Field(default_factory=list)


from .responder.performance import OutputPreferences, SpeechTicket


class PersonaRequestSettings(BaseModel):
    """Explicit user controls supplied by a trusted chat client on every request."""

    model_config = ConfigDict(extra="forbid")

    adult_innuendo_opt_in: bool = False


class ChatRequest(BaseModel):
    conversation_id: str = "default"
    user_id: str = "local-user"
    message: str
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    persona_settings: PersonaRequestSettings = Field(
        default_factory=PersonaRequestSettings
    )
    output_preferences: OutputPreferences = Field(default_factory=OutputPreferences)
    render_profile_revision: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )


class ChatResponse(BaseModel):
    text: str
    keywords: list[str] = Field(default_factory=list)
    anchored: list[str] = Field(default_factory=list)
    sources: list[SearchResult] = Field(default_factory=list)
    request_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    reply_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    speech: SpeechTicket | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    trace_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    status: Literal["ok", "degraded"] = Field(
        default="ok", exclude_if=lambda value: value == "ok"
    )
    degraded_reasons: list[str] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )
    post_turn_status: Literal["completed", "pending_retry"] = Field(
        default="completed", exclude_if=lambda value: value == "completed"
    )
    post_turn_retry_id: str | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class VoiceJobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply_id: str = Field(min_length=1, max_length=128)
    mode: Literal["interactive", "offline"] = "interactive"
    rendition_id: str = Field(default="default", min_length=1, max_length=128)
    variant_salt: str | None = Field(default=None, max_length=128)
    user_id: str = "local-user"


class VisualPlanCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply_id: str = Field(min_length=1, max_length=128)
    epoch: int = Field(default=0, ge=0)
    user_id: str = "local-user"


@dataclass(slots=True)
class AgentRunContext:
    conversation_id: str
    user_message: str
    keywords: list[str] = field(default_factory=list)
    search_results: list[SearchResult] = field(default_factory=list)
    anchored: list[str] = field(default_factory=list)
