from __future__ import annotations

from io import BytesIO

import httpx
import numpy as np
import soundfile as sf
import soxr

from ..profiles import VoiceProfile
from .base import SynthesisRequest, SynthesisResult


class GPTSoVITSV2ProBackend:
    """Loopback client for the pinned GPT-SoVITS v2Pro sidecar."""

    adapter_revision = "gpt_sovits_v2pro_http_1"

    def __init__(
        self,
        profile: VoiceProfile,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        if not profile.enabled:
            raise ValueError("voice profile is not enabled")
        if profile.backend != "gpt_sovits_v2pro" or profile.gpt_sovits is None:
            raise ValueError("GPT-SoVITS v2Pro profile is required")
        self.profile = profile
        self.settings = profile.gpt_sovits
        self.model_revision = str(profile.model.revision)
        self._client = httpx.Client(
            base_url=self.settings.endpoint,
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    @classmethod
    def load(cls, profile: VoiceProfile) -> "GPTSoVITSV2ProBackend":
        # The model is owned by the separately started Python 3.9 sidecar.
        # Construction intentionally performs no model load or synthesis.
        return cls(profile)

    def close(self) -> None:
        self._client.close()

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        prompt = request.voice_plan.style_prompt
        if prompt is None or not prompt.transcript:
            raise ValueError("GPT-SoVITS v2Pro requires an exact prompt transcript")

        response = self._client.post(
            "/tts",
            json={
                "text": request.engine_text.engine_text,
                "text_lang": self.settings.text_language,
                "ref_audio_path": prompt.path,
                "prompt_text": prompt.transcript,
                "prompt_lang": self.settings.prompt_language,
                "top_k": self.settings.top_k,
                "top_p": self.settings.top_p,
                "temperature": self.settings.temperature,
                "text_split_method": self.settings.text_split_method,
                "batch_size": self.settings.batch_size,
                "split_bucket": self.settings.split_bucket,
                "speed_factor": self.settings.speed_factor,
                "seed": request.seed,
                "media_type": "wav",
                "streaming_mode": 0,
                "parallel_infer": self.settings.parallel_infer,
                "repetition_penalty": self.settings.repetition_penalty,
            },
        )
        if response.status_code != 200:
            detail = response.text.replace("\r", " ").replace("\n", " ")[:240]
            raise RuntimeError(
                f"GPT-SoVITS sidecar returned HTTP {response.status_code}: {detail}"
            )

        try:
            pcm, native_sample_rate = sf.read(
                BytesIO(response.content), dtype="float32", always_2d=True
            )
        except Exception as exc:
            raise ValueError("GPT-SoVITS sidecar did not return a readable WAV") from exc
        if pcm.shape[1] != 1:
            raise ValueError("GPT-SoVITS sidecar returned non-mono audio")
        samples = pcm[:, 0]
        if native_sample_rate != self.profile.output.package_sample_rate:
            samples = soxr.resample(
                samples,
                native_sample_rate,
                self.profile.output.package_sample_rate,
                quality="HQ",
            )
        samples = np.asarray(samples, dtype=np.float32)
        if samples.size == 0 or not np.isfinite(samples).all():
            raise ValueError("GPT-SoVITS sidecar returned invalid audio samples")

        return SynthesisResult(
            pcm=samples,
            sample_rate=self.profile.output.package_sample_rate,
            backend_metadata={
                "backend": "gpt_sovits_v2pro",
                "adapter_revision": self.adapter_revision,
                "model_id": self.profile.model.id,
                "model_revision": self.model_revision,
                "package_version": self.profile.model.package_version,
                "native_sample_rate": native_sample_rate,
                "seed": request.seed,
            },
        )
