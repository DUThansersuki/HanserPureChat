from __future__ import annotations

from typing import Any

import numpy as np

from ..profiles import VoiceProfile
from .base import SynthesisRequest, SynthesisResult


class VoxCPM2HybridBackend:
    """VoxCPM2 with the main model on CUDA and AudioVAE on CPU."""

    adapter_revision = "voxcpm2_hybrid_vae_cpu_1"

    def __init__(self, profile: VoiceProfile, model: Any):
        if profile.backend != "voxcpm2_hybrid" or profile.voxcpm2 is None:
            raise ValueError("VoxCPM2 hybrid profile is required")
        self.profile = profile
        self.settings = profile.voxcpm2
        self.model_revision = str(profile.model.revision)
        self._model = model

    @classmethod
    def load(cls, profile: VoiceProfile) -> "VoxCPM2HybridBackend":
        import torch
        from voxcpm import VoxCPM

        model = VoxCPM.from_pretrained(
            str(profile.model.local_path),
            load_denoiser=profile.inference.load_denoiser,
            local_files_only=profile.voxcpm2.local_files_only,
            optimize=profile.voxcpm2.optimize,
            device=profile.voxcpm2.model_device,
        )
        vae = model.tts_model.audio_vae.to(profile.voxcpm2.audio_vae_device)
        original_encode = vae.encode
        original_decode = vae.decode
        vae.encode = lambda audio, sample_rate: original_encode(
            audio.to(profile.voxcpm2.audio_vae_device), sample_rate
        )
        vae.decode = lambda latent: original_decode(
            latent.to(profile.voxcpm2.audio_vae_device)
        )
        torch.cuda.empty_cache()
        return cls(profile, model)

    def close(self) -> None:
        self._model = None

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        prompt = request.voice_plan.style_prompt
        if prompt is None or not prompt.transcript:
            raise ValueError("VoxCPM2 ref-continuation requires an exact prompt transcript")

        samples = self._model.generate(
            text=request.engine_text.engine_text,
            prompt_wav_path=prompt.path,
            prompt_text=prompt.transcript,
            reference_wav_path=request.voice_plan.identity_reference.path,
            cfg_value=request.cfg_value,
            inference_timesteps=request.inference_timesteps,
            normalize=self.profile.inference.normalize,
            denoise=False,
            retry_badcase=self.profile.inference.retry_badcase,
            seed=request.seed,
        )
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        sample_rate = int(self._model.tts_model.sample_rate)
        if sample_rate != self.profile.output.package_sample_rate:
            raise ValueError(f"VoxCPM2 returned unexpected sample rate: {sample_rate}")
        if samples.size == 0 or not np.isfinite(samples).all():
            raise ValueError("VoxCPM2 returned invalid audio samples")

        return SynthesisResult(
            pcm=samples,
            sample_rate=sample_rate,
            backend_metadata={
                "backend": "voxcpm2_hybrid",
                "adapter_revision": self.adapter_revision,
                "model_id": self.profile.model.id,
                "model_revision": self.model_revision,
                "package_version": self.profile.model.package_version,
                "model_device": self.settings.model_device,
                "audio_vae_device": self.settings.audio_vae_device,
                "seed": request.seed,
            },
        )
