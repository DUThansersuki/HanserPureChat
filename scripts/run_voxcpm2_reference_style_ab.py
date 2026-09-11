from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from run_voxcpm2_long_reference_sweep import TARGET_TEXT, load_hybrid_model


CANDIDATES = (
    ("normal_01", "clip_000464"),
    ("normal_17", "clip_000524"),
    ("playful_24", "clip_000636"),
    ("gentle_original", "clip_000180"),
)


def source(source_dir: Path, stem: str) -> dict[str, str]:
    wav_path = source_dir / f"{stem}.wav"
    text_path = source_dir / f"{stem}.txt"
    return {
        "stem": stem,
        "wav_path": str(wav_path.resolve()),
        "text_path": str(text_path.resolve()),
        "transcript": text_path.read_text(encoding="utf-8-sig").strip(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260912)
    parser.add_argument("--steps", type=int, default=10)
    parser.add_argument("--cfg", type=float, default=2.0)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    source_dir = args.source_dir.resolve()
    identity_180 = source(source_dir, "clip_000180")
    candidates = [(label, source(source_dir, stem)) for label, stem in CANDIDATES]

    experiment = {
        "target_text": TARGET_TEXT,
        "seed": args.seed,
        "cfg_value": args.cfg,
        "inference_timesteps": args.steps,
        "group_a": "candidate WAV is both identity reference and style prompt",
        "group_b": "clip_000180 is identity reference; candidate WAV is style prompt",
        "candidates": [
            {"label": label, **candidate} for label, candidate in candidates
        ],
    }
    (args.output_dir / "experiment.json").write_text(
        json.dumps(experiment, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    model = load_hybrid_model(args.model_dir.resolve())
    sample_rate = int(model.tts_model.sample_rate)
    combinations = [
        ("A_paired", label, candidate, candidate)
        for label, candidate in candidates
    ] + [
        ("B_fixed180", label, identity_180, candidate)
        for label, candidate in candidates
    ]

    results_path = args.output_dir / "results.jsonl"
    with results_path.open("w", encoding="utf-8") as results_file:
        for index, (group, label, identity, prompt) in enumerate(combinations, start=1):
            output_path = args.output_dir / f"{group}_{label}_{prompt['stem']}.wav"
            print(f"[{index:02d}/08] {group} {label}", flush=True)
            started = time.perf_counter()
            samples = model.generate(
                text=TARGET_TEXT,
                prompt_wav_path=prompt["wav_path"],
                prompt_text=prompt["transcript"],
                reference_wav_path=identity["wav_path"],
                cfg_value=args.cfg,
                inference_timesteps=args.steps,
                normalize=False,
                denoise=False,
                retry_badcase=False,
                seed=args.seed,
            )
            samples = np.asarray(samples, dtype=np.float32).reshape(-1)
            sf.write(output_path, samples, sample_rate, subtype="PCM_16")
            elapsed = time.perf_counter() - started
            result = {
                "group": group,
                "label": label,
                "identity_stem": identity["stem"],
                "style_prompt_stem": prompt["stem"],
                "output_wav": str(output_path.resolve()),
                "sample_rate": sample_rate,
                "sample_count": int(samples.size),
                "duration_seconds": samples.size / sample_rate,
                "generation_seconds": elapsed,
            }
            results_file.write(json.dumps(result, ensure_ascii=False) + "\n")
            results_file.flush()
            print(
                f"[{index:02d}/08] done in {elapsed:.2f}s, audio {result['duration_seconds']:.2f}s",
                flush=True,
            )


if __name__ == "__main__":
    main()
