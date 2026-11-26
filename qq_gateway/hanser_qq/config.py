from __future__ import annotations

from pathlib import Path
from typing import Literal
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class SegmentedReplySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    split_short_clauses: bool = True
    strict_short_bubbles: bool = True
    min_reply_chars: int = Field(default=40, ge=1)
    min_segment_chars: int = Field(default=12, ge=1)
    target_chars: int = Field(default=36, ge=1)
    max_chars: int = Field(default=60, ge=1)
    max_segments: int = Field(default=4, ge=1, le=8)
    min_interval_seconds: float = Field(default=0.6, ge=0, le=10)
    max_interval_seconds: float = Field(default=1.5, ge=0, le=10)
    seconds_per_char: float = Field(default=0.025, ge=0, le=1)

    @model_validator(mode="after")
    def check_ranges(self):
        if not self.min_segment_chars <= self.target_chars <= self.max_chars:
            raise ValueError("segment lengths must satisfy min_segment_chars <= target_chars <= max_chars")
        if self.min_interval_seconds > self.max_interval_seconds:
            raise ValueError("segment interval minimum must not exceed maximum")
        return self


class ProactiveSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    idle_seconds: float = Field(default=14400, ge=1)
    jitter_seconds: float = Field(default=1800, ge=0)
    daily_limit: int = Field(default=1, ge=1, le=5)
    start_hour: float = Field(default=9, ge=0, lt=24)
    end_hour: float = Field(default=22.5, gt=0, le=24)
    poll_seconds: float = Field(default=60, ge=0.1)
    expiry_seconds: float = Field(default=600, ge=1)
    context_hours: float = Field(default=72, ge=1, le=168)

    @model_validator(mode="after")
    def check_hours(self):
        if self.start_hour >= self.end_hour:
            raise ValueError("proactive start_hour must precede end_hour")
        return self


class VoiceReplySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    probability: float = Field(default=0.2, ge=0, le=1)
    timeout_seconds: float = Field(default=180, gt=0, le=600)
    min_pause_ms: int = Field(default=160, ge=0, le=2000)
    comma_pause_min_ms: int = Field(default=160, ge=0, le=1000)
    comma_pause_max_ms: int = Field(default=220, ge=0, le=1000)
    output_gain_db: float = Field(default=6.0, ge=0, le=12)

    @model_validator(mode="after")
    def check_pause_range(self):
        if self.comma_pause_min_ms > self.comma_pause_max_ms:
            raise ValueError("comma pause minimum must not exceed maximum")
        return self


class VoiceRuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    port: int = Field(default=18771, ge=1, le=65535)
    token: str = Field(default="", repr=False)
    python_path: Path = Path("../voice_runtime/.venv/Scripts/python.exe")
    package_path: Path = Path(".runtime/voice")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


class ResponderGatewaySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    # This is an explicit local-gateway boundary, not a general localhost model exception.
    endpoint: Literal["http://127.0.0.1:3000/v1"] = "http://127.0.0.1:3000/v1"
    model: Literal["auto-code"] = "auto-code"
    api_key_file: Path = Path("C:/AI-Gateway/new-api/local-agent.token")


class RagSelectorSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = False
    endpoint: Literal['http://127.0.0.1:3000/v1'] = 'http://127.0.0.1:3000/v1'
    model: Literal['rag-selector'] = 'rag-selector'
    api_key_file: Path = Path('C:/AI-Gateway/new-api/local-agent.token')
    timeout_seconds: float = Field(default=120, gt=0, le=300)


class GatewaySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = Field(default=8781, ge=1, le=65535)
    onebot_access_token: str = Field(min_length=1, repr=False)
    admin_token: str = Field(min_length=1, repr=False)
    bot_ids: list[str] = Field(default_factory=list)
    private_enabled: bool = True
    private_users: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    debounce_seconds: float = Field(default=3, ge=0, le=30)
    max_wait_seconds: float = Field(default=6, gt=0, le=60)
    max_batch_messages: int = Field(default=12, ge=1, le=30)
    max_message_chars: int = Field(default=8000, ge=1, le=32000)
    backend_timeout_seconds: float = Field(default=420, gt=0)
    backend_attempts: int = Field(default=3, ge=1, le=5)
    segmented_reply: SegmentedReplySettings = Field(default_factory=SegmentedReplySettings)
    speech_habits_enabled: bool = False
    expression_polish_enabled: bool = False
    proactive: ProactiveSettings = Field(default_factory=ProactiveSettings)
    voice_reply: VoiceReplySettings = Field(default_factory=VoiceReplySettings)

    @model_validator(mode="after")
    def check_window(self):
        if self.max_wait_seconds < self.debounce_seconds:
            raise ValueError("max_wait_seconds must be >= debounce_seconds")
        for identifier in [*self.bot_ids, *self.private_users, *self.groups]:
            if not identifier.isdigit():
                raise ValueError("QQ and group identifiers must be digit strings")
        return self


class BackendSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = Field(default=8782, ge=1, le=65535)
    token: str = Field(min_length=1, repr=False)
    package_path: Path = Path(".runtime/backend")
    config_path: Path = Path("hanser.yml")

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    gateway: GatewaySettings
    backend: BackendSettings
    voice_runtime: VoiceRuntimeSettings = Field(default_factory=VoiceRuntimeSettings)
    responder_gateway: ResponderGatewaySettings = Field(default_factory=ResponderGatewaySettings)
    rag_selector: RagSelectorSettings = Field(default_factory=RagSelectorSettings)
    storage_path: Path = Path(".runtime/gateway.sqlite3")

    @model_validator(mode="after")
    def distinct_boundaries(self):
        if self.gateway.port == self.backend.port:
            raise ValueError("gateway and backend ports must differ")
        if self.voice_runtime.port in {self.gateway.port, self.backend.port}:
            raise ValueError("voice runtime port must differ from gateway/backend")
        if len({self.gateway.onebot_access_token, self.gateway.admin_token, self.backend.token}) != 3:
            raise ValueError("OneBot, admin and backend credentials must differ")
        return self


def load_settings(path: Path | str) -> Settings:
    path = Path(path).resolve()
    settings = Settings.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    for owner, name in [(settings, "storage_path"), (settings.backend, "package_path"),
                        (settings.backend, "config_path"), (settings.voice_runtime, "python_path"),
                        (settings.voice_runtime, "package_path"), (settings.responder_gateway, "api_key_file"),
                        (settings.rag_selector, 'api_key_file')]:
        value = getattr(owner, name)
        setattr(owner, name, (path.parent / value).resolve())
    return settings
