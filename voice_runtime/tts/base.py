from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..contracts import EngineTextPlan, VoicePlan


@dataclass(frozen=True, slots=True)
class SynthesisRequest:
    engine_text: EngineTextPlan
    voice_plan: VoicePlan
    seed: int
    cfg_value: float
    inference_timesteps: int


@dataclass(frozen=True, slots=True)
class SynthesisResult:
    pcm: Any
    sample_rate: int
    backend_metadata: dict[str, object]


class TTSBackend(Protocol):
    model_revision: str

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        ...
