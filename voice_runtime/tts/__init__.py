from .base import SynthesisRequest, SynthesisResult, TTSBackend
from .voxcpm2_hybrid import VoxCPM2HybridBackend


def load_backend(profile):
    return VoxCPM2HybridBackend.load(profile)


__all__ = [
    "VoxCPM2HybridBackend",
    "SynthesisRequest",
    "SynthesisResult",
    "TTSBackend",
    "load_backend",
]
