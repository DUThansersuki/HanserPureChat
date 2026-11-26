from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from demucs.apply import apply_model
from demucs.pretrained import get_model
from demucs.separate import load_track
from scipy.signal import resample_poly


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    args = parser.parse_args()
    source = args.source.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    model = get_model("htdemucs")
    model.cpu().eval()
    loaded = time.perf_counter()
    wav = load_track(source, model.audio_channels, model.samplerate)
    reference = wav.mean(0)
    wav -= reference.mean()
    wav /= reference.std()
    sources = apply_model(
        model,
        wav[None],
        device=args.device,
        shifts=1,
        split=True,
        overlap=0.25,
        progress=True,
        num_workers=0,
    )[0]
    sources *= reference.std()
    sources += reference.mean()
    vocals = sources[model.sources.index("vocals")].mean(0).cpu().numpy()
    vocals_48k = resample_poly(vocals, 160, 147).astype(np.float32)
    vocals_path = output_dir / "clip_000180.demucs-vocals.wav"
    sf.write(vocals_path, vocals_48k, 48_000, subtype="PCM_16")

    enhanced_path = output_dir / "clip_000180.demucs-vocals-enhanced.wav"
    filter_started = time.perf_counter()
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(vocals_path),
            "-af",
            "highpass=f=70,lowpass=f=12000,afftdn=nr=8:nf=-40,alimiter=limit=0.95",
            "-ar",
            "48000",
            "-ac",
            "1",
            str(enhanced_path),
        ],
        check=True,
    )
    finished = time.perf_counter()
    report = {
        "source": str(source),
        "model": "htdemucs",
        "device": args.device,
        "model_load_seconds": round(loaded - started, 3),
        "separation_and_write_seconds": round(filter_started - loaded, 3),
        "post_filter_seconds": round(finished - filter_started, 3),
        "total_seconds": round(finished - started, 3),
        "outputs": [str(vocals_path), str(enhanced_path)],
        "sample_rate": 48_000,
        "channels": 1,
    }
    (output_dir / "reference-processing-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
