from .base import SynthesisRequest, SynthesisResult, TTSBackend
from .gpt_sovits_v2pro import GPTSoVITSV2ProBackend
from .voxcpm2 import VoxCPM2Backend


def load_backend(profile):
    if profile.backend == "voxcpm2":
        return VoxCPM2Backend.load(profile)
    if profile.backend == "gpt_sovits_v2pro":
        return GPTSoVITSV2ProBackend.load(profile)
    raise ValueError(f"unsupported voice backend: {profile.backend}")


__all__ = [
    "GPTSoVITSV2ProBackend",
    "SynthesisRequest",
    "SynthesisResult",
    "TTSBackend",
    "VoxCPM2Backend",
    "load_backend",
]
