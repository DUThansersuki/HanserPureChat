from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    host: str = "127.0.0.1"
    port: int = 8770
    internal_token: str = ""
    data_root: Path = Path("voice_runtime/data")
    voice_profile: Path = Path("voice_runtime/config/voice_profile.candidate.yml")
    rig_profile: Path = Path("voice_runtime/config/rig_profile.candidate.yml")
    load_voice_model: bool = False
    max_pending_jobs: int = 8
    max_text_codepoints: int = 4000
    event_window: int = 256
    max_ready_segments: int = 2
    interactive_deadline_seconds: float = 120.0
    offline_deadline_seconds: float = 900.0

    @classmethod
    def from_environment(cls, root: str | Path | None = None) -> "RuntimeSettings":
        project_root = Path(root or Path(__file__).resolve().parents[1]).resolve()

        def path_value(name: str, default: str) -> Path:
            raw = Path(os.getenv(name, default))
            return (raw if raw.is_absolute() else project_root / raw).resolve()

        return cls(
            host=os.getenv("HANSER_VOICE_HOST", "127.0.0.1"),
            port=int(os.getenv("HANSER_VOICE_PORT", "8770")),
            internal_token=os.getenv("HANSER_VOICE_INTERNAL_TOKEN", ""),
            data_root=path_value("HANSER_VOICE_DATA_ROOT", "voice_runtime/data"),
            voice_profile=path_value(
                "HANSER_VOICE_PROFILE",
                "voice_runtime/config/voice_profile.candidate.yml",
            ),
            rig_profile=path_value(
                "HANSER_RIG_PROFILE",
                "voice_runtime/config/rig_profile.candidate.yml",
            ),
            load_voice_model=os.getenv("HANSER_VOICE_LOAD_MODEL", "false").lower()
            in {"1", "true", "yes"},
            max_pending_jobs=int(os.getenv("HANSER_VOICE_MAX_PENDING_JOBS", "8")),
            max_text_codepoints=int(os.getenv("HANSER_VOICE_MAX_TEXT", "4000")),
            event_window=int(os.getenv("HANSER_VOICE_EVENT_WINDOW", "256")),
            max_ready_segments=int(os.getenv("HANSER_VOICE_MAX_READY_SEGMENTS", "2")),
            interactive_deadline_seconds=float(
                os.getenv("HANSER_VOICE_INTERACTIVE_DEADLINE", "120")
            ),
            offline_deadline_seconds=float(
                os.getenv("HANSER_VOICE_OFFLINE_DEADLINE", "900")
            ),
        )
