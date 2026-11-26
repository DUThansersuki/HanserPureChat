from __future__ import annotations

import argparse
import json
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf


def dbfs(value: float) -> float:
    return round(20 * np.log10(max(value, 1e-12)), 3)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("audio", nargs="+", type=Path)
    args = parser.parse_args()
    rows: list[dict[str, object]] = []
    for input_path in args.audio:
        path = input_path.resolve()
        audio, sample_rate = sf.read(path, dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        first_half_second = audio[: sample_rate // 2]
        rows.append(
            {
                "path": str(path),
                "sample_rate": sample_rate,
                "channels": 1,
                "duration_seconds": round(len(audio) / sample_rate, 3),
                "rms_dbfs": dbfs(float(np.sqrt(np.mean(np.square(audio))))),
                "first_500ms_rms_dbfs": dbfs(
                    float(np.sqrt(np.mean(np.square(first_half_second))))
                ),
                "peak_dbfs": dbfs(float(np.max(np.abs(audio)))),
                "spectral_centroid_hz": round(
                    float(librosa.feature.spectral_centroid(y=audio, sr=sample_rate).mean()),
                    1,
                ),
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
