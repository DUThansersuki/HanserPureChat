from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from demucs.apply import apply_model
from demucs.pretrained import get_model
from demucs.separate import load_track
from scipy.signal import resample_poly


PROCESSING_REVISION = "demucs-htdemucs-vocals+ffmpeg-light-v1"
FILTER = "highpass=f=70,lowpass=f=12000,afftdn=nr=8:nf=-40,alimiter=limit=0.95"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json_atomic(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=Path)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    args = parser.parse_args()
    source_root = args.source_root.resolve()
    output_root = args.output_root.resolve()
    audio_root = output_root / "audio"
    transcript_root = output_root / "transcripts"
    work_root = output_root / ".work"
    audio_root.mkdir(parents=True, exist_ok=True)
    transcript_root.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "processing-manifest.json"

    audio_sources = sorted(source_root.glob("*.wav"))
    missing = [path.name for path in audio_sources if not path.with_suffix(".txt").is_file()]
    if missing:
        raise FileNotFoundError(f"missing transcripts: {missing[:5]}")
    existing: list[dict[str, object]] = []
    if manifest_path.is_file():
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("processing_revision") != PROCESSING_REVISION:
            raise ValueError("existing manifest uses another processing revision")
        existing = list(payload.get("items", []))
    completed = {
        str(item["source_name"]): item
        for item in existing
        if (output_root / str(item["output_audio"])).is_file()
        and (output_root / str(item["output_transcript"])).is_file()
    }
    pending = [path for path in audio_sources if path.name not in completed]
    started = time.perf_counter()
    model_load_seconds = 0.0
    model = None
    if pending:
        load_started = time.perf_counter()
        model = get_model("htdemucs")
        model.cpu().eval()
        model_load_seconds = time.perf_counter() - load_started

    items = list(completed.values())
    for position, source in enumerate(pending, start=1):
        item_started = time.perf_counter()
        assert model is not None
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
            progress=False,
            num_workers=0,
        )[0]
        sources *= reference.std()
        sources += reference.mean()
        vocals = sources[model.sources.index("vocals")].mean(0).cpu().numpy()
        vocals_48k = resample_poly(vocals, 160, 147).astype(np.float32)
        raw_path = work_root / source.name
        filtered_path = work_root / f"{source.stem}.filtered.wav"
        target_audio = audio_root / source.name
        target_transcript = transcript_root / source.with_suffix(".txt").name
        sf.write(raw_path, vocals_48k, 48_000, subtype="PCM_16")
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(raw_path),
                "-af",
                FILTER,
                "-ar",
                "48000",
                "-ac",
                "1",
                str(filtered_path),
            ],
            check=True,
        )
        os.replace(filtered_path, target_audio)
        raw_path.unlink()
        shutil.copy2(source.with_suffix(".txt"), target_transcript)
        info = sf.info(target_audio)
        item = {
            "source_name": source.name,
            "source_audio": str(source),
            "source_transcript": str(source.with_suffix(".txt")),
            "source_sha256": sha256(source),
            "output_audio": target_audio.relative_to(output_root).as_posix(),
            "output_transcript": target_transcript.relative_to(output_root).as_posix(),
            "output_sha256": sha256(target_audio),
            "review_status": "candidate",
            "sample_rate": info.samplerate,
            "channels": info.channels,
            "duration_seconds": round(info.duration, 3),
            "processing_seconds": round(time.perf_counter() - item_started, 3),
        }
        items.append(item)
        items.sort(key=lambda value: str(value["source_name"]))
        write_json_atomic(
            manifest_path,
            {
                "schema_version": "1.0",
                "processing_revision": PROCESSING_REVISION,
                "model": "htdemucs",
                "device": args.device,
                "filter": FILTER,
                "source_root": str(source_root),
                "review_status": "candidate",
                "items": items,
            },
        )
        if position == 1 or position % 10 == 0 or position == len(pending):
            print(f"processed {position}/{len(pending)}: {source.name}", flush=True)

    elapsed = time.perf_counter() - started
    summary = {
        "processing_revision": PROCESSING_REVISION,
        "device": args.device,
        "source_count": len(audio_sources),
        "resumed_count": len(completed),
        "processed_count": len(pending),
        "model_load_seconds": round(model_load_seconds, 3),
        "wall_seconds": round(elapsed, 3),
        "review_status": "candidate",
        "manifest": str(manifest_path),
    }
    write_json_atomic(output_root / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
