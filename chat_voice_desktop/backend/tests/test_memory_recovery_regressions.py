from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from hanser_agent import db
from hanser_agent.agent.context_builder import ContextBuilder
from hanser_agent.agent.conversation import ConversationStore
from hanser_agent.agent.recovery import recover_post_turn_in_background
from hanser_agent.agent.request_state import RequestStateStore
from hanser_agent.agent.service import ChatAgentService
from hanser_agent.config import MemoryConfig, PerformanceConfig
from hanser_agent.failures import ServiceFailure
from hanser_agent.memory import MemoryCandidateExtractor, MemoryStore, MemoryWriteGate, PostTurnPipeline
from hanser_agent.memory.state_engine import CharacterStateEngine
from hanser_agent.memory.summarizer import ConversationSummarizer
from hanser_agent.models import ChatRequest, DialoguePlan, RelationshipState, SceneState
from hanser_agent.persona.signals import extract_explicit_preference_events
from hanser_agent.persona import PersonaCompiler
from hanser_agent.responder import GeneratedResponse


class AssertionScopeTests(unittest.TestCase):
    def setUp(self):
        self.extractor = MemoryCandidateExtractor()
        self.gate = MemoryWriteGate(MemoryConfig())

    def accepted(self, text):
        return self.gate.select(self.extractor.extract(
            user_id="u", conversation_id="c", message_id="m", message=text,
        ))

    def test_reports_and_quotes_do_not_become_user_facts(self):
        for text in [
            "她喜欢咖啡，我在转述她的话", "我朋友说他喜欢咖啡",
            "她说我叫小明，我只是转述她的话", "她说，我叫小明",
            "小明说我喜欢咖啡", "她说：“我喜欢咖啡”", "“我叫小明”",
        ]:
            with self.subTest(text=text):
                self.assertEqual(self.accepted(text), [])

    def test_mixed_subjects_retain_only_user_assertion(self):
        for text in ["她喜欢咖啡，但我喜欢茶", "她喜欢咖啡但我喜欢茶", "她说：“我喜欢咖啡”，我喜欢茶"]:
            with self.subTest(text=text):
                self.assertEqual([m.content for m in self.accepted(text)], ["用户喜欢茶"])
        self.assertEqual([m.content for m in self.accepted("她问：咖啡好吗？我喜欢茶")], ["用户喜欢茶"])
        self.assertEqual([m.content for m in self.accepted("我喜欢咖啡吗？不过我喜欢茶")], ["用户喜欢茶"])

    def test_multiple_preferences_and_corrections_remain_supported(self):
        self.assertEqual({m.object_value for m in self.accepted("我喜欢咖啡也喜欢茶")}, {"咖啡", "茶"})
        self.assertEqual(self.accepted("我不是不喜欢咖啡")[0].polarity, "positive")
        values = self.accepted("我之前说错了 我不喜欢咖啡 我喜欢茶")
        self.assertEqual({m.content for m in values}, {"用户不喜欢咖啡", "用户喜欢茶"})

    def test_close_and_retire_share_assertion_scope(self):
        for text in ["她说考试取消了，我只是转述", "考试取消了吗", "如果考试取消了", "她说别叫我小林", "别叫我小林吗"]:
            with self.subTest(text=text):
                self.assertEqual(self.extractor.closed_memory_keys(text), [])
                self.assertEqual(self.extractor.retired_address_values(text), [])
        self.assertEqual(self.extractor.closed_memory_keys("我已经考完试了"), ["unresolved:考试"])
        self.assertEqual(self.extractor.retired_address_values("以后别叫我小林"), ["小林"])

    def test_temporary_and_durable_length_have_distinct_scope(self):
        for text in ["这次回答短一点就好", "回答短一点", "她说回答短一点"]:
            self.assertFalse(any(m.predicate == "answer_length" for m in self.accepted(text)))
        self.assertEqual(self.accepted("以后回答短一点")[0].memory_scope, "global")
        self.assertEqual(self.accepted("这次聊天回答详细一点")[0].memory_scope, "conversation")

    def test_current_length_overrides_default_without_truncating_long_task_by_default(self):
        plan = DialoguePlan(
            intent="chitchat", need_wiki=False, standalone_query="测试", keywords=[],
            response_mode="casual", fact_sensitivity="low", target_length="long",
        )
        contract = ContextBuilder._response_contract(plan, answer_length_preference="short")
        self.assertIn("目标长度 long", contract)
        contract = ContextBuilder._response_contract(plan, answer_length_preference="short", answer_length_source="本轮明确要求")
        self.assertIn("目标长度 short 来源 本轮明确要求", contract)


class LexicalIndexStub:
    def __init__(self, store):
        self.store = store
        self.index = AsyncMock(side_effect=self._index)

    async def _index(self, memory):
        if hasattr(self.store, "set_retrieval_ref"):
            self.store.set_retrieval_ref(memory.id, "test:" + memory.id)
        else:
            self.store.set_embedding_ref(memory.id)


class PostTurnAtomicityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "memory.db"
        with db.connect(self.path) as conn:
            db.init_db(conn)
        self.conversations = ConversationStore(self.path)
        self.store = MemoryStore(self.path)
        self.index = LexicalIndexStub(self.store)
        self.summarizer = ConversationSummarizer(
            conversations=self.conversations, memories=self.store,
            config=MemoryConfig(summary_trigger_messages=1, recent_messages=0),
        )
        self.pipeline = PostTurnPipeline(
            store=self.store, extractor=MemoryCandidateExtractor(),
            write_gate=MemoryWriteGate(MemoryConfig()), retriever=self.index,
            summarizer=self.summarizer, state_engine=CharacterStateEngine(),
        )

    def append(self, text, conversation="c"):
        message_id, _ = self.conversations.append_turn(
            conversation_id=conversation, user_id="u", user_text=text,
            assistant_text="收到", model_name="test", trace_id=text,
            persona_version="test",
        )
        return message_id

    async def process(self, text, message_id, conversation="c"):
        return await self.pipeline.process(
            user_id="u", conversation_id=conversation, user_message_id=message_id,
            user_message=text, previous_relationship=RelationshipState(),
            previous_scene=SceneState(), permission_events=extract_explicit_preference_events(
                text, source_message_id=message_id,
            ),
        )

    async def test_failure_rolls_back_all_semantic_tables_including_marker(self):
        text = "我喜欢咖啡，以后别逗我"
        message_id = self.append(text)
        original = self.store.mark_post_turn_committed

        def fail_after_marker(**kwargs):
            original(**kwargs)
            raise RuntimeError("injected semantic failure")

        with patch.object(self.store, "mark_post_turn_committed", side_effect=fail_after_marker):
            with self.assertRaises(RuntimeError):
                await self.process(text, message_id)
        with db.connect(self.path) as conn:
            for table in ["memories", "relationship_states", "scene_states", "conversation_summaries", "persona_permission_events", "post_turn_commits"]:
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)
        self.assertEqual(self.index.index.await_count, 0)
        self.assertIsNone(self.store._transaction_connection)

    async def test_failed_old_task_preserves_new_correction_and_new_state(self):
        old = self.append("我喜欢咖啡")
        with patch.object(self.summarizer, "update", side_effect=RuntimeError("summary failed")):
            with self.assertRaises(RuntimeError):
                await self.process("我喜欢咖啡", old)
        self.assertEqual(self.store.list_memories(user_id="u"), [])
        new = self.append("我不喜欢咖啡")
        await self.process("我不喜欢咖啡", new)
        relationship = self.store.get_relationship("u")
        scene = self.store.get_scene("c")
        await self.process("我喜欢咖啡", old)
        self.assertEqual([m.content for m in self.store.list_memories(user_id="u")], ["用户不喜欢咖啡"])
        self.assertEqual(self.store.get_relationship("u"), relationship)
        self.assertEqual(self.store.get_scene("c"), scene)
        self.assertTrue(self.store.is_post_turn_committed(old))

    async def test_old_retry_can_add_unrelated_fact_without_overwriting_new_key(self):
        old = self.append("我喜欢咖啡，也喜欢茶")
        new = self.append("我不喜欢咖啡")
        await self.process("我不喜欢咖啡", new)
        await self.process("我喜欢咖啡，也喜欢茶", old)
        self.assertEqual({m.content for m in self.store.list_memories(user_id="u")}, {"用户不喜欢咖啡", "用户喜欢茶"})

    async def test_old_open_does_not_reopen_new_closed_event(self):
        old = self.append("我明天有考试")
        await self.process("我明天有考试", old)
        new = self.append("我已经考完试了")
        await self.process("我已经考完试了", new)
        # Simulate a legacy task missing its marker after partial writes.
        with db.connect(self.path) as conn:
            conn.execute("DELETE FROM post_turn_commits WHERE user_message_id=?", (old,))
        await self.process("我明天有考试", old)
        self.assertFalse(any(m.type == "unresolved_thread" for m in self.store.list_memories(user_id="u")))

    async def test_old_close_and_old_address_retirement_do_not_change_newer_sources(self):
        close = self.append("考试取消了")
        retire = self.append("别叫我小林")
        new = self.append("我明天有考试，我叫小林")
        await self.process("我明天有考试，我叫小林", new)
        await self.process("考试取消了", close)
        await self.process("别叫我小林", retire)
        self.assertEqual(len(self.store.list_memories(user_id="u")), 2)

    async def test_reaffirmation_retains_newer_source_order(self):
        initial = self.append("我明天有考试")
        await self.process("我明天有考试", initial)
        close = self.append("考试取消了")
        newer = self.append("我明天有考试")
        await self.process("我明天有考试", newer)
        await self.process("考试取消了", close)
        self.assertEqual(len(self.store.list_memories(user_id="u")), 1)
        self.assertIn(newer, self.store.list_memories(user_id="u")[0].source_message_ids)

    async def test_index_failure_retries_only_index_without_state_replay(self):
        message_id = self.append("我喜欢咖啡")
        self.index.index.side_effect = RuntimeError("index unavailable")
        with self.assertRaises(RuntimeError):
            await self.process("我喜欢咖啡", message_id)
        self.assertTrue(self.store.is_post_turn_committed(message_id))
        state = self.store.get_relationship("u")
        self.index.index.side_effect = self.index._index
        await self.process("我喜欢咖啡", message_id)
        self.assertEqual(self.store.get_relationship("u"), state)
        self.assertEqual(len(self.store.list_memories(user_id="u")), 1)

    async def test_conversation_length_overrides_global_and_does_not_leak(self):
        await self.process("以后回答短一点", self.append("以后回答短一点"))
        await self.process("这次聊天回答详细一点", self.append("这次聊天回答详细一点"))
        self.assertEqual(self.store.get_answer_length_preference(user_id="u", conversation_id="c").memory_scope, "conversation")
        self.assertEqual(self.store.get_answer_length_preference(user_id="u", conversation_id="other").memory_scope, "global")
        self.assertIsNone(self.store.get_answer_length_preference(user_id="other", conversation_id="c"))

    async def test_send_reads_default_without_memory_retrieval_and_current_request_overrides(self):
        await self.process("以后回答短一点", self.append("以后回答短一点"))
        plan = DialoguePlan(
            intent="chitchat", need_wiki=False, standalone_query="测试", keywords=[],
            response_mode="casual", fact_sensitivity="low", target_length="medium",
            need_memory=False, need_style_examples=False,
        )
        memory_tool = SimpleNamespace(search=AsyncMock())
        responder = SimpleNamespace(model_name="test", respond=AsyncMock(return_value=GeneratedResponse(text="收到", raw_text="收到")))
        service = ChatAgentService(
            conversations=self.conversations,
            planner=SimpleNamespace(plan=AsyncMock(return_value=plan)),
            wiki_tool=None, style_tool=None, memory_tool=memory_tool,
            memory_store=self.store, post_turn=self.pipeline,
            context_builder=ContextBuilder(PersonaCompiler(Path(__file__).parents[1] / "hanser_agent/prompts/persona")),
            responder=responder,
        )
        await service.send(ChatRequest(user_id="u", conversation_id="c", message="聊点别的"))
        context = responder.respond.call_args.args[0]
        self.assertIn("目标长度 short 来源 已保存的回答长度偏好", context.blocks["response_contract"])
        memory_tool.search.assert_not_awaited()
        await service.send(ChatRequest(user_id="u", conversation_id="c", message="以后回答短一点，这次回答详细一点"))
        self.assertIn("目标长度 long 来源 本轮明确要求", responder.respond.call_args.args[0].blocks["response_contract"])
        self.assertEqual(self.store.get_answer_length_preference(user_id="u", conversation_id="c").object_value, "用户偏好简短回答")

    async def test_text_only_request_disables_structured_responder_and_keeps_reply_identity(self):
        plan = DialoguePlan(intent="chitchat", need_wiki=False, standalone_query="测试", keywords=[],
                            response_mode="casual", fact_sensitivity="low", target_length="short",
                            need_memory=False, need_style_examples=False)
        responder = SimpleNamespace(model_name="test", respond=AsyncMock(return_value=GeneratedResponse(text="收到", raw_text="收到")))
        service = ChatAgentService(conversations=self.conversations,
            planner=SimpleNamespace(plan=AsyncMock(return_value=plan)), wiki_tool=None,
            style_tool=None, memory_tool=None, memory_store=self.store, post_turn=self.pipeline,
            context_builder=ContextBuilder(PersonaCompiler(Path(__file__).parents[1] / "hanser_agent/prompts/persona")),
            responder=responder, performance_config=PerformanceConfig(structured_performance_enabled=True))
        response = await service.send(ChatRequest(user_id="u", conversation_id="c", message="简短回复"))
        self.assertIs(responder.respond.call_args.kwargs["structured_performance"], False)
        self.assertNotIn("只输出一个JSON对象", responder.respond.call_args.args[0].messages[0].content)
        self.assertTrue(response.reply_id)
        self.assertIsNotNone(self.conversations.get_reply_snapshot(response.reply_id, user_id="u"))

    async def test_service_does_not_restore_forgotten_permission_from_history_or_stored_events(self):
        await self.process("以后别说脏话", self.append("以后别说脏话"))
        memory = next(item for item in self.store.list_memories(user_id="u") if item.predicate == "expression_permission")
        plan = DialoguePlan(intent="chitchat", need_wiki=False, standalone_query="测试", keywords=[],
            response_mode="casual", fact_sensitivity="low", target_length="short", need_memory=False, need_style_examples=False)
        responder = SimpleNamespace(model_name="test", respond=AsyncMock(return_value=GeneratedResponse(text="收到", raw_text="收到")))
        persona_root = Path(__file__).parents[1] / "hanser_agent/prompts/persona/candidates/hanser-persona-v2-candidate"
        service = ChatAgentService(conversations=self.conversations,
            planner=SimpleNamespace(plan=AsyncMock(return_value=plan)), wiki_tool=None, style_tool=None, memory_tool=None,
            memory_store=self.store, post_turn=self.pipeline,
            context_builder=ContextBuilder(PersonaCompiler(persona_root, settings_lifecycle="candidate")), responder=responder)
        await service.send(ChatRequest(user_id="u", conversation_id="c", message="继续聊聊"))
        self.assertIn("preference.disable_profanity", responder.respond.call_args.args[0].messages[0].content)
        self.store.delete_memory(memory.id)
        self.assertTrue(self.store.list_permission_events(user_id="u", conversation_id="c"))
        await service.send(ChatRequest(user_id="u", conversation_id="c", message="继续聊聊"))
        self.assertNotIn("preference.disable_profanity", responder.respond.call_args.args[0].messages[0].content)
        await service.send(ChatRequest(user_id="u", conversation_id="c", message="以后别说脏话"))
        self.assertIn("preference.disable_profanity", responder.respond.call_args.args[0].messages[0].content)

    async def test_failure_listing_uses_original_message_order(self):
        first = self.append("我喜欢咖啡")
        second = self.append("我不喜欢咖啡")
        requests = RequestStateStore(self.path)
        for request_id, message_id in [("second", second), ("first", first)]:
            requests.record_post_turn_failure(request_id=request_id, trace_id="test", stage="post_turn", payload={"user_message_id": message_id}, error=RuntimeError("test"))
        self.assertEqual([r["request_id"] for r in requests.list_post_turn_failures()], ["first", "second"])


class RecoveryCoordinationTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_and_send_share_user_lock(self):
        payload = {"user_id": "u"}
        request_state = SimpleNamespace(get_post_turn_failure=lambda _: {"payload_json": json.dumps(payload)})
        service = ChatAgentService(
            conversations=None, planner=None, wiki_tool=None, style_tool=None,
            memory_tool=None, memory_store=None, post_turn=None,
            context_builder=None, responder=None, request_state=request_state,
        )
        entered, release = asyncio.Event(), asyncio.Event()

        async def retry(_):
            entered.set()
            await release.wait()
            return {"status": "completed"}

        service._retry_post_turn = retry
        service._send = AsyncMock(return_value="sent")
        task = asyncio.create_task(service.retry_post_turn("failure"))
        await entered.wait()
        send = asyncio.create_task(service.send(ChatRequest(user_id="u", conversation_id="c", message="新消息")))
        await asyncio.sleep(0)
        self.assertEqual(service._send.await_count, 0)
        release.set()
        await task
        self.assertEqual(await send, "sent")
        self.assertEqual(service._user_locks, {})

    async def test_background_recovery_attempts_each_failure_once_and_can_stop(self):
        calls = []
        completed = asyncio.Event()

        async def retry(failure_id):
            calls.append(failure_id)
            if failure_id == "first":
                raise ServiceFailure("post_turn_retry_failed", "pending", retryable=True)
            completed.set()

        agent = SimpleNamespace(
            pending_post_turn_failures=lambda: [{"id": "first"}, {"id": "second"}],
            retry_post_turn=retry,
        )
        task = asyncio.create_task(recover_post_turn_in_background(agent, interval=0.01, batch_size=1))
        try:
            await asyncio.wait_for(completed.wait(), timeout=1)
            await asyncio.sleep(0.025)
            self.assertEqual(calls, ["first", "second"])
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


if __name__ == "__main__":
    unittest.main()
