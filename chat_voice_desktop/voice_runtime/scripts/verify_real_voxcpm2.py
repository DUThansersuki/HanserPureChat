from __future__ import annotations

import argparse
import json
import secrets
import time
import wave
from pathlib import Path

import numpy as np

from voice_runtime.contracts import AllowedPerformance, Delivery
from voice_runtime.engine_text import EngineTextAdapter
from voice_runtime.performance import VoiceCapabilityMapper
from voice_runtime.profiles import load_voice_assets, load_voice_profile
from voice_runtime.speech import SpeechPlanner
from voice_runtime.tts.base import SynthesisRequest
from voice_runtime.tts.voxcpm2_hybrid import VoxCPM2HybridBackend
from voice_runtime.voice_assets import VoiceAssetSelector


FIXED_REPLIES = (
    "你好，今天也要好好休息哦。",
    "别着急，我们慢慢来。",
    "我会在这里陪着你的。",
)


def write_pcm16(path: Path, pcm: np.ndarray, sample_rate: int) -> None:
    samples = np.clip(np.asarray(pcm, dtype=np.float32), -1.0, 1.0)
    encoded = (samples * 32767.0).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(encoded)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path("voice_runtime/config/voice_profile.chat_voice.yml"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("release/verification/real-voxcpm2"),
    )
    parser.add_argument("--reply-count", type=int, choices=(1, 3), default=1)
    parser.add_argument(
        "--experimental-reference",
        type=Path,
        help="Verification-only identity/style WAV override; never updates the runtime manifest.",
    )
    parser.add_argument(
        "--experimental-transcript-file",
        type=Path,
        help="Exact transcript paired with --experimental-reference.",
    )
    parser.add_argument(
        "--text",
        action="append",
        help="Custom semantic text; repeat the option to generate multiple replies.",
    )
    parser.add_argument("--random-seeds", action="store_true")
    args = parser.parse_args()

    profile_path = args.profile.resolve()
    profile = load_voice_profile(profile_path)
    assets = load_voice_assets(profile_path, profile)
    experimental_reference = None
    if args.experimental_reference is not None:
        experimental_reference = str(args.experimental_reference.resolve())
        assets[profile.clone.identity_reference_id].path = experimental_reference
        if profile.clone.default_style_prompt_id is not None:
            assets[profile.clone.default_style_prompt_id].path = experimental_reference
    experimental_transcript = None
    if args.experimental_transcript_file is not None:
        experimental_transcript = args.experimental_transcript_file.read_text(
            encoding="utf-8"
        ).strip()
        if not experimental_reference:
            raise ValueError("experimental transcript requires an experimental reference")
        if profile.clone.default_style_prompt_id is None:
            raise ValueError("profile has no style prompt to receive the transcript")
        assets[profile.clone.default_style_prompt_id].transcript = experimental_transcript
    allowed = AllowedPerformance(
        delivery=Delivery.NEUTRAL,
        intensity=0.2,
        constraints_ref="constraints:real-voxcpm2-verification",
    )
    capability = VoiceCapabilityMapper().resolve(allowed, profile)
    planner = SpeechPlanner()
    selector = VoiceAssetSelector()
    adapter = EngineTextAdapter()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    load_started = time.perf_counter()
    backend = VoxCPM2HybridBackend.load(profile)
    load_seconds = time.perf_counter() - load_started
    results: list[dict[str, object]] = []
    try:
        reply_texts = tuple(args.text) if args.text else FIXED_REPLIES[: args.reply_count]
        for index, text in enumerate(reply_texts, start=1):
            speech = planner.plan(f"real-reply-{index}", text, allowed)
            if len(speech.segments) != 1:
                raise RuntimeError("fixed verification reply must produce one segment")
            segment = speech.segments[0]
            voice_plan = selector.select(segment, capability, profile, assets)
            engine_text = adapter.compile(segment.speech_text, voice_plan)
            started = time.perf_counter()
            seed = secrets.randbelow(2**31) if args.random_seeds else 2026091900 + index
            synthesis = backend.synthesize(
                SynthesisRequest(
                    engine_text=engine_text,
                    voice_plan=voice_plan,
                    seed=seed,
                    cfg_value=profile.inference.cfg_value,
                    inference_timesteps=profile.inference.inference_timesteps,
                )
            )
            elapsed = time.perf_counter() - started
            output_path = args.output_dir / f"reply-{index:02d}.wav"
            write_pcm16(output_path, synthesis.pcm, synthesis.sample_rate)
            results.append(
                {
                    "reply_id": segment.reply_id,
                    "semantic_text": text,
                    "speech_text": segment.speech_text,
                    "sample_rate": synthesis.sample_rate,
                    "channels": 1,
                    "frames": int(np.asarray(synthesis.pcm).size),
                    "seconds": round(elapsed, 3),
                    "seed": seed,
                    "identity_reference_id": voice_plan.identity_reference.asset_id,
                    "style_prompt_id": (
                        voice_plan.style_prompt.asset_id
                        if voice_plan.style_prompt is not None
                        else None
                    ),
                    "artifact": str(output_path.resolve()),
                }
            )
    finally:
        backend.close()

    report = {
        "profile_revision": profile.profile_revision,
        "model_device": profile.voxcpm2.model_device,
        "audio_vae_device": profile.voxcpm2.audio_vae_device,
        "experimental_reference": experimental_reference,
        "experimental_transcript": experimental_transcript,
        "experimental_reference_review_status": (
            "unreviewed_candidate" if experimental_reference else None
        ),
        "load_seconds": round(load_seconds, 3),
        "results": results,
    }
    report_path = args.output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
