from __future__ import annotations

from typing import Any

from ..profiles import VoiceProfile
from .base import SynthesisRequest, SynthesisResult


class VoxCPM2Backend:
    """Public VoxCPM API adapter. Construction never loads a model implicitly."""

    def __init__(self, model: Any, profile: VoiceProfile):
        if not profile.enabled:
            raise ValueError("voice profile is not enabled")
        self.model = model
        self.profile = profile
        self.model_revision = str(profile.model.revision)

    @classmethod
    def load(cls, profile: VoiceProfile) -> "VoxCPM2Backend":
        """Explicit startup hook; this is the only model-loading boundary."""

        if not profile.enabled:
            raise ValueError("voice profile is not enabled")
        from voxcpm import VoxCPM

        source = profile.model.local_path or profile.model.id
        model = VoxCPM.from_pretrained(
            source,
            load_denoiser=profile.inference.load_denoiser,
        )
        return cls(model, profile)

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        plan = request.voice_plan
        kwargs: dict[str, object] = {
            "text": request.engine_text.engine_text,
            "reference_wav_path": plan.identity_reference.path,
            "cfg_value": request.cfg_value,
            "inference_timesteps": request.inference_timesteps,
            "normalize": False,
            "retry_badcase": False,
            "seed": request.seed,
        }
        if plan.style_prompt is not None:
            kwargs["prompt_wav_path"] = plan.style_prompt.path
            kwargs["prompt_text"] = plan.style_prompt.transcript
        waveform = self.model.generate(**kwargs)
        sample_rate = int(self.model.tts_model.sample_rate)
        return SynthesisResult(
            pcm=waveform,
            sample_rate=sample_rate,
            backend_metadata={
                "backend": "voxcpm2",
                "model_id": self.profile.model.id,
                "model_revision": self.model_revision,
                "package_version": self.profile.model.package_version,
                "api_mode": "public",
                "normalize": False,
                "retry_badcase": False,
                "seed": request.seed,
            },
        )
