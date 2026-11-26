from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx

from hanser_agent.agent.planner import DialoguePlanner, PlannerDecision
from hanser_agent.agent.service import ChatAgentService
from hanser_agent.config import MemoryConfig, ModelProfileConfig, load_settings
from hanser_agent.memory.retriever import MemoryRetriever
from hanser_agent.model_gateway import ModelGateway
from hanser_agent.models import ChatMessage, MemoryItem
from hanser_agent.retrieval import bm25_scores
from hanser_agent.agent.tools.style_search import StyleSearchTool


def write_config(root: Path, provider: str = "openai_compatible") -> Path:
    path = root / "config.yml"
    path.write_text(
        f"""
data:
  db_path: documents.db
  data_dir: data
  userdict_path: userdict.txt
models:
  planner:
    provider: {provider}
  responder:
    provider: {provider}
llm:
  base_url: https://example.invalid/v1
  api_key: test-key
  model: external-model
""".strip(),
        encoding="utf-8",
    )
    return path


class AlwaysWikiGateway:
    async def generate_json_with_status(self, profile_name, messages, schema):
        del profile_name, messages, schema
        return (
            PlannerDecision(
                intent="wiki_fact",
                wiki=True,
                memory=True,
                query=None,
                keywords=["直播"],
                mode="factual",
                sensitivity="high",
                length="short",
            ),
            [],
        )


class AlwaysChitchatGateway:
    async def generate_json_with_status(self, profile_name, messages, schema):
        del profile_name, messages, schema
        return (
            PlannerDecision(
                intent="chitchat",
                mode="casual",
                length="short",
            ),
            [],
        )


class MemoryFixtureStore:
    def __init__(self, memories: list[MemoryItem]):
        self.memories = memories
        self.accessed: list[str] = []

    def eligible_memory_ids(self, *, user_id, conversation_id):
        del conversation_id
        return [item.id for item in self.memories if item.user_id == user_id]

    def active_memories_by_ids(self, *, user_id, memory_ids):
        allowed = set(memory_ids)
        return [
            item
            for item in self.memories
            if item.user_id == user_id and item.id in allowed
        ]

    def mark_accessed(self, memory_ids):
        self.accessed.extend(memory_ids)


class ExternalOnlyRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_settings_reject_local_model_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "openai_compatible"):
                load_settings(write_config(root, provider="ollama"))

    def test_settings_bind_planner_and_responder_to_external_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = load_settings(write_config(Path(tmp)))
        self.assertEqual(settings.planner.provider, "openai_compatible")
        self.assertEqual(settings.responder.provider, "openai_compatible")
        self.assertEqual(settings.planner.model, "external-model")
        self.assertEqual(settings.responder.model, "external-model")

    def test_settings_reject_unknown_loopback_model_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_config(root)
            payload = path.read_text(encoding="utf-8").replace(
                "https://example.invalid/v1",
                "http://127.0.0.1:11435/v1",
            )
            path.write_text(payload, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "loopback"):
                load_settings(path)

    async def test_topic_words_alone_cannot_open_wiki_route(self) -> None:
        planner = DialoguePlanner(AlwaysWikiGateway())
        for message in (
            "我刚看完你的直播",
            "这个作品挺好听",
            "这个我也喜欢",
        ):
            plan = await planner.plan(message, [])
            self.assertFalse(plan.need_wiki, message)
            self.assertEqual(plan.keywords, [], message)

        explicit = await planner.plan("你有哪些作品？", [])
        self.assertTrue(explicit.need_wiki)

    async def test_persona_story_requests_open_wiki_route(self) -> None:
        planner = DialoguePlanner(AlwaysChitchatGateway())
        for message in (
            "讲讲你最近印象很深的一件事",
            "就聊聊你嘛 讲讲之前的故事也可以",
            "聊聊你的经历",
            "你讲讲以前的故事",
        ):
            plan = await planner.plan(message, [])
            self.assertTrue(plan.need_wiki, message)
            self.assertEqual(plan.response_mode, "factual", message)

        shared_by_user = await planner.plan("我跟你讲讲最近一件事", [])
        self.assertFalse(shared_by_user.need_wiki)

    async def test_semantic_fact_intent_can_open_non_lexical_wiki_route(self) -> None:
        planner = DialoguePlanner(AlwaysWikiGateway())
        plan = await planner.plan("你平时最喜欢吃什么？", [])
        self.assertTrue(plan.need_wiki)

    def test_style_overlap_rejects_generic_time_and_connector_words(self) -> None:
        for token in ("最近", "之前", "可以", "也可", "的一"):
            self.assertFalse(
                StyleSearchTool._meaningful_content_overlap({token}, {token}),
                token,
            )
        self.assertTrue(
            StyleSearchTool._meaningful_content_overlap(
                {"最近", "失眠"},
                {"之前", "失眠"},
            )
        )

    def test_lexical_bm25_uses_full_document_length(self) -> None:
        scores = bm25_scores(
            "失眠",
            {
                "short": ("失眠",),
                "long": ("失眠", *(f"无关{index}" for index in range(100))),
            },
        )
        self.assertGreater(scores["short"], scores["long"])

    async def test_fact_followup_keeps_wiki_route(self) -> None:
        planner = DialoguePlanner(AlwaysWikiGateway())
        history = [
            ChatMessage(role="user", content="Hanser什么时候退出VirtuaReal？"),
            ChatMessage(role="assistant", content="在某个日期"),
        ]
        plan = await planner.plan("那是为什么？", history)
        self.assertTrue(plan.need_wiki)

    async def test_gateway_diagnostics_are_bounded_and_capture_is_per_task(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gateway = ModelGateway(
            {
                "responder": ModelProfileConfig(
                    endpoint="https://example.invalid/v1",
                    model="external-model",
                    api_key="test-key",
                )
            },
            client=client,
        )
        token = gateway.start_call_capture()
        for _ in range(gateway._RECORD_LIMIT + 10):
            await gateway.generate(
                "responder",
                [ChatMessage(role="user", content="hello")],
            )
        captured = gateway.finish_call_capture(token)
        self.assertEqual(len(gateway.call_records), gateway._RECORD_LIMIT)
        self.assertEqual(len(captured), gateway._RECORD_LIMIT + 10)
        await client.aclose()

    async def test_user_lock_entries_are_released(self) -> None:
        service = ChatAgentService.__new__(ChatAgentService)
        service._user_locks = {}

        async def fake_send(request):
            return request.user_id

        service._send = fake_send
        for index in range(300):
            result = await service.send(SimpleNamespace(user_id=f"user-{index}"))
            self.assertEqual(result, f"user-{index}")
        self.assertEqual(service._user_locks, {})

    async def test_memory_token_cache_is_bounded(self) -> None:
        now = datetime.now(timezone.utc)
        store = MemoryFixtureStore([
            MemoryItem(
                id=f"memory-{index}",
                user_id="user",
                type="user_preference",
                memory_key=f"drink:{index}",
                content=f"我喜欢咖啡 {index}",
                importance=0.8,
                confidence=0.9,
                created_at=now,
            )
            for index in range(300)
        ])
        retriever = MemoryRetriever(store=store, config=MemoryConfig())
        results = await retriever.search(query="喜欢咖啡", user_id="user")
        self.assertTrue(results)
        self.assertLessEqual(
            len(retriever._token_cache),
            retriever._TOKEN_CACHE_LIMIT,
        )


if __name__ == "__main__":
    unittest.main()
