"""Consume committed replies through the existing owner-checked Voice runtime."""
from __future__ import annotations

import asyncio
import io
import logging
import math
import re
import os
import shutil
import subprocess
import wave
from array import array
from pathlib import Path
import sys

import httpx

logger = logging.getLogger(__name__)


def amplify_audio(blob: bytes, gain_db: float = 6.0) -> tuple[bytes, dict]:
    """Raise average level with a latency-compensated, -1 dBFS peak limiter."""
    with wave.open(io.BytesIO(blob), 'rb') as source:
        params = source.getparams()
        if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 48000):
            raise ValueError('voice_pcm_format_mismatch')
        pcm = array('h', source.readframes(source.getnframes()))
    if sys.byteorder != 'little':
        pcm.byteswap()
    peak = max((abs(sample) for sample in pcm), default=0)
    before_energy = sum(sample * sample for sample in pcm)
    raw = pcm.tobytes()
    if sys.byteorder != 'little':
        pcm.byteswap()
        raw = pcm.tobytes()
        pcm.byteswap()
    if peak and gain_db:
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            raise FileNotFoundError('FFmpeg is required for QQ output limiting')
        try:
            converted = subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error',
                '-f', 's16le', '-ar', '48000', '-ac', '1', '-i', 'pipe:0',
                '-af', f'volume={gain_db:g}dB,alimiter=limit=0.891250938:attack=5:release=80:level=0:latency=1',
                '-f', 's16le', 'pipe:1'], input=raw, capture_output=True, check=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ValueError('voice_output_limiter_failed') from exc
        data = converted.stdout
    else:
        data = raw
    if len(data) != len(raw):
        raise ValueError('voice_output_limiter_duration_changed')
    output_pcm = array('h', data)
    if sys.byteorder != 'little':
        output_pcm.byteswap()
    output_peak = max((abs(sample) for sample in output_pcm), default=0)
    after_energy = sum(sample * sample for sample in output_pcm)
    applied = round(10 * math.log10(after_energy / before_energy), 3) if before_energy else 0.0
    output = io.BytesIO()
    with wave.open(output, 'wb') as target:
        target.setparams(params)
        target.writeframes(data)
    return output.getvalue(), {'requested_gain_db': gain_db, 'applied_gain_db': applied,
        'gain_mode': 'lookahead_peak_limiter', 'applied_gain_measurement': 'whole_clip_rms',
        'peak_before_dbfs': round(20 * math.log10(peak / 32768), 3) if peak else None,
        'peak_after_dbfs': round(20 * math.log10(output_peak / 32768), 3) if output_peak else None,
        'peak_limit_dbfs': -1.0}


def suppress_start_transient(samples: array) -> int:
    """Remove the observed AudioVAE startup pulse only when isolated by silence.

    Keep duration and all subsequent samples. A normal onset without the known
    decaying prefix and at least 40ms of quiet is left untouched.
    """
    if len(samples) < 12000 or samples[0] < 2000:
        return 0
    ratios = [samples[i] / samples[0] for i in (1, 2, 3)]
    if not (.94 < ratios[0] < .98 and .89 < ratios[1] < .95 and .86 < ratios[2] < .93):
        return 0
    run = 0
    for start in range(960, min(len(samples), 12000), 480):
        window = samples[start:start + 480]
        quiet = sum(x * x for x in window) <= len(window) * 131 ** 2
        run = run + 1 if quiet else 0
        if run == 4:
            quiet_start = start - 3 * 480
            end = quiet_start + 240  # pulse plus 5ms inside the confirmed quiet
            samples[:end] = array('h', [0]) * end
            return end
    return 0


def join_audio(blobs: list[bytes], pauses: list[int], *, min_pause_ms: int = 450, cleanup_notes=None) -> bytes:
    """Preserve PCM and fill sentence gaps, counting silence already in the clip."""
    clips = []
    for index, (blob, pause) in enumerate(zip(blobs, pauses, strict=True)):
        with wave.open(io.BytesIO(blob), 'rb') as source:
            if (source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype()) != (1, 2, 48000, "NONE"):
                raise ValueError("voice_pcm_format_mismatch")
            samples = array('h', source.readframes(source.getnframes()))
        if sys.byteorder != 'little':
            samples.byteswap()
        suppressed = suppress_start_transient(samples)
        if cleanup_notes is not None:
            cleanup_notes.append({'index': index, 'suppressed_start_ms': round(suppressed / 48, 3)})
        clips.append((samples, pause))
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(48000)
        for index, (samples, pause) in enumerate(clips):
            frames = array('h', samples)
            if sys.byteorder != 'little':
                frames.byteswap()
            target.writeframes(frames.tobytes())
            if index + 1 < len(blobs):
                # Match the runtime's 0.004 full-scale quiet threshold. Keep
                # natural tails intact and add only the missing silence.
                tail = next((offset for offset, sample in enumerate(reversed(samples))
                             if abs(sample) > 131), len(samples))
                following = clips[index + 1][0]
                head = next((offset for offset, sample in enumerate(following) if abs(sample) > 131), len(following))
                gap = max(0, pause - head, round(48000 * min_pause_ms / 1000) - tail - head)
                target.writeframes(b"\0\0" * gap)
    return output.getvalue()


class QQVoiceRenderer:
    def __init__(self, conversations, performance, settings, *, bridge=None):
        from hanser_agent.render_bridge import RenderBridge, RenderBridgeFailure
        self.bridge = bridge or RenderBridge(conversations, performance)
        self.bridge_failure = RenderBridgeFailure
        self.conversations = conversations
        self.settings = settings
        self.root = settings.storage_path.parent / "voice_clips"

    async def close(self):
        await self.bridge.close()

    async def status(self):
        if not self.settings.gateway.voice_reply.enabled:
            return {"enabled": False, "voice_ready": False}
        try:
            async with asyncio.timeout(1):
                status = await self.bridge.get_runtime("qq:0:0")
            return {"enabled": True, **{key: status.get(key) for key in
                ("voice_ready", "voice_reasons", "profile_revision", "model_loaded", "queue_depth")}}
        except (TimeoutError, httpx.HTTPError, self.bridge_failure):
            return {"enabled": True, "voice_ready": False, "voice_reasons": ["voice_runtime_unavailable"]}

    async def render(self, reply_id: str, user_id: str) -> dict:
        snapshot = self.conversations.get_reply_snapshot(reply_id, user_id=user_id)
        if snapshot is None or not snapshot.allow_tts or not snapshot.output_preferences.speech:
            return {"voice_status": "text_only", "voice_reason": "snapshot_not_speakable"}
        try:
            async with asyncio.timeout(self.settings.gateway.voice_reply.timeout_seconds):
                return await self._render(reply_id, user_id)
        except (TimeoutError, httpx.HTTPError, self.bridge_failure, ValueError, OSError, wave.Error) as exc:
            # This happens before any OneBot send. Never fall back after an ambiguous send.
            logger.warning("QQ voice unavailable reply=%s exception=%s", reply_id, type(exc).__name__)
            return {"voice_status": "unavailable", "voice_reason": "synthesis_unavailable"}

    async def _render(self, reply_id: str, user_id: str):
        job = await self.bridge.create_voice_job(reply_id=reply_id, user_id=user_id,
            mode="offline", rendition_id="qq-voice-comma-v5")
        job_id = job["job_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", job_id):
            raise ValueError("invalid_voice_job_id")
        while job["status"] not in {"completed", "failed", "cancelled"}:
            await asyncio.sleep(0.5)
            job = await self.bridge.get_job(job_id, user_id)
        if job["status"] != "completed" or job.get("partial") or not job["ready_segments"]:
            raise ValueError("voice_job_incomplete")
        blobs, pauses = [], []
        for index, segment in enumerate(job["ready_segments"]):
            if segment["index"] != index or segment["audio"]["qa_status"] != "passed":
                raise ValueError("voice_segment_not_validated")
            stream = await self.bridge.stream_artifact(segment["audio"]["artifact_id"], user_id, range_header=None)
            blobs.append(b"".join([chunk async for chunk in stream.body]))
            pauses.append(segment['timeline']['pause_after_samples'])
        pause_ms = self.settings.gateway.voice_reply.min_pause_ms
        cleanup_notes = []
        audio = join_audio(blobs, pauses, min_pause_ms=pause_ms, cleanup_notes=cleanup_notes)
        audio, level = amplify_audio(audio, self.settings.gateway.voice_reply.output_gain_db)
        self.root.mkdir(parents=True, exist_ok=True)
        with wave.open(io.BytesIO(audio), 'rb') as file:
            duration = file.getnframes() / file.getframerate()
        path = self.root / f"{job_id}-mix-v4-{pause_ms}ms-gain{level['requested_gain_db']:g}db-limited.wav"
        temporary = path.with_suffix('.tmp.wav')
        temporary.write_bytes(audio)
        temporary.replace(path)
        clip = {"file": str(path.resolve()), "job_id": job_id,
                "source_segment_count": len(blobs), "min_pause_ms": pause_ms,
                "duration_seconds": round(duration, 3), 'level_adjustment': level,
                'boundary_cleanup': cleanup_notes}
        return {"voice_status": "ready", "voice": clip, "voices": [clip]}


def onebot_message(batch: dict, text: str, audio_root: Path):
    from nonebot.adapters.onebot.v11 import Message, MessageSegment
    message = Message()
    if batch.get("delivery_kind", "text") == "record":
        path = Path(text).resolve()
        if path.parent != audio_root.resolve() or path.suffix != ".wav" or not path.is_file():
            raise ValueError("QQ voice artifact is outside the trusted audio directory")
        return message + MessageSegment.record(file=path.as_uri(), cache=False)
    if batch["kind"] == "group" and batch.get("part_index", 0) == 0:
        message += MessageSegment.at(batch["sender_id"])
        message += MessageSegment.text(" ")
    return message + MessageSegment.text(text)
