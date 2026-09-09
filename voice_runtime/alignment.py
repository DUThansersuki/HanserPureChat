from __future__ import annotations

from .contracts import AmplitudeCue


def amplitude_envelope(
    pcm,
    sample_rate: int,
    *,
    window_ms: int = 20,
    noise_floor: float = 0.008,
    gain: float = 4.0,
) -> list[AmplitudeCue]:
    import numpy as np

    samples = np.asarray(pcm, dtype=np.float32).reshape(-1)
    window = max(1, round(sample_rate * window_ms / 1000))
    cues: list[AmplitudeCue] = []
    previous = 0.0
    for start in range(0, samples.size, window):
        end = min(samples.size, start + window)
        rms = float(np.sqrt(np.mean(np.square(samples[start:end]))))
        target = max(0.0, min(1.0, (rms - noise_floor) * gain))
        coefficient = 0.55 if target > previous else 0.35
        value = previous + coefficient * (target - previous)
        cues.append(
            AmplitudeCue(start_sample=start, end_sample=end, value=value)
        )
        previous = value
    return cues


def trailing_silence_samples(
    pcm,
    *,
    threshold: float = 0.004,
    maximum_fraction: float = 0.25,
) -> int:
    import numpy as np

    samples = np.asarray(pcm, dtype=np.float32).reshape(-1)
    if not samples.size:
        return 0
    limit = int(samples.size * maximum_fraction)
    count = 0
    for value in samples[::-1]:
        if abs(float(value)) > threshold or count >= limit:
            break
        count += 1
    return count
