from __future__ import annotations

import asyncio
import json
import time
import winsound
from pathlib import Path

from voice_runtime.contracts import (
    AllowedPerformance,
    Delivery,
    JobStatus,
    OutputPreferences,
    PlaybackReceipt,
    ReplySnapshot,
    VoiceJobRequest,
)
from voice_runtime.jobs import VoiceJobManager
from voice_runtime.profiles import load_rig_profile, load_voice_assets, load_voice_profile
from voice_runtime.tts.voxcpm2_hybrid import VoxCPM2HybridBackend


OWNER = "real-runtime-verification"
THREE_SEGMENT_TEXT = (
    "今天我们慢慢聊一聊。你刚才说的事情，我已经认真听到了。"
    "先别急着给自己下结论，我们可以一件一件看清楚。"
    "等你准备好了，再告诉我你最在意的是哪句话。我会继续听你说。"
)


def snapshot(reply_id: str, text: str) -> ReplySnapshot:
    return ReplySnapshot(
        request_id=f"request-{reply_id}",
        reply_id=reply_id,
        user_id=OWNER,
        conversation_id="conversation-real-runtime-verification",
        semantic_text=text,
        display_text=text,
        allowed_performance=AllowedPerformance(
            delivery=Delivery.NEUTRAL,
            intensity=0.2,
            constraints_ref="constraints:real-runtime-verification",
        ),
        output_preferences=OutputPreferences(speech=True),
        allow_tts=True,
        language="zh",
        constraints_ref="constraints:real-runtime-verification",
        render_profile_revision="hanser-chat-voice-neutral-v1",
        text_source="validated_semantic",
    )


async def wait_terminal(manager: VoiceJobManager, job_id: str, timeout: float = 180) -> object:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = await manager.get(job_id, OWNER)
        if current.status in {JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.FAILED}:
            return current
        await asyncio.sleep(0.1)
    raise TimeoutError(job_id)


async def play_and_receipt(manager: VoiceJobManager, job: object, epoch: int) -> list[str]:
    played: list[str] = []
    for segment in job.ready_segments:
        resolved = manager.resolve_artifact(OWNER, segment.audio.artifact_id)
        if resolved is None:
            raise RuntimeError("published audio artifact is missing")
        artifact_path, _ = resolved
        winsound.PlaySound(str(artifact_path), winsound.SND_FILENAME)
        await manager.record_playback(
            job.job_id,
            OWNER,
            PlaybackReceipt(
                playback_id=f"real-playback-epoch-{epoch}",
                epoch=epoch,
                event_id=f"real-receipt-{epoch}-{segment.segment_id}",
                status="completed",
                segment_id=segment.segment_id,
                phase="gap",
                sample_offset=segment.timeline.pause_after_samples,
            ),
        )
        played.append(segment.segment_id)
    return played


async def play_until_terminal(manager: VoiceJobManager, job_id: str, epoch: int) -> tuple[object, list[str]]:
    played: list[str] = []
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        job = await manager.get(job_id, OWNER)
        for segment in job.ready_segments:
            if segment.segment_id in played:
                continue
            await play_and_receipt(manager, job.model_copy(update={"ready_segments": [segment]}), epoch)
            played.append(segment.segment_id)
        if job.status in {JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.FAILED}:
            return job, played
        await asyncio.sleep(0.1)
    raise TimeoutError(job_id)


async def main() -> None:
    root = Path(__file__).resolve().parents[2]
    profile_path = root / "voice_runtime/config/voice_profile.chat_voice.yml"
    profile = load_voice_profile(profile_path)
    rig = load_rig_profile(root / "voice_runtime/config/rig_profile.disabled.yml")
    assets = load_voice_assets(profile_path, profile)
    load_started = time.perf_counter()
    backend = VoxCPM2HybridBackend.load(profile)
    load_seconds = time.perf_counter() - load_started
    verification_root = root / "release/verification/real-runtime-flow"
    run_root = verification_root / f"run-{time.time_ns()}"
    manager = VoiceJobManager(
        data_root=run_root,
        profile=profile,
        rig=rig,
        assets=assets,
        backend=backend,
        max_ready_segments=1,
        interactive_deadline_seconds=600,
    )
    report: dict[str, object] = {}
    await manager.start()
    try:
        first, _ = await manager.create(
            VoiceJobRequest(
                owner=OWNER,
                snapshot=snapshot("real-flow-first", THREE_SEGMENT_TEXT),
            )
        )
        first, first_played = await play_until_terminal(manager, first.job_id, epoch=1)
        if first.status != JobStatus.COMPLETED:
            raise RuntimeError(f"first job failed: {first.terminal_reason}")
        if len(first_played) < 3:
            raise RuntimeError(f"expected at least three played segments, got {len(first_played)}")

        cancelled, _ = await manager.create(
            VoiceJobRequest(
                owner=OWNER,
                snapshot=snapshot(
                    "real-flow-cancelled",
                    "这是一条用于验证停止行为的较长消息。" * 8,
                ),
            )
        )
        while True:
            current = await manager.get(cancelled.job_id, OWNER)
            if current.status in {JobStatus.PREPARING, JobStatus.GENERATING}:
                break
            await asyncio.sleep(0.05)
        cancelled = await manager.cancel(cancelled.job_id, OWNER)

        after_stop, _ = await manager.create(
            VoiceJobRequest(
                owner=OWNER,
                snapshot=snapshot("real-flow-after-stop", "你好，今天也要好好休息哦。"),
            )
        )
        after_stop = await wait_terminal(manager, after_stop.job_id)
        if after_stop.status != JobStatus.COMPLETED:
            raise RuntimeError(f"post-cancel job failed: {after_stop.terminal_reason}")
        second_played = await play_and_receipt(manager, after_stop, epoch=2)
        cancelled_final = await manager.get(cancelled.job_id, OWNER)
        report = {
            "profile_revision": profile.profile_revision,
            "identity_asset_id": profile.clone.identity_reference_id,
            "style_asset_id": profile.clone.default_style_prompt_id,
            "model_device": profile.voxcpm2.model_device,
            "audio_vae_device": profile.voxcpm2.audio_vae_device,
            "load_seconds": round(load_seconds, 3),
            "first_job": first.model_dump(mode="json"),
            "first_played_segments": first_played,
            "cancelled_job": cancelled_final.model_dump(mode="json"),
            "after_stop_job": after_stop.model_dump(mode="json"),
            "after_stop_played_segments": second_played,
        }
    finally:
        await manager.close()
        backend.close()

    verification_root.mkdir(parents=True, exist_ok=True)
    report["run_root"] = str(run_root.resolve())
    report_path = verification_root / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
