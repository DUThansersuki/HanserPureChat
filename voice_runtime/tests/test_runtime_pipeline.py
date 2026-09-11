from __future__ import annotations

import asyncio
import json
import tempfile
import time
from io import BytesIO
from pathlib import Path

import httpx
import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from voice_runtime.api import create_app
from voice_runtime.cache import generation_identity
from voice_runtime.contracts import (
    AllowedPerformance,
    Delivery,
    EngineTextPlan,
    JobStatus,
    OutputPreferences,
    ReplySnapshot,
    VoiceAsset,
    VoiceJobRequest,
)
from voice_runtime.engine_text import EngineTextAdapter
from voice_runtime.jobs import VoiceJobManager
from voice_runtime.performance import (
    CrossModalCompatibility,
    VoiceCapabilityMapper,
    VisualCapabilityMapper,
)
from voice_runtime.profiles import RigPreset, RigProfile, VoiceProfile
from voice_runtime.speech import SpeechPlanner
from voice_runtime.settings import RuntimeSettings
from voice_runtime.tts.base import SynthesisRequest, SynthesisResult
from voice_runtime.tts.gpt_sovits_v2pro import GPTSoVITSV2ProBackend
from voice_runtime.voice_assets import VoiceAssetSelector


def allowed() -> AllowedPerformance:
    return AllowedPerformance(
        delivery=Delivery.NEUTRAL,
        intensity=0.2,
        constraints_ref="constraints:test",
    )


def profile() -> VoiceProfile:
    return VoiceProfile.model_validate(
        {
            "profile_id": "test",
            "profile_revision": "test-1",
            "status": "validated",
            "enabled": True,
            "model": {
                "id": "test/model",
                "revision": "model-1",
                "package_version": "package-1",
            },
            "clone": {
                "mode": "ref_continuation",
                "identity_reference_id": "identity",
                "default_style_prompt_id": "neutral",
            },
        }
    )


def gpt_sovits_profile() -> VoiceProfile:
    return VoiceProfile.model_validate(
        {
            "profile_id": "gpt-sovits-test",
            "profile_revision": "gpt-sovits-test-1",
            "status": "validated",
            "enabled": True,
            "backend": "gpt_sovits_v2pro",
            "model": {
                "id": "RVC-Boss/GPT-SoVITS-v2Pro",
                "revision": "model-1",
                "package_version": "package-1",
            },
            "clone": {
                "mode": "ref_continuation",
                "identity_reference_id": "identity",
                "default_style_prompt_id": "neutral",
            },
            "gpt_sovits": {"endpoint": "http://127.0.0.1:9880"},
        }
    )


def assets() -> dict[str, VoiceAsset]:
    return {
        "identity": VoiceAsset(
            asset_id="identity",
            asset_revision="identity-1",
            speaker_id="hanser",
            kind="identity_reference",
            path="identity.wav",
            review_status="approved",
            approved_languages=["zh"],
        ),
        "neutral": VoiceAsset(
            asset_id="neutral",
            asset_revision="neutral-1",
            speaker_id="hanser",
            kind="style_prompt",
            path="neutral.wav",
            transcript="这是中性参考。",
            review_status="approved",
            approved_languages=["zh"],
        ),
    }


def snapshot(text: str = "你好。") -> ReplySnapshot:
    return ReplySnapshot(
        request_id="request-1",
        reply_id="reply-1",
        user_id="owner-1",
        conversation_id="conversation-1",
        semantic_text=text,
        display_text=text,
        allowed_performance=allowed(),
        output_preferences=OutputPreferences(speech=True),
        allow_tts=True,
        language="zh",
        constraints_ref="constraints:test",
        render_profile_revision="render-1",
        text_source="validated_semantic",
    )


def test_speech_planner_keeps_program_generated_source_map() -> None:
    plan = SpeechPlanner().plan(
        "reply-1",
        "哈哈，哈哈。见[说明](https://example.test)，日期2026-09-09🙂。`x`",
        allowed(),
    )
    assert plan.speech_text.count("哈哈") == 2
    assert "https://" not in plan.speech_text
    assert "二零二六年九月九日" in plan.speech_text
    assert "🙂" not in plan.speech_text
    assert "`x`" not in plan.speech_text
    assert all(item.semantic_span[0] <= item.semantic_span[1] for item in plan.source_map)


def test_engine_adapter_exposes_control_and_spoken_ranges() -> None:
    segment = SpeechPlanner().plan("reply-1", "你好。", allowed()).segments[0]
    active_profile = profile()
    voice = VoiceCapabilityMapper().resolve(allowed(), active_profile)
    voice_plan = VoiceAssetSelector().select(
        segment, voice, active_profile, assets()
    )
    identity = EngineTextAdapter().compile(segment.speech_text, voice_plan)
    assert identity.engine_text == segment.speech_text
    assert identity.control_spans == []
    assert identity.spoken_engine_spans == [(0, len(segment.speech_text))]


def test_audio_cache_identity_excludes_planned_gap_and_reply_id() -> None:
    active_profile = profile()
    plans = SpeechPlanner()
    first = plans.plan("reply-a", "相同文本。", allowed()).segments[0]
    second = plans.plan("reply-b", "相同文本。", allowed()).segments[0]
    second.target_gap_ms = 900
    capability = VoiceCapabilityMapper().resolve(allowed(), active_profile)
    first_voice = VoiceAssetSelector().select(first, capability, active_profile, assets())
    second_voice = VoiceAssetSelector().select(second, capability, active_profile, assets())
    adapter = EngineTextAdapter()
    first_key, first_seed = generation_identity(
        profile=active_profile,
        voice_plan=first_voice,
        engine_text=adapter.compile(first.speech_text, first_voice),
        variant_salt=None,
    )
    second_key, second_seed = generation_identity(
        profile=active_profile,
        voice_plan=second_voice,
        engine_text=adapter.compile(second.speech_text, second_voice),
        variant_salt=None,
    )
    assert (first_key, first_seed) == (second_key, second_seed)


def test_cross_modal_filter_caps_strong_visual_with_neutral_voice() -> None:
    requested = AllowedPerformance(
        delivery=Delivery.EXCITED,
        intensity=0.9,
        constraints_ref="constraints:test",
    )
    active_profile = profile()
    rig = RigProfile(
        profile_id="rig",
        revision="rig-1",
        enabled=True,
        status="validated",
        model_path="placeholder.model3.json",
        presets={
            Delivery.NEUTRAL: RigPreset(preset="neutral", max_weight=0.2),
            Delivery.EXCITED: RigPreset(
                preset="excited",
                max_weight=0.9,
                motion="large_bounce",
                feature_tags=["large_motion"],
            ),
        },
    )
    voice = VoiceCapabilityMapper().resolve(requested, active_profile)
    visual = VisualCapabilityMapper().resolve(requested, rig)
    filtered = CrossModalCompatibility().filter(voice, visual)
    assert voice.family == "neutral"
    assert filtered.weight == 0.25
    assert filtered.motion == "none"
    assert "neutral_voice_strong_visual_filtered" in filtered.degraded_reasons


def test_candidate_runtime_exposes_control_plane_without_loading_model() -> None:
    project_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory() as temporary:
        settings = RuntimeSettings(
            data_root=Path(temporary),
            voice_profile=project_root
            / "voice_runtime/config/voice_profile.candidate.yml",
            rig_profile=project_root
            / "voice_runtime/config/rig_profile.candidate.yml",
            load_voice_model=False,
        )
        with TestClient(create_app(settings=settings)) as client:
            runtime = client.get("/runtime")
            assert runtime.status_code == 200
            assert runtime.json()["control_ready"] is True
            assert runtime.json()["voice_ready"] is False
            assert runtime.json()["visual_ready"] is False
            assert runtime.json()["model_loaded"] is False

            unavailable = client.post(
                "/internal/v1/jobs",
                headers={"X-Hanser-Owner": "owner-1"},
                json=VoiceJobRequest(
                    owner="owner-1",
                    snapshot=snapshot(),
                    mode="interactive",
                ).model_dump(mode="json"),
            )
            assert unavailable.status_code == 503

            invalid_cursor = client.get(
                "/internal/v1/jobs/missing/events",
                headers={
                    "X-Hanser-Owner": "owner-1",
                    "Last-Event-ID": "not-an-integer",
                },
            )
            assert invalid_cursor.status_code == 400


def test_gpt_sovits_profile_rejects_non_loopback_endpoint() -> None:
    payload = gpt_sovits_profile().model_dump(mode="json")
    payload["gpt_sovits"]["endpoint"] = "https://example.test"
    with pytest.raises(ValueError, match="loopback HTTP origin"):
        VoiceProfile.model_validate(payload)


def test_gpt_sovits_adapter_posts_exact_prompt_and_resamples_to_48k() -> None:
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed.update(json.loads(request.content))
        buffer = BytesIO()
        samples = np.sin(np.linspace(0, 30, 3200, dtype=np.float32)) * 0.1
        sf.write(buffer, samples, 32_000, format="WAV", subtype="PCM_16")
        return httpx.Response(
            200,
            content=buffer.getvalue(),
            headers={"content-type": "audio/wav"},
        )

    active_profile = gpt_sovits_profile()
    segment = SpeechPlanner().plan("reply-1", "你好。", allowed()).segments[0]
    voice = VoiceCapabilityMapper().resolve(allowed(), active_profile)
    voice_plan = VoiceAssetSelector().select(
        segment, voice, active_profile, assets()
    )
    engine_text = EngineTextAdapter().compile(segment.speech_text, voice_plan)
    backend = GPTSoVITSV2ProBackend(
        active_profile, transport=httpx.MockTransport(handler)
    )
    result = backend.synthesize(
        SynthesisRequest(
            engine_text=engine_text,
            voice_plan=voice_plan,
            seed=1234,
            cfg_value=2.0,
            inference_timesteps=10,
        )
    )
    backend.close()

    assert observed["text"] == "你好。"
    assert observed["ref_audio_path"] == "neutral.wav"
    assert observed["prompt_text"] == "这是中性参考。"
    assert observed["streaming_mode"] == 0
    assert observed["seed"] == 1234
    assert result.sample_rate == 48_000
    assert 4_790 <= len(result.pcm) <= 4_810
    assert result.backend_metadata["native_sample_rate"] == 32_000


class _Backend:
    model_revision = "model-1"

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.calls = 0

    def synthesize(self, request):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        samples = np.sin(np.linspace(0, 60, 4800, dtype=np.float32)) * 0.1
        return SynthesisResult(
            pcm=samples,
            sample_rate=48_000,
            backend_metadata={"seed": request.seed},
        )


@pytest.mark.asyncio
async def test_job_is_idempotent_and_publishes_one_terminal_event() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        backend = _Backend()
        manager = VoiceJobManager(
            data_root=temporary,
            profile=profile(),
            rig=RigProfile(profile_id="rig", revision="rig-1"),
            assets=assets(),
            backend=backend,
        )
        await manager.start()
        request = VoiceJobRequest(owner="owner-1", snapshot=snapshot(), mode="offline")
        first, created = await manager.create(request)
        second, created_again = await manager.create(request)
        assert created is True
        assert created_again is False
        assert second.job_id == first.job_id
        events, terminal = await manager.wait_for_events(
            first.job_id, "owner-1", 0, timeout=3
        )
        if not terminal:
            events, terminal = await manager.wait_for_events(
                first.job_id, "owner-1", events[-1].sequence, timeout=3
            )
        final = await manager.get(first.job_id, "owner-1")
        await manager.close()
        assert terminal is True
        assert final.status == JobStatus.COMPLETED
        assert backend.calls == 1
        all_events = await manager.events_after(first.job_id, "owner-1", 0)
        assert sum(item.type == "turn.completed" for item in all_events) == 1
        assert final.ready_segments[0].segment_duration_samples == (
            final.ready_segments[0].audio.sample_count
            + final.ready_segments[0].timeline.pause_after_samples
        )


@pytest.mark.asyncio
async def test_cancel_wins_over_late_backend_result() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        manager = VoiceJobManager(
            data_root=temporary,
            profile=profile(),
            rig=RigProfile(profile_id="rig", revision="rig-1"),
            assets=assets(),
            backend=_Backend(delay=0.2),
        )
        await manager.start()
        created, _ = await manager.create(
            VoiceJobRequest(owner="owner-1", snapshot=snapshot(), mode="offline")
        )
        await manager.wait_for_events(created.job_id, "owner-1", 0, timeout=1)
        cancelled = await manager.cancel(created.job_id, "owner-1")
        await asyncio.sleep(0.3)
        final = await manager.get(created.job_id, "owner-1")
        await manager.close()
        assert cancelled.status == JobStatus.CANCELLED
        assert final.status == JobStatus.CANCELLED
        assert final.ready_segments == []
        events = await manager.events_after(created.job_id, "owner-1", 0)
        assert events[-1].type == "turn.cancelled"
        assert sum(item.type.startswith("turn.") and item.type != "turn.started" for item in events) == 1


@pytest.mark.asyncio
async def test_missing_playback_receipt_releases_the_worker_at_deadline() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        manager = VoiceJobManager(
            data_root=temporary,
            profile=profile(),
            rig=RigProfile(profile_id="rig", revision="rig-1"),
            assets=assets(),
            backend=_Backend(),
            max_ready_segments=1,
            interactive_deadline_seconds=0.05,
        )
        await manager.start()
        first, _ = await manager.create(
            VoiceJobRequest(
                owner="owner-1",
                snapshot=snapshot("第一段内容。" * 20 + "第二段内容。" * 20),
                mode="interactive",
            )
        )
        second_request = VoiceJobRequest(
            owner="owner-1",
            snapshot=snapshot("后续任务。").model_copy(
                update={"request_id": "request-2", "reply_id": "reply-2"}
            ),
            mode="offline",
        )
        second, _ = await manager.create(second_request)
        await asyncio.sleep(0.2)
        first_final = await manager.get(first.job_id, "owner-1")
        second_final = await manager.get(second.job_id, "owner-1")
        await manager.close()
        assert first_final.status == JobStatus.FAILED
        assert first_final.terminal_reason == "voice_job_deadline_exceeded"
        assert second_final.status == JobStatus.COMPLETED
