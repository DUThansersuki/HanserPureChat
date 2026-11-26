from __future__ import annotations

from pathlib import Path

import pytest

from hanser_agent.agent.context_builder import ContextBundle
from hanser_agent.config import ModelProfileConfig
from hanser_agent.models import ChatMessage
from hanser_agent.persona import PersonaCompiler
from hanser_agent.responder import HanserResponder, StyleValidator
from hanser_agent.responder.performance import (
    Delivery,
    OutputPreferences,
    PerformanceIntent,
    effective_preferences,
)


PERSONA_DIR = (
    Path(__file__).parents[1]
    / "hanser_agent"
    / "prompts"
    / "persona"
    / "candidates"
    / "hanser-persona-v2-candidate"
)


class FakeGateway:
    def __init__(self, output: str):
        self.output = output
        self.calls = 0
        self.profiles = {"responder": ModelProfileConfig(model="fake-responder")}

    async def generate(self, profile, messages, *, json_object=False):
        self.calls += 1
        return self.output


def test_effective_preferences_keep_text_only_and_enable_requested_speech() -> None:
    text_only = effective_preferences(
        OutputPreferences(speech=False),
        structured_enabled=True,
        speech_enabled=True,
        dynamic_live2d_enabled=False,
        offline_export_enabled=False,
    )
    speech = effective_preferences(
        OutputPreferences(speech=True),
        structured_enabled=True,
        speech_enabled=True,
        dynamic_live2d_enabled=False,
        offline_export_enabled=False,
    )

    assert text_only == OutputPreferences()
    assert speech == OutputPreferences(speech=True)


@pytest.mark.asyncio
async def test_bad_performance_does_not_retry_or_replace_semantic_text() -> None:
    gateway = FakeGateway(
        '{"semantic_text":"收到。","performance":'
        '{"schema_version":"1.1","delivery":"gentle","intensity":"bad"}}'
    )
    responder = HanserResponder(
        gateway,
        StyleValidator(PERSONA_DIR / "style_constraints.yaml"),
        structured_performance_enabled=True,
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
        delivery=Delivery.GENTLE,
        intensity=0.2,
    )
    assert "performance_intensity_invalid" in result.performance_degraded_reasons


@pytest.mark.asyncio
async def test_structured_responder_preserves_exact_output_in_same_call() -> None:
    exact = "逐字保留：A_B + `code` 😊"
    gateway = FakeGateway(
        '{"semantic_text":"逐字保留：A_B + `code` 😊",'
        '"performance":{"schema_version":"1.1","delivery":"neutral","intensity":0.2}}'
    )
    responder = HanserResponder(
        gateway,
        StyleValidator(PERSONA_DIR / "style_constraints.yaml"),
        structured_performance_enabled=True,
    )
    context = ContextBundle(
        messages=[ChatMessage(role="user", content="请逐字输出")],
        persona=PersonaCompiler(PERSONA_DIR).compile("casual"),
        structured_performance=True,
        exact_output=exact,
        required_verbatim_spans=[exact],
    )

    result = await responder.respond(context, structured_performance=True)

    assert gateway.calls == 1
    assert result.semantic_text == exact
    assert result.text == exact
