from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from hanser_agent import db
from hanser_agent.agent.context_builder import ContextBuilder, ContextBundle
from hanser_agent.agent.conversation import ConversationStore
from hanser_agent.agent.request_state import RequestStateStore
from hanser_agent.agent.service import ChatAgentService
from hanser_agent.agent.tools.wiki_search import WikiSearchTool
from hanser_agent.api import create_app
from hanser_agent.config import (
    HybridRetrievalConfig,
    LLMTaskConfig,
    MemoryConfig,
    ModelProfileConfig,
    RerankerConfig,
    Settings,
)
from hanser_agent.failures import ServiceFailure
from hanser_agent.memory import (
    MemoryCandidateExtractor,
    MemoryRetriever,
    MemoryStore,
    MemoryWriteGate,
    PostTurnPipeline,
)
from hanser_agent.memory.state_engine import CharacterStateEngine
from hanser_agent.memory.summarizer import ConversationSummarizer
from hanser_agent.model_gateway import ModelGateway
from hanser_agent.models import (
    ChatMessage,
    ChatRequest,
    DialoguePlan,
    RelationshipState,
    SceneState,
)
from hanser_agent.persona import PersonaCompiler
from hanser_agent.responder import GeneratedResponse, HanserResponder, StyleValidator
from hanser_agent.retrieval import HybridRetriever, SQLiteVectorStore
from tests.test_context_and_persona import PERSONA_DIR
from tests.test_reranker import FakeHybridRetriever


def settings_for(path: Path) -> Settings:
    task = LLMTaskConfig("unused", 64, 0.1, 0.9)
    return Settings(
        root=path.parent,
        db_path=path,
        data_dir=path.parent,
        userdict_path=path.parent / "userdict.txt",
        base_url="https://example.test/v1",
        api_key="test",
        default_model="unused",
        bunny=task,
        prometheus=task,
        hanser=task,
        reranker=RerankerConfig(top_k=1),
    )


class ResponderFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_200_empty_retries_same_responder_then_succeeds(self) -> None:
        replies = ["", "晚上好"]

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": replies.pop(0)}}]},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gateway = ModelGateway(
            {
                "responder": ModelProfileConfig(
                    provider="openai_compatible",
                    endpoint="https://example.test/v1",
                    model="test",
                )
            },
            client=client,
        )
        responder = HanserResponder(gateway, StyleValidator(PERSONA_DIR / "style_constraints.yaml"))
        result = await responder.respond(
            ContextBundle(
                messages=[ChatMessage(role="user", content="晚上好")],
                persona=PersonaCompiler(PERSONA_DIR).compile("casual"),
            )
        )
        await client.aclose()
        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.text, "晚上好")

    async def test_repeated_empty_is_structured_failure_not_unknown_answer(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gateway = ModelGateway(
            {"responder": ModelProfileConfig(provider="openai_compatible", endpoint="https://example.test/v1", model="test")},
            client=client,
        )
        responder = HanserResponder(gateway, StyleValidator(PERSONA_DIR / "style_constraints.yaml"))
        with self.assertRaises(ServiceFailure) as raised:
            await responder.respond(
                ContextBundle(
                    messages=[ChatMessage(role="user", content="我考上啦")],
                    persona=PersonaCompiler(PERSONA_DIR).compile("casual"),
                )
            )
        await client.aclose()
        self.assertEqual(raised.exception.code, "empty_provider_output")
        self.assertNotIn("不知道", raised.exception.safe_message)

    async def test_provider_timeout_has_stable_error_code(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("injected", request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        gateway = ModelGateway(
            {"responder": ModelProfileConfig(provider="openai_compatible", endpoint="https://example.test/v1", model="test")},
            client=client,
        )
        with self.assertRaises(ServiceFailure) as raised:
            await gateway.generate("responder", [ChatMessage(role="user", content="hi")])
        await client.aclose()
        self.assertEqual(raised.exception.code, "provider_timeout")
        self.assertEqual(raised.exception.status_code, 504)


class StructuredFailureApiTests(unittest.TestCase):
    def test_service_failure_is_returned_as_structured_api_error(self) -> None:
        class FailedAgent:
            async def send(self, request):
                raise ServiceFailure(
                    "provider_timeout",
                    "responder timed out",
                    retryable=True,
                    status_code=504,
                    trace_id="trace-1",
                )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.db"
            app = create_app(settings=settings_for(path), chat_agent=FailedAgent())
            with TestClient(app) as client:
                response = client.post("/v1/chat", json={"message": "hi"})
        self.assertEqual(response.status_code, 504)
        self.assertEqual(
            response.json()["detail"],
            {
                "code": "provider_timeout",
                "message": "responder timed out",
                "retryable": True,
                "trace_id": "trace-1",
            },
        )


class RetrievalDegradeTests(unittest.IsolatedAsyncioTestCase):
    async def test_reranker_failure_preserves_sources_and_marks_degraded(self) -> None:
        class FailedReranker:
            name = "failed"

            async def rerank(self, *args, **kwargs):
                raise RuntimeError("injected")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.db"
            result = await WikiSearchTool(
                settings_for(path), FailedReranker(), FakeHybridRetriever()
            ).search("Hanser直播", ["Hanser"])
        self.assertEqual(result.status, "degraded")
        self.assertIn("reranker_unavailable_retrieval_fallback", result.degraded_reasons)
        self.assertTrue(result.evidence)
        self.assertTrue(result.candidates)

    async def test_dense_failure_falls_back_to_bm25(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.db"
            with db.connect(path) as conn:
                db.init_db(conn)
                document = conn.execute("INSERT INTO documents(filename,filepath,content,mtime,size,indexed_at) VALUES ('wiki.md','wiki.md','退出',0,2,'now')")
                chunk = conn.execute("INSERT INTO document_chunks(document_id,chunk_index,text,start_char,end_char,token_count,metadata_json) VALUES (?,0,'退出',0,2,1,'{\"source_type\":\"wiki\"}')", (int(document.lastrowid),))
                conn.execute("INSERT INTO chunk_tokens(chunk_id,token,tf) VALUES (?,'退出',1)", (int(chunk.lastrowid),))
                conn.commit()
            retriever = HybridRetriever(
                db_path=path,
                userdict_path=Path(tmp) / "missing.txt",
                config=HybridRetrievalConfig(),
                embedder=SimpleNamespace(model_name="m"),
                vector_store=SimpleNamespace(),
            )
            with patch.object(retriever, "_dense_search", AsyncMock(side_effect=RuntimeError("injected"))):
                result = await retriever.search_with_status("退出", ["退出"])
        self.assertEqual(result.degraded_reasons, ("dense_unavailable_bm25_fallback",))
        self.assertEqual(result.candidates[0].filename, "wiki.md")


class IdempotencyAndPostTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_persisted_reply_survives_post_turn_failure_and_retry_is_idempotent(self) -> None:
        class Planner:
            async def plan(self, message, history, summary=None):
                return DialoguePlan(intent="chitchat", need_wiki=False, need_memory=False, need_style_examples=False, standalone_query=message, keywords=[], response_mode="casual", fact_sensitivity="low", target_length="short")

        class Responder:
            model_name = "test"

            def __init__(self):
                self.calls = 0

            async def respond(self, context):
                self.calls += 1
                return GeneratedResponse(text="收到", raw_text="收到")

        class PostTurn:
            def __init__(self):
                self.calls = 0

            async def process(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("injected after persistence")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.db"
            with db.connect(path) as conn:
                db.init_db(conn)
                db.apply_slice4_failure_state_migration(conn)
            memory = MemoryStore(path)
            responder = Responder()
            post_turn = PostTurn()
            service = ChatAgentService(
                conversations=ConversationStore(path),
                planner=Planner(),
                wiki_tool=SimpleNamespace(),
                style_tool=SimpleNamespace(),
                memory_tool=SimpleNamespace(),
                memory_store=memory,
                post_turn=post_turn,
                context_builder=ContextBuilder(PersonaCompiler(PERSONA_DIR)),
                responder=responder,
                request_state=RequestStateStore(path),
            )
            request = ChatRequest(conversation_id="c", user_id="u", message="hello", request_id="request-1")
            first = await service.send(request)
            second = await service.send(request)
            self.assertEqual(first.post_turn_status, "pending_retry")
            self.assertEqual(second.trace_id, first.trace_id)
            self.assertEqual(responder.calls, 1)
            self.assertEqual(ConversationStore(path).message_count("c", user_id="u"), 2)
            await service.retry_post_turn(first.post_turn_retry_id)
            third = await service.send(request)
            self.assertEqual(third.post_turn_status, "completed")
            self.assertEqual(third.status, "ok")
            self.assertEqual(post_turn.calls, 2)
            with self.assertRaises(ServiceFailure) as conflict:
                await service.send(
                    ChatRequest(
                        conversation_id="c",
                        user_id="u",
                        message="different",
                        request_id="request-1",
                    )
                )
            self.assertEqual(conflict.exception.code, "idempotency_conflict")
            with self.assertRaises(ServiceFailure) as setting_conflict:
                await service.send(
                    ChatRequest(
                        conversation_id="c",
                        user_id="u",
                        message="hello",
                        request_id="request-1",
                        persona_settings={"adult_innuendo_opt_in": True},
                    )
                )
            self.assertEqual(setting_conflict.exception.code, "idempotency_conflict")

    async def test_embedding_failure_is_retried_for_existing_unindexed_memory(self) -> None:
        class FlakyEmbedder:
            model_name = "m"

            def __init__(self):
                self.calls = 0

            async def embed_documents(self, texts):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("injected embedding failure")
                return [[1.0, 0.0] for _ in texts]

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.db"
            with db.connect(path) as conn:
                db.init_db(conn)
                db.apply_slice5_ownership_index_migration(conn)
            conversations = ConversationStore(path)
            message_id, _ = conversations.append_turn(conversation_id="c", user_id="u", user_text="我喜欢咖啡", assistant_text="好", model_name="m", trace_id="t", persona_version="p")
            store = MemoryStore(path)
            embedder = FlakyEmbedder()
            pipeline = PostTurnPipeline(
                store=store,
                extractor=MemoryCandidateExtractor(),
                write_gate=MemoryWriteGate(MemoryConfig()),
                retriever=MemoryRetriever(store=store, embedder=embedder, vector_store=SQLiteVectorStore(path), config=MemoryConfig()),
                summarizer=ConversationSummarizer(conversations=conversations, memories=store, config=MemoryConfig()),
                state_engine=CharacterStateEngine(),
            )
            values = dict(user_id="u", conversation_id="c", user_message_id=message_id, user_message="我喜欢咖啡", previous_relationship=RelationshipState(), previous_scene=SceneState())
            with self.assertRaises(RuntimeError):
                await pipeline.process(**values)
            await pipeline.process(**values)
            self.assertEqual(embedder.calls, 2)
            self.assertEqual(len(store.eligible_memory_ids(user_id="u")), 1)


if __name__ == "__main__":
    unittest.main()
