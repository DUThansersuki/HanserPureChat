from __future__ import annotations

from .contracts import AmplitudeCue


def amplitude_envelope(
    pcm,
    sample_rate: int,
    *,
    window_ms: int = 20,
    noise_floor: float = 0.008,
    gain: float = 1.15,
) -> list[AmplitudeCue]:
    import numpy as np

    samples = np.asarray(pcm, dtype=np.float32).reshape(-1)
    window = max(1, round(sample_rate * window_ms / 1000))
    if not samples.size:
        return []
    rms_frames = np.asarray(
        [
            float(np.sqrt(np.mean(np.square(samples[start : start + window]))))
            for start in range(0, samples.size, window)
        ],
        dtype=np.float32,
    )
    adaptive_floor = max(noise_floor, float(np.percentile(rms_frames, 20)) * 1.35)
    speech_level = float(np.percentile(rms_frames, 90))
    dynamic_range = max(0.012, speech_level - adaptive_floor)
    normalized = np.clip(
        (rms_frames - adaptive_floor) / (dynamic_range * gain), 0.0, 1.0
    )
    normalized = np.where(normalized < 0.07, 0.0, np.power(normalized, 0.68))
    if normalized.size >= 3:
        normalized = np.median(
            np.stack((np.roll(normalized, 1), normalized, np.roll(normalized, -1))),
            axis=0,
        )
        normalized[0] = max(normalized[0], normalized[1] * 0.35)
        normalized[-1] = min(normalized[-1], normalized[-2])
    # One 20 ms frame of partial look-ahead offsets display latency without
    # making the avatar visibly lead the voice.
    if normalized.size >= 2:
        normalized[:-1] = np.maximum(normalized[:-1], normalized[1:] * 0.42)

    cues: list[AmplitudeCue] = []
    previous = 0.0
    for frame_index, start in enumerate(range(0, samples.size, window)):
        end = min(samples.size, start + window)
        target = float(normalized[frame_index])
        coefficient = 0.72 if target > previous else 0.48
        value = previous + coefficient * (target - previous)
        if target == 0.0 and value < 0.035:
            value = 0.0
        cues.append(
            AmplitudeCue(
                start_sample=start,
                end_sample=end,
                value=round(value, 6),
            )
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
