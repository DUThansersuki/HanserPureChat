from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

from .contracts import PlaybackReceipt, VisualPlanRequest, VisualTurnPlan, VoiceJobRequest
from .jobs import VoiceJobManager
from .performance import (
    CrossModalCompatibility,
    VoiceCapabilityMapper,
    VisualCapabilityMapper,
)
from .profiles import load_rig_profile, load_voice_assets, load_voice_profile
from .settings import RuntimeSettings
from .tts.voxcpm2 import VoxCPM2Backend


def create_app(
    *,
    settings: RuntimeSettings | None = None,
    manager: VoiceJobManager | None = None,
) -> FastAPI:
    active = settings or RuntimeSettings.from_environment()
    profile = load_voice_profile(active.voice_profile)
    rig = load_rig_profile(active.rig_profile)
    assets = load_voice_assets(active.voice_profile, profile) if profile.enabled else {}
    backend = (
        VoxCPM2Backend.load(profile)
        if profile.enabled and active.load_voice_model
        else None
    )
    jobs = manager or VoiceJobManager(
        data_root=active.data_root,
        profile=profile,
        rig=rig,
        assets=assets,
        backend=backend,
        max_pending_jobs=active.max_pending_jobs,
        max_text_codepoints=active.max_text_codepoints,
        event_window=active.event_window,
        max_ready_segments=active.max_ready_segments,
        interactive_deadline_seconds=active.interactive_deadline_seconds,
        offline_deadline_seconds=active.offline_deadline_seconds,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await jobs.start()
        yield
        await jobs.close()

    app = FastAPI(
        title="Hanser Voice/Performance Runtime",
        version="0.1.0",
        lifespan=lifespan,
    )

    async def trusted_request(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> None:
        if active.internal_token:
            if authorization != f"Bearer {active.internal_token}":
                raise HTTPException(status_code=401, detail="invalid internal token")
            return
        client = request.client.host if request.client else ""
        if client not in {"127.0.0.1", "::1", "localhost", "testclient"}:
            raise HTTPException(status_code=403, detail="runtime is loopback-only")

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {"ok": True}

    @app.get("/runtime")
    async def runtime() -> dict[str, object]:
        voice_reasons: list[str] = []
        if not profile.enabled:
            voice_reasons.append("voice_profile_disabled")
        if profile.enabled and not assets:
            voice_reasons.append("approved_voice_assets_unavailable")
        if profile.enabled and backend is None:
            voice_reasons.append("voice_model_not_loaded")
        return {
            "schema_version": "1.1",
            "control_ready": True,
            "voice_ready": jobs.voice_ready,
            "visual_ready": rig.enabled,
            "voice_reasons": voice_reasons,
            "visual_reasons": [] if rig.enabled else ["rig_profile_disabled"],
            "queue_depth": jobs.queue_depth,
            "profile_revision": profile.profile_revision,
            "rig_revision": rig.revision,
            "model_revision": profile.model.revision,
            "model_loaded": backend is not None,
        }

    @app.post("/internal/v1/jobs", status_code=202, dependencies=[Depends(trusted_request)])
    async def create_job(body: VoiceJobRequest) -> dict[str, object]:
        if not jobs.voice_ready:
            raise HTTPException(status_code=503, detail="voice runtime is not ready")
        try:
            snapshot, created = await jobs.create(body)
        except OverflowError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {**snapshot.model_dump(mode="json"), "created": created}

    @app.get(
        "/internal/v1/jobs/{job_id}", dependencies=[Depends(trusted_request)]
    )
    async def get_job(
        job_id: str,
        x_hanser_owner: str = Header(alias="X-Hanser-Owner"),
    ) -> dict[str, object]:
        try:
            snapshot = await jobs.get(job_id, x_hanser_owner)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="voice job not found") from exc
        return snapshot.model_dump(mode="json")

    @app.get(
        "/internal/v1/jobs/{job_id}/events", dependencies=[Depends(trusted_request)]
    )
    async def job_events(
        job_id: str,
        request: Request,
        after: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        x_hanser_owner: str = Header(alias="X-Hanser-Owner"),
    ) -> StreamingResponse:
        try:
            sequence = max(after, int(last_event_id or 0))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid Last-Event-ID") from exc

        async def stream():
            cursor = sequence
            while not await request.is_disconnected():
                try:
                    events, terminal = await jobs.wait_for_events(
                        job_id, x_hanser_owner, cursor
                    )
                except KeyError:
                    yield "event: error\ndata: {\"detail\":\"voice job not found\"}\n\n"
                    return
                for event in events:
                    cursor = event.sequence
                    yield (
                        f"id: {cursor}\nevent: {event.type}\n"
                        f"data: {event.model_dump_json()}\n\n"
                    )
                if terminal:
                    return
                if not events:
                    yield ": keepalive\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post(
        "/internal/v1/jobs/{job_id}/cancel", dependencies=[Depends(trusted_request)]
    )
    async def cancel_job(
        job_id: str,
        x_hanser_owner: str = Header(alias="X-Hanser-Owner"),
    ) -> dict[str, object]:
        try:
            snapshot = await jobs.cancel(job_id, x_hanser_owner)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="voice job not found") from exc
        return snapshot.model_dump(mode="json")

    @app.post(
        "/internal/v1/jobs/{job_id}/playback", dependencies=[Depends(trusted_request)]
    )
    async def playback(
        job_id: str,
        receipt: PlaybackReceipt,
        x_hanser_owner: str = Header(alias="X-Hanser-Owner"),
    ) -> dict[str, bool]:
        try:
            await jobs.record_playback(job_id, x_hanser_owner, receipt)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="voice job not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"accepted": True}

    @app.get(
        "/internal/v1/artifacts/{artifact_id}", dependencies=[Depends(trusted_request)]
    )
    async def artifact(
        artifact_id: str,
        x_hanser_owner: str = Header(alias="X-Hanser-Owner"),
    ):
        resolved = jobs.resolve_artifact(x_hanser_owner, artifact_id)
        if resolved is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        path, media_type = resolved
        return FileResponse(path, media_type=media_type, filename=path.name)

    @app.post(
        "/internal/v1/visual-plans", dependencies=[Depends(trusted_request)]
    )
    async def visual_plan(body: VisualPlanRequest) -> dict[str, object]:
        if not rig.enabled:
            raise HTTPException(status_code=503, detail="visual runtime is not ready")
        visual = VisualCapabilityMapper().resolve(
            body.snapshot.allowed_performance, rig
        )
        if body.snapshot.output_preferences.speech:
            voice = VoiceCapabilityMapper().resolve(
                body.snapshot.allowed_performance,
                profile,
            )
            visual = CrossModalCompatibility().filter(voice, visual)
        identity = json.dumps(
            {
                "reply_id": body.snapshot.reply_id,
                "rig_revision": rig.revision,
                "epoch": body.epoch,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        plan = VisualTurnPlan(
            plan_id="visual_" + hashlib.sha256(identity).hexdigest()[:24],
            reply_id=body.snapshot.reply_id,
            epoch=body.epoch,
            preset=visual.preset,
            weight=visual.weight,
            motion=visual.motion,
            attack_ms=rig.attack_ms,
            hold_ms=max(rig.hold_ms, min(12000, len(body.snapshot.semantic_text) * 90)),
            release_ms=rig.release_ms,
            rig_revision=rig.revision,
            degraded_reasons=visual.degraded_reasons,
        )
        return plan.model_dump(mode="json")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    configured = RuntimeSettings.from_environment()
    uvicorn.run(
        "voice_runtime.api:app",
        host=configured.host,
        port=configured.port,
        reload=False,
    )
