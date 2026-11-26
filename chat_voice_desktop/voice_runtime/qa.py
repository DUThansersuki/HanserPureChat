from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AudioQAResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    sample_count: int = Field(ge=0)
    peak: float = Field(ge=0.0)
    duration_seconds: float = Field(ge=0.0)
    reasons: list[str] = Field(default_factory=list)


def basic_audio_qa(pcm, sample_rate: int) -> AudioQAResult:
    import numpy as np

    samples = np.asarray(pcm, dtype=np.float32).reshape(-1)
    reasons: list[str] = []
    if samples.size == 0:
        reasons.append("empty_pcm")
    if samples.size and not np.isfinite(samples).all():
        reasons.append("non_finite_pcm")
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if samples.size and peak < 1e-5:
        reasons.append("all_silent_pcm")
    if peak > 1.0:
        reasons.append("pcm_out_of_range")
    duration = samples.size / sample_rate if sample_rate > 0 else 0.0
    if sample_rate <= 0:
        reasons.append("invalid_sample_rate")
    if duration > 120.0:
        reasons.append("audio_duration_exceeds_segment_limit")
    return AudioQAResult(
        status="failed" if reasons else "passed",
        sample_count=int(samples.size),
        peak=peak,
        duration_seconds=duration,
        reasons=reasons,
    )
