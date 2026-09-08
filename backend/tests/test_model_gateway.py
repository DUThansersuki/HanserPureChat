from __future__ import annotations

import json
import unittest

import httpx

from hanser_agent.config import ModelProfileConfig
from hanser_agent.model_gateway import ModelGateway
from hanser_agent.models import ChatMessage, DialoguePlan


class ModelGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_deepseek_profile_explicitly_controls_thinking_mode(self) -> None:
        captured = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "晚上好"}}]},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gateway = ModelGateway(
            {
                "responder": ModelProfileConfig(
                    provider="openai_compatible",
                    endpoint="https://api.deepseek.test",
                    model="deepseek-v4-flash",
                    api_key="test",
                    think=False,
                )
            },
            client=client,
        )
        await gateway.generate(
            "responder",
            [ChatMessage(role="user", content="晚上好")],
        )
        await client.aclose()

        self.assertEqual(captured["thinking"], {"type": "disabled"})

    async def test_plain_generation_uses_named_responder_profile(self) -> None:
        captured = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={"message": {"content": "晚上好呀"}},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gateway = ModelGateway(
            {
                "responder": ModelProfileConfig(
                    model="local-responder",
                    temperature=0.65,
                    max_tokens=256,
                    think=False,
                )
            },
            client=client,
        )
        result = await gateway.generate(
            "responder",
            [ChatMessage(role="user", content="晚上好")],
        )
        await client.aclose()

        self.assertEqual(result, "晚上好呀")
        self.assertEqual(captured["model"], "local-responder")
        self.assertEqual(captured["options"]["num_predict"], 256)
        self.assertEqual(captured["options"]["temperature"], 0.65)

    async def test_ollama_uses_schema_and_resource_limits(self) -> None:
        captured = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            plan = DialoguePlan(
                intent="wiki_fact",
                need_wiki=True,
                standalone_query="Hanser什么时候退出VirtuaReal",
                keywords=["Hanser", "VirtuaReal", "退出"],
                response_mode="factual",
                fact_sensitivity="high",
                target_length="medium",
            )
            return httpx.Response(
                200,
                json={"message": {"content": plan.model_dump_json()}},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gateway = ModelGateway(
            {
                "planner": ModelProfileConfig(
                    model="local-planner",
                    context_window=4096,
                    keep_alive="5m",
                    think=False,
                )
            },
            client=client,
        )
        result = await gateway.generate_json(
            "planner",
            [ChatMessage(role="user", content="什么时候退出")],
            DialoguePlan,
        )
        await client.aclose()

        self.assertEqual(result.intent, "wiki_fact")
        self.assertEqual(captured["model"], "local-planner")
        self.assertEqual(captured["format"]["type"], "object")
        self.assertEqual(captured["options"]["num_ctx"], 4096)
        self.assertEqual(captured["keep_alive"], "5m")
        self.assertFalse(captured["think"])

    async def test_ollama_profile_can_be_unloaded(self) -> None:
        captured = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={"done": True})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gateway = ModelGateway(
            {"planner": ModelProfileConfig(model="local-planner")},
            client=client,
        )
        await gateway.unload("planner")
        await client.aclose()

        self.assertEqual(captured["path"], "/api/generate")
        self.assertEqual(captured["model"], "local-planner")
        self.assertEqual(captured["keep_alive"], 0)

    async def test_configured_external_fallback_is_single_step(self) -> None:
        calls = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path == "/api/chat":
                return httpx.Response(500, text="local unavailable")
            plan = DialoguePlan(
                intent="chitchat",
                need_wiki=False,
                standalone_query="晚上好",
                keywords=[],
                response_mode="casual",
                fact_sensitivity="low",
                target_length="short",
            )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": plan.model_dump_json()}}
                    ]
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gateway = ModelGateway(
            {
                "planner": ModelProfileConfig(
                    fallback_profile="planner_external"
                ),
                "planner_external": ModelProfileConfig(
                    provider="openai_compatible",
                    endpoint="https://example.test/v1",
                    model="external-planner",
                    api_key="test",
                ),
            },
            client=client,
        )
        result, degraded_reasons = await gateway.generate_json_with_status(
            "planner",
            [ChatMessage(role="user", content="晚上好")],
            DialoguePlan,
        )
        await client.aclose()

        self.assertEqual(result.intent, "chitchat")
        self.assertEqual(
            degraded_reasons, ["planner_primary_provider_fallback"]
        )
        self.assertEqual(calls, ["/api/chat", "/v1/chat/completions"])
        self.assertEqual(len(gateway.call_records), 2)
        self.assertEqual(gateway.call_records[0]["status"], "failed")
        self.assertEqual(gateway.call_records[0]["model"], "qwen3.5:4b")
        self.assertEqual(gateway.call_records[1]["status"], "success")
        self.assertEqual(gateway.call_records[1]["model"], "external-planner")


if __name__ == "__main__":
    unittest.main()
