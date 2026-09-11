from __future__ import annotations

import tempfile
from pathlib import Path

import httpx
import pytest

from hanser_agent import db
from hanser_agent.agent.context_builder import ContextBundle
from hanser_agent.agent.conversation import ConversationOwnershipError, ConversationStore
from hanser_agent.config import ModelProfileConfig, PerformanceConfig
from hanser_agent.models import ChatMessage, ChatRequest, ChatResponse
from hanser_agent.persona import PersonaCompiler
from hanser_agent.persona.schemas import BehaviorDecision, ExpressionCaps
from hanser_agent.responder import HanserResponder, StyleValidator
from hanser_agent.responder.performance import (
    AllowedPerformance,
    Delivery,
    OutputPreferences,
    PerformanceIntent,
    ReplySnapshot,
    parse_performance,
)
from hanser_agent.responder.performance_policy import PerformancePolicyResolver
from hanser_agent.render_bridge import RenderBridge, RenderBridgeFailure
from tests.test_context_and_persona import PERSONA_DIR


class _Gateway:
    def __init__(self, output: str):
        self.output = output
        self.calls = 0
        self.profiles = {"responder": ModelProfileConfig(model="test")}

    async def generate(self, profile, messages):
        self.calls += 1
        return self.output


@pytest.mark.asyncio
async def test_bad_performance_field_does_not_retry_valid_semantic_text() -> None:
    gateway = _Gateway(
        '{"semantic_text":"收到。","performance":'
        '{"schema_version":"1.1","delivery":"gentle","intensity":"0.8"}}'
    )
    responder = HanserResponder(
        gateway,
        StyleValidator(PERSONA_DIR / "style_constraints.yaml"),
    )
    context = ContextBundle(
        messages=[ChatMessage(role="user", content="知道了")],
        persona=PersonaCompiler(PERSONA_DIR).compile("casual"),
        structured_performance=True,
    )

    result = await responder.respond(context, structured_performance=True)

    assert gateway.calls == 1
    assert result.semantic_text == "收到。"
    assert result.performance == PerformanceIntent(
        delivery=Delivery.GENTLE, intensity=0.2
    )
    assert "performance_intensity_invalid" in result.performance_degraded_reasons


def test_unknown_performance_schema_degrades_without_guessing() -> None:
    result = parse_performance(
        {"schema_version": "2.0", "delivery": "excited", "intensity": 0.9}
    )
    assert result.intent is None
    assert result.degraded_reasons == ["performance_schema_unsupported"]


def test_policy_applies_integer_persona_cap_as_versioned_boundary() -> None:
    decision = BehaviorDecision(
        expression_caps=ExpressionCaps(
            hard_intensity_limits={"teasing": 1}
        ),
        boundary_ids=["boundary:no-overacting"],
    )
    resolved = PerformancePolicyResolver().resolve(
        PerformanceIntent(delivery=Delivery.TEASING, intensity=0.9), decision
    )
    assert resolved.delivery == Delivery.TEASING
    assert resolved.intensity == 0.35
    assert "intensity_capped:teasing:0.35" in resolved.decisions


def test_legacy_chat_json_omits_extension_fields() -> None:
    request = ChatRequest(message="hello")
    response = ChatResponse(text="world")
    assert request.model_dump(exclude_defaults=True) == {"message": "hello"}
    assert response.model_dump(exclude_defaults=True) == {"text": "world"}


def test_reply_snapshot_is_atomic_with_assistant_message_and_owner_checked() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "test.db"
        with db.connect(path) as connection:
            db.init_db(connection)
        store = ConversationStore(path)
        snapshot = ReplySnapshot(
            request_id="request-1",
            reply_id="assistant-1",
            user_id="owner-1",
            conversation_id="conversation-1",
            semantic_text="你好。",
            display_text="你好",
            allowed_performance=AllowedPerformance(),
            output_preferences=OutputPreferences(speech=True),
            allow_tts=True,
            language="zh",
            constraints_ref="constraints:test",
            render_profile_revision="render-test",
            text_source="validated_semantic",
        )
        store.append_turn(
            conversation_id="conversation-1",
            user_id="owner-1",
            user_text="你好",
            assistant_text="你好",
            model_name="test",
            trace_id="trace-1",
            persona_version="test",
            user_message_id="user-1",
            assistant_message_id="assistant-1",
            reply_snapshot=snapshot,
            response=ChatResponse(text="你好", reply_id="assistant-1"),
            request_hash="hash-1",
            post_turn_payload={"message": "你好"},
        )
        assert store.get_reply_snapshot("assistant-1", user_id="owner-1") == snapshot
        with pytest.raises(ConversationOwnershipError):
            store.get_reply_snapshot("assistant-1", user_id="owner-2")


def test_render_bridge_projects_persona_trace_out_of_runtime_snapshot() -> None:
    snapshot = ReplySnapshot(
        request_id="request-1",
        reply_id="assistant-1",
        user_id="owner-1",
        conversation_id="conversation-1",
        semantic_text="你好。",
        display_text="你好",
        allowed_performance=AllowedPerformance(),
        output_preferences=OutputPreferences(speech=True, dynamic_live2d=True),
        allow_tts=True,
        language="zh",
        constraints_ref="constraints:test",
        render_profile_revision="render-test",
        text_source="validated_semantic",
        persona_trace={"debug": {"large": "payload"}},
    )

    projected = RenderBridge._runtime_snapshot(snapshot)

    assert "persona_trace" not in projected
    assert projected["output_preferences"] == {
        "text": True,
        "speech": True,
        "dynamic_live2d": True,
        "offline_performance": False,
    }


@pytest.mark.asyncio
async def test_streaming_runtime_error_is_read_before_forwarding() -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "artifact not found"})

    with tempfile.TemporaryDirectory() as temporary:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(respond),
            base_url="http://runtime.test",
        )
        bridge = RenderBridge(
            ConversationStore(Path(temporary) / "conversation.db"),
            PerformanceConfig(),
            client=client,
        )
        with pytest.raises(RenderBridgeFailure) as captured:
            await bridge.stream_artifact(
                "missing-artifact",
                "owner-1",
                range_header=None,
            )
        await client.aclose()
        assert captured.value.status_code == 404
        assert captured.value.message == "artifact not found"
