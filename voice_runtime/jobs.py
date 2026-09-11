from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import soundfile as sf

from .audio import AudioArtifactStore
from .cache import generation_identity
from .contracts import (
    AudioTurnPackage,
    JobEvent,
    JobSnapshot,
    JobStatus,
    PlaybackReceipt,
    VoiceJobRequest,
)
from .engine_text import EngineTextAdapter
from .performance import (
    CrossModalCompatibility,
    VoiceCapability,
    VoiceCapabilityMapper,
    VisualCapabilityMapper,
    VisualResolution,
)
from .profiles import RigProfile, VoiceProfile
from .speech import SpeechPlanner
from .timeline import TimelineCompiler
from .tts.base import SynthesisRequest, TTSBackend
from .voice_assets import VoiceAssetSelector


TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}


@dataclass(slots=True)
class _Job:
    request: VoiceJobRequest | None
    snapshot: JobSnapshot
    identity: str
    events: deque[JobEvent]
    created_monotonic: float = field(default_factory=time.monotonic)
    consumed_index: int = -1
    playback_event_ids: set[str] = field(default_factory=set)


class VoiceJobManager:
    """One-worker segment pipeline with durable snapshots and cancel arbitration."""

    def __init__(
        self,
        *,
        data_root: str | Path,
        profile: VoiceProfile,
        rig: RigProfile,
        assets: dict,
        backend: TTSBackend | None,
        max_pending_jobs: int = 8,
        max_text_codepoints: int = 4000,
        event_window: int = 256,
        max_ready_segments: int = 2,
        interactive_deadline_seconds: float = 120.0,
        offline_deadline_seconds: float = 900.0,
    ):
        self.data_root = Path(data_root).resolve()
        self.jobs_root = self.data_root / "jobs"
        self.packages_root = self.data_root / "packages"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.packages_root.mkdir(parents=True, exist_ok=True)
        self.profile = profile
        self.rig = rig
        self.assets = assets
        self.backend = backend
        self.max_text_codepoints = max_text_codepoints
        self.event_window = event_window
        self.max_ready_segments = max_ready_segments
        self.interactive_deadline_seconds = interactive_deadline_seconds
        self.offline_deadline_seconds = offline_deadline_seconds
        self.audio_store = AudioArtifactStore(self.data_root / "artifacts")
        self.timeline_compiler = TimelineCompiler(self.data_root / "timelines")
        self.speech_planner = SpeechPlanner()
        self.voice_mapper = VoiceCapabilityMapper()
        self.visual_mapper = VisualCapabilityMapper()
        self.cross_modal = CrossModalCompatibility()
        self.asset_selector = VoiceAssetSelector()
        self.engine_adapter = EngineTextAdapter()
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=max_pending_jobs)
        self._jobs: dict[str, _Job] = {}
        self._identity_index: dict[str, str] = {}
        self._condition = asyncio.Condition()
        self._worker_task: asyncio.Task[None] | None = None
        self._load_terminal_snapshots()

    @property
    def voice_ready(self) -> bool:
        return bool(self.profile.enabled and self.assets and self.backend is not None)

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    async def start(self) -> None:
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._worker(), name="voice-worker")

    async def close(self) -> None:
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        close_backend = getattr(self.backend, "close", None)
        if close_backend is not None:
            close_backend()

    async def create(self, request: VoiceJobRequest) -> tuple[JobSnapshot, bool]:
        if len(request.snapshot.semantic_text) > self.max_text_codepoints:
            raise ValueError("semantic text exceeds the configured voice job limit")
        identity = self._job_identity(request)
        async with self._condition:
            existing_id = self._identity_index.get(identity)
            if existing_id is not None:
                return self._jobs[existing_id].snapshot.model_copy(deep=True), False
            if self._queue.full():
                raise OverflowError("voice job queue is full")
            job_id = "voice_" + secrets.token_urlsafe(18)
            snapshot = JobSnapshot(
                job_id=job_id,
                reply_id=request.snapshot.reply_id,
                rendition_id=request.rendition_id,
                owner=request.owner,
                status=JobStatus.QUEUED,
                mode=request.mode,
            )
            job = _Job(
                request=request,
                snapshot=snapshot,
                identity=identity,
                events=deque(maxlen=self.event_window),
            )
            self._jobs[job_id] = job
            self._identity_index[identity] = job_id
            self._persist(job)
            self._queue.put_nowait(job_id)
            self._condition.notify_all()
            return snapshot.model_copy(deep=True), True

    async def get(self, job_id: str, owner: str) -> JobSnapshot:
        async with self._condition:
            job = self._owned(job_id, owner)
            return job.snapshot.model_copy(deep=True)

    async def events_after(
        self, job_id: str, owner: str, sequence: int
    ) -> list[JobEvent]:
        async with self._condition:
            job = self._owned(job_id, owner)
            return [item.model_copy(deep=True) for item in job.events if item.sequence > sequence]

    async def wait_for_events(
        self,
        job_id: str,
        owner: str,
        sequence: int,
        *,
        timeout: float = 15.0,
    ) -> tuple[list[JobEvent], bool]:
        async with self._condition:
            job = self._owned(job_id, owner)
            events = [item for item in job.events if item.sequence > sequence]
            if not events and job.snapshot.status not in TERMINAL:
                try:
                    await asyncio.wait_for(
                        self._condition.wait_for(
                            lambda: self._has_update(job_id, sequence)
                        ),
                        timeout=timeout,
                    )
                except TimeoutError:
                    pass
                job = self._owned(job_id, owner)
                events = [item for item in job.events if item.sequence > sequence]
            return (
                [item.model_copy(deep=True) for item in events],
                job.snapshot.status in TERMINAL,
            )

    async def cancel(self, job_id: str, owner: str) -> JobSnapshot:
        async with self._condition:
            job = self._owned(job_id, owner)
            if job.snapshot.status not in TERMINAL:
                self._terminal(job, JobStatus.CANCELLED, "cancel_requested")
            self._condition.notify_all()
            return job.snapshot.model_copy(deep=True)

    async def record_playback(
        self, job_id: str, owner: str, receipt: PlaybackReceipt
    ) -> None:
        async with self._condition:
            job = self._owned(job_id, owner)
            if receipt.event_id in job.playback_event_ids:
                return
            segment_index = next(
                (
                    item.index
                    for item in job.snapshot.ready_segments
                    if item.segment_id == receipt.segment_id
                ),
                None,
            )
            if segment_index is None:
                raise ValueError("playback receipt references an unpublished segment")
            job.playback_event_ids.add(receipt.event_id)
            if receipt.status in {"completed", "interrupted"} or receipt.phase == "gap":
                job.consumed_index = max(job.consumed_index, segment_index)
            self._condition.notify_all()

    def resolve_artifact(self, owner: str, artifact_id: str) -> tuple[Path, str] | None:
        audio = self.audio_store.resolve(owner, artifact_id)
        if audio is not None:
            return audio, "audio/wav"
        timeline = (self.data_root / "timelines" / f"{artifact_id}.json").resolve()
        timeline_root = (self.data_root / "timelines").resolve()
        if timeline.parent == timeline_root and timeline.is_file():
            if any(
                item.owner == owner
                and any(
                    segment.timeline.artifact_id == artifact_id
                    for segment in item.ready_segments
                )
                for item in (job.snapshot for job in self._jobs.values())
            ):
                return timeline, "application/json"
        return None

    async def _worker(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                await self._run(job_id)
            finally:
                self._queue.task_done()

    async def _run(self, job_id: str) -> None:
        async with self._condition:
            job = self._jobs[job_id]
            if job.snapshot.status == JobStatus.CANCELLED:
                return
            job.snapshot.status = JobStatus.PREPARING
            self._emit(job, "turn.started")
            self._persist(job)
            self._condition.notify_all()
        try:
            request = job.request
            if request is None:
                raise RuntimeError("persisted terminal job cannot be queued")
            speech = self.speech_planner.plan(
                request.snapshot.reply_id,
                request.snapshot.semantic_text,
                request.snapshot.allowed_performance,
            )
            if not speech.segments:
                raise ValueError("no_speakable_content")
            deadline = (
                self.interactive_deadline_seconds
                if request.mode == "interactive"
                else self.offline_deadline_seconds
            )
            previous_prompt: str | None = None
            voice_trace: list[dict[str, object]] = []
            visual_trace: list[dict[str, object]] = []
            for segment in speech.segments:
                await self._wait_for_capacity(job, segment.index, deadline)
                async with self._condition:
                    if job.snapshot.status == JobStatus.CANCELLED:
                        return
                    job.snapshot.status = JobStatus.GENERATING
                    self._persist(job)
                if time.monotonic() - job.created_monotonic > deadline:
                    raise TimeoutError("voice_job_deadline_exceeded")

                capability = self.voice_mapper.resolve(
                    request.snapshot.allowed_performance, self.profile
                )
                voice_plan = self.asset_selector.select(
                    segment,
                    capability,
                    self.profile,
                    self.assets,
                    previous_prompt_id=previous_prompt,
                    forbidden_features=tuple(
                        request.snapshot.allowed_performance.forbidden_features
                    ),
                )
                previous_prompt = (
                    voice_plan.style_prompt.asset_id
                    if voice_plan.style_prompt is not None
                    else None
                )
                engine_text = self.engine_adapter.compile(segment.speech_text, voice_plan)
                cache_key, seed = generation_identity(
                    profile=self.profile,
                    voice_plan=voice_plan,
                    engine_text=engine_text,
                    variant_salt=request.variant_salt,
                )
                artifact = self.audio_store.find(request.owner, cache_key)
                if artifact is not None:
                    pcm, _ = sf.read(
                        self.audio_store.resolve(request.owner, artifact.artifact_id),
                        dtype="float32",
                        always_2d=False,
                    )
                    degraded = [*voice_plan.degraded_reasons, "audio_cache_hit"]
                else:
                    if self.backend is None:
                        raise RuntimeError("voice_backend_unavailable")
                    result = await asyncio.to_thread(
                        self.backend.synthesize,
                        SynthesisRequest(
                            engine_text=engine_text,
                            voice_plan=voice_plan,
                            seed=seed,
                            cfg_value=self.profile.inference.cfg_value,
                            inference_timesteps=self.profile.inference.inference_timesteps,
                        ),
                    )
                    async with self._condition:
                        if job.snapshot.status == JobStatus.CANCELLED:
                            return
                    pcm = result.pcm
                    artifact = self.audio_store.publish(
                        owner=request.owner,
                        cache_key=cache_key,
                        pcm=pcm,
                        native_sample_rate=result.sample_rate,
                    )
                    degraded = list(voice_plan.degraded_reasons)
                visual = self._visual(
                    request.snapshot.allowed_performance,
                    capability,
                )
                voice_trace.append(
                    {
                        "segment_id": segment.segment_id,
                        "mapper_revision": self.voice_mapper.revision,
                        "selector_revision": self.asset_selector.revision,
                        "family": voice_plan.family,
                        "intensity_band": voice_plan.intensity_band,
                        "voice_intensity_mode": voice_plan.voice_intensity_mode,
                        "identity_reference_id": voice_plan.identity_reference.asset_id,
                        "identity_reference_revision": voice_plan.identity_reference.asset_revision,
                        "style_prompt_id": (
                            voice_plan.style_prompt.asset_id
                            if voice_plan.style_prompt is not None
                            else None
                        ),
                        "style_prompt_revision": (
                            voice_plan.style_prompt.asset_revision
                            if voice_plan.style_prompt is not None
                            else None
                        ),
                        "control_text_present": voice_plan.control_text is not None,
                        "degraded_reasons": list(voice_plan.degraded_reasons),
                    }
                )
                visual_trace.append(
                    {
                        "segment_id": segment.segment_id,
                        "mapper_revision": self.visual_mapper.revision,
                        "compatibility_revision": self.cross_modal.revision,
                        **visual.model_dump(mode="json"),
                    }
                )
                package = self.timeline_compiler.compile(
                    job_id=job.snapshot.job_id,
                    reply_id=job.snapshot.reply_id,
                    rendition_id=job.snapshot.rendition_id,
                    segment_id=segment.segment_id,
                    index=segment.index,
                    audio=artifact,
                    pcm=pcm,
                    target_gap_ms=segment.target_gap_ms,
                    visual=visual,
                    degraded_reasons=degraded,
                )
                async with self._condition:
                    if job.snapshot.status == JobStatus.CANCELLED:
                        return
                    job.snapshot.ready_segments.append(package)
                    self._emit(job, "segment.ready", segment=package)
                    self._persist(job)
                    self._condition.notify_all()

            final_package = AudioTurnPackage(
                job_id=job.snapshot.job_id,
                reply_id=job.snapshot.reply_id,
                rendition_id=job.snapshot.rendition_id,
                status="completed",
                segments=job.snapshot.ready_segments,
                total_duration_samples=sum(
                    item.segment_duration_samples for item in job.snapshot.ready_segments
                ),
                source_map=speech.source_map,
                requested=request.snapshot.performance,
                allowed=request.snapshot.allowed_performance,
                voice_resolved=voice_trace,
                visual_resolved={"segments": visual_trace},
                profile_revision=self.profile.profile_revision,
                model_revision=self.profile.model.revision,
                compiler_revision=(
                    f"{speech.mapping_revision}+{self.engine_adapter.revision}+"
                    f"{self.timeline_compiler.revision}"
                ),
            )
            self._publish_package(final_package)
            async with self._condition:
                if job.snapshot.status != JobStatus.CANCELLED:
                    self._terminal(job, JobStatus.COMPLETED, None)
                    self._condition.notify_all()
        except Exception as exc:
            async with self._condition:
                if job.snapshot.status != JobStatus.CANCELLED:
                    self._terminal(job, JobStatus.FAILED, str(exc)[:240])
                    self._condition.notify_all()

    async def _wait_for_capacity(
        self, job: _Job, next_index: int, deadline: float
    ) -> None:
        if job.request is None:
            raise RuntimeError("persisted terminal job cannot be queued")
        if job.request.mode == "offline" or next_index < self.max_ready_segments:
            return
        remaining = deadline - (time.monotonic() - job.created_monotonic)
        if remaining <= 0:
            raise TimeoutError("voice_job_deadline_exceeded")
        async with self._condition:
            try:
                await asyncio.wait_for(
                    self._condition.wait_for(
                        lambda: job.snapshot.status == JobStatus.CANCELLED
                        or next_index - job.consumed_index <= self.max_ready_segments
                    ),
                    timeout=remaining,
                )
            except TimeoutError as exc:
                raise TimeoutError("voice_job_deadline_exceeded") from exc

    def _visual(
        self,
        allowed,
        voice: VoiceCapability,
    ) -> VisualResolution:
        if not self.rig.enabled:
            return VisualResolution(preset="neutral", weight=0.0)
        return self.cross_modal.filter(
            voice,
            self.visual_mapper.resolve(allowed, self.rig),
        )

    def _owned(self, job_id: str, owner: str) -> _Job:
        job = self._jobs.get(job_id)
        if job is None or job.snapshot.owner != owner:
            raise KeyError(job_id)
        return job

    def _has_update(self, job_id: str, sequence: int) -> bool:
        job = self._jobs[job_id]
        return job.snapshot.last_sequence > sequence or job.snapshot.status in TERMINAL

    def _emit(self, job: _Job, event_type: str, **values) -> None:
        sequence = job.snapshot.last_sequence + 1
        event = JobEvent(
            job_id=job.snapshot.job_id,
            reply_id=job.snapshot.reply_id,
            rendition_id=job.snapshot.rendition_id,
            sequence=sequence,
            type=event_type,
            **values,
        )
        job.snapshot.last_sequence = sequence
        job.events.append(event)

    def _terminal(
        self, job: _Job, status: JobStatus, reason: str | None
    ) -> None:
        if job.snapshot.status in TERMINAL:
            return
        job.snapshot.status = status
        job.snapshot.terminal_reason = reason
        job.snapshot.partial = bool(job.snapshot.ready_segments) and status != JobStatus.COMPLETED
        event_type = {
            JobStatus.COMPLETED: "turn.completed",
            JobStatus.FAILED: "turn.failed",
            JobStatus.CANCELLED: "turn.cancelled",
        }[status]
        self._emit(job, event_type, reason=reason, partial=job.snapshot.partial)
        self._persist(job)

    def _persist(self, job: _Job) -> None:
        root = self.jobs_root / job.snapshot.job_id
        root.mkdir(parents=True, exist_ok=True)
        payload = {
            "identity": job.identity,
            "snapshot": job.snapshot.model_dump(mode="json"),
            "events": [event.model_dump(mode="json") for event in job.events],
        }
        temporary = root / ".state.tmp.json"
        final = root / "state.json"
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, final)

    def _publish_package(self, package: AudioTurnPackage) -> None:
        final = self.packages_root / f"{package.job_id}.json"
        temporary = self.packages_root / f".{package.job_id}.tmp.json"
        temporary.write_text(package.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temporary, final)

    def _load_terminal_snapshots(self) -> None:
        for state_path in self.jobs_root.glob("*/state.json"):
            raw = json.loads(state_path.read_text(encoding="utf-8"))
            snapshot = JobSnapshot.model_validate(raw["snapshot"])
            events = deque(
                (JobEvent.model_validate(item) for item in raw.get("events", [])),
                maxlen=self.event_window,
            )
            if snapshot.status not in TERMINAL:
                snapshot.status = JobStatus.FAILED
                snapshot.terminal_reason = "runtime_restarted"
                snapshot.partial = bool(snapshot.ready_segments)
                sequence = snapshot.last_sequence + 1
                snapshot.last_sequence = sequence
                events.append(
                    JobEvent(
                        job_id=snapshot.job_id,
                        reply_id=snapshot.reply_id,
                        rendition_id=snapshot.rendition_id,
                        sequence=sequence,
                        type="turn.failed",
                        reason="runtime_restarted",
                        partial=snapshot.partial,
                    )
                )
            job = _Job(
                request=None,
                snapshot=snapshot,
                identity=str(raw["identity"]),
                events=events,
            )
            self._jobs[snapshot.job_id] = job
            self._identity_index[job.identity] = snapshot.job_id
            self._persist(job)

    @staticmethod
    def _job_identity(request: VoiceJobRequest) -> str:
        encoded = json.dumps(
            {
                "owner": request.owner,
                "reply_id": request.snapshot.reply_id,
                "render_profile_revision": request.snapshot.render_profile_revision,
                "mode": request.mode,
                "rendition_id": request.rendition_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
