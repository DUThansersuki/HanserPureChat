import json
import unittest
from pathlib import Path

import httpx

from hanser_agent.config import ModelProfileConfig
from hanser_agent.model_gateway import ModelGateway
from hanser_agent.models import ChatMessage
from hanser_agent.models import DialoguePlan
from hanser_agent.agent.context_builder import ContextBuilder
from hanser_agent.persona import PersonaCompiler


class StructuredWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_history_does_not_truncate_current_request_capture(self):
        profile = ModelProfileConfig(provider="openai_compatible", model="test")
        async with httpx.AsyncClient() as client:
            gateway = ModelGateway({"responder": profile}, client=client, record_limit=256)
            for _ in range(300):
                gateway._record_call(profile, "text", {}, 0.01)
            self.assertEqual(len(gateway.call_records), 256)
            token = gateway.start_call_capture()
            for index in range(3):
                gateway._record_call(profile, "text", {"usage": {"total_tokens": index + 1}}, 0.01)
            captured = gateway.finish_call_capture(token)
            self.assertEqual([row["total_tokens"] for row in captured], [1, 2, 3])
            self.assertEqual(len(gateway.call_records), 256)
            full = ModelGateway({"responder": profile}, client=client, record_limit=None)
            for _ in range(300):
                full._record_call(profile, "text", {}, 0.01)
            self.assertEqual(len(full.call_records), 300)

    def test_plain_history_is_encoded_for_structured_request_without_mutation(self):
        compiler = PersonaCompiler(Path(__file__).resolve().parents[1] / "hanser_agent/prompts/persona")
        builder = ContextBuilder(compiler)
        history = [ChatMessage(role="user", content="过去的问题"),
                   ChatMessage(role="assistant", content='含有"引号"的旧回复', message_id="past")]
        plan = DialoguePlan(intent="chitchat", need_wiki=False, standalone_query="本轮", keywords=[],
                            response_mode="casual", fact_sensitivity="low", target_length="short")
        context = builder.build(current_message="本轮", history=history, plan=plan, structured_performance=True)
        assistant = next(message for message in context.messages if message.role == "assistant")
        self.assertEqual(json.loads(assistant.content), {"semantic_text": history[1].content})
        self.assertEqual(assistant.message_id, "past")
        self.assertEqual(history[1].content, '含有"引号"的旧回复')
        plain = builder.build(current_message="本轮", history=history, plan=plan)
        self.assertEqual(next(message.content for message in plain.messages if message.role == "assistant"), history[1].content)
        self.assertLessEqual(context.estimated_input_tokens, context.input_token_budget)

    async def test_provider_receives_json_mode_only_for_structured_output(self):
        requests = []

        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"semantic_text":"收到"}'}}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            gateway = ModelGateway({"responder": ModelProfileConfig(provider="openai_compatible", endpoint="https://example.test/v1", model="test")}, client=client)
            messages = [ChatMessage(role="user", content="输出 JSON")]
            await gateway.generate("responder", messages)
            await gateway.generate("responder", messages, json_object=True)
        self.assertNotIn("response_format", requests[0])
        self.assertEqual(requests[1]["response_format"], {"type": "json_object"})
