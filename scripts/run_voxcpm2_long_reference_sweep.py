from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from voxcpm import VoxCPM


TARGET_TEXT = "今天外面的风挺舒服的，感觉很适合出去走一圈。"


def select_sources(source_dir: Path, count: int) -> list[dict[str, object]]:
    selected: list[dict[str, object]] = []
    for text_path in source_dir.glob("*.txt"):
        if text_path.stem == "clip_000180":
            continue
        wav_path = text_path.with_suffix(".wav")
        if not wav_path.is_file():
            continue
        transcript = text_path.read_text(encoding="utf-8-sig").strip()
        selected.append(
            {
                "stem": text_path.stem,
                "text_path": str(text_path),
                "wav_path": str(wav_path),
                "transcript": transcript,
                "text_characters": len(transcript),
            }
        )
    selected.sort(key=lambda item: (-int(item["text_characters"]), str(item["stem"])))
    return selected[:count]


def load_hybrid_model(model_dir: Path) -> VoxCPM:
    model = VoxCPM.from_pretrained(
        str(model_dir),
        load_denoiser=False,
        local_files_only=True,
        optimize=False,
        device="cuda",
    )
    vae = model.tts_model.audio_vae.to("cpu")
    original_encode = vae.encode
    original_decode = vae.decode
    vae.encode = lambda audio, sample_rate: original_encode(audio.to("cpu"), sample_rate)
    vae.decode = lambda latent: original_decode(latent.to("cpu"))
    torch.cuda.empty_cache()
    return model


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20260911)
    parser.add_argument("--steps", type=int, default=10)
    parser.add_argument("--cfg", type=float, default=2.0)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    sources = select_sources(args.source_dir.resolve(), args.count)
    if len(sources) != args.count:
        raise RuntimeError(f"expected {args.count} source pairs, found {len(sources)}")

    selection = {
        "target_text": TARGET_TEXT,
        "selection_rule": "descending stripped TXT character count, then clip stem; clip_000180 excluded",
        "reference_mode": "same source WAV used as identity reference and neutral style prompt",
        "seed": args.seed,
        "cfg_value": args.cfg,
        "inference_timesteps": args.steps,
        "sources": sources,
    }
    (args.output_dir / "selection.json").write_text(
        json.dumps(selection, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    model = load_hybrid_model(args.model_dir.resolve())
    sample_rate = int(model.tts_model.sample_rate)
    results_path = args.output_dir / "results.jsonl"

    with results_path.open("w", encoding="utf-8") as results_file:
        for index, source in enumerate(sources, start=1):
            output_name = (
                f"{index:02d}_{source['stem']}_chars{source['text_characters']}"
                f"_seed{args.seed}_steps{args.steps}.wav"
            )
            output_path = args.output_dir / output_name
            started = time.perf_counter()
            print(f"[{index:02d}/{args.count}] generating {source['stem']} -> {output_name}", flush=True)
            samples = model.generate(
                text=TARGET_TEXT,
                prompt_wav_path=str(source["wav_path"]),
                prompt_text=str(source["transcript"]),
                reference_wav_path=str(source["wav_path"]),
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
                "index": index,
                "source_stem": source["stem"],
                "source_wav": source["wav_path"],
                "source_txt": source["text_path"],
                "source_transcript": source["transcript"],
                "source_text_characters": source["text_characters"],
                "output_wav": str(output_path.resolve()),
                "sample_rate": sample_rate,
                "sample_count": int(samples.size),
                "duration_seconds": samples.size / sample_rate,
                "generation_seconds": elapsed,
            }
            results_file.write(json.dumps(result, ensure_ascii=False) + "\n")
            results_file.flush()
            print(
                f"[{index:02d}/{args.count}] done in {elapsed:.2f}s, audio {result['duration_seconds']:.2f}s",
                flush=True,
            )


if __name__ == "__main__":
    main()
