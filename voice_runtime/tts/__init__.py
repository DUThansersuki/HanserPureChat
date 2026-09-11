from .base import SynthesisRequest, SynthesisResult, TTSBackend
from .gpt_sovits_v2pro import GPTSoVITSV2ProBackend


def load_backend(profile):
    return GPTSoVITSV2ProBackend.load(profile)


__all__ = [
    "GPTSoVITSV2ProBackend",
    "SynthesisRequest",
    "SynthesisResult",
    "TTSBackend",
    "load_backend",
]
