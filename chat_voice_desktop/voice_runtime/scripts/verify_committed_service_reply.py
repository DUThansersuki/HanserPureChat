"""Synthesize a committed real-service reply in an isolated runtime directory.

This verifies synthesis/persistence, not Renderer playback or perceptual quality.
"""
import argparse
import asyncio
import json
import sqlite3
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from voice_runtime.contracts import ReplySnapshot, VoiceJobRequest, JobStatus
from voice_runtime.jobs import VoiceJobManager
from voice_runtime.profiles import load_rig_profile, load_voice_assets, load_voice_profile
from voice_runtime.tts.voxcpm2_hybrid import VoxCPM2HybridBackend


async def run(args):
    args.output.mkdir(parents=True, exist_ok=False)
    source = sqlite3.connect(f"file:{args.database.resolve().as_posix()}?mode=ro", uri=True)
    try:
        row = source.execute("SELECT snapshot_json FROM reply_snapshots WHERE request_id=?", (args.request_id,)).fetchone()
    finally:
        source.close()
    # Use the same projection as RenderBridge._runtime_snapshot.
    payload = json.loads(row[0])
    payload.pop("persona_trace", None)
    snapshot = ReplySnapshot.model_validate(payload)
    assert snapshot.allow_tts and snapshot.text_source == "validated_semantic"
    (args.output / "input_snapshot.json").write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    profile_path = root / "voice_runtime/config/voice_profile.chat_voice.yml"
    profile = load_voice_profile(profile_path)
    rig = load_rig_profile(root / "voice_runtime/config/rig_profile.disabled.yml")
    assets = load_voice_assets(profile_path, profile)
    started = time.perf_counter()
    backend = VoxCPM2HybridBackend.load(profile)
    load_seconds = time.perf_counter() - started
    print(json.dumps({"model_load_seconds": load_seconds}), flush=True)
    manager = VoiceJobManager(data_root=args.output / "runtime", profile=profile, rig=rig,
                              assets=assets, backend=backend)
    await manager.start()
    try:
        request = VoiceJobRequest(owner=snapshot.user_id, snapshot=snapshot, mode="offline")
        started = time.perf_counter()
        job, created = await manager.create(request)
        first_ready = None
        while time.perf_counter() - started < 600:
            job = await manager.get(job.job_id, snapshot.user_id)
            if job.ready_segments and first_ready is None:
                first_ready = time.perf_counter() - started
            if job.status in {JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.FAILED}:
                break
            await asyncio.sleep(0.1)
        assert job.status == JobStatus.COMPLETED, job.model_dump_json()
        generation_seconds = time.perf_counter() - started
        audio = []
        for segment in job.ready_segments:
            path, _ = manager.resolve_artifact(snapshot.user_id, segment.audio.artifact_id)
            samples, rate = sf.read(path)
            assert rate == 48000 and samples.ndim == 1 and np.isfinite(samples).all()
            assert np.max(np.abs(samples)) > 0
            audio.append({"path": str(path), "sample_rate": rate, "samples": len(samples),
                          "seconds": len(samples) / rate, "peak": float(np.max(np.abs(samples)))})
        repeat, created = await manager.create(request)
        assert not created and repeat.job_id == job.job_id
        report = {"status": "PASS", "reply_id": snapshot.reply_id, "job_id": job.job_id,
                  "model_load_seconds": load_seconds, "first_ready_seconds": first_ready,
                  "generation_seconds": generation_seconds, "audio": audio,
                  "idempotent_job_reuse": True, "renderer_playback": "NOT_EXECUTED",
                  "perceptual_quality": "NOT_EVALUATED", "snapshot_modified": False}
        (args.output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)
    finally:
        await manager.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--request-id", default="lifecycle-9")
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
