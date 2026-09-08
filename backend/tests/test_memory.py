from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from fastapi.testclient import TestClient

from hanser_agent import db
from hanser_agent.agent.conversation import ConversationStore
from hanser_agent.api import create_app
from hanser_agent.config import LLMTaskConfig, MemoryConfig, Settings
from hanser_agent.memory import (
    MemoryCandidateExtractor,
    MemoryRetriever,
    MemoryStore,
    MemoryWriteGate,
    PostTurnPipeline,
)
from hanser_agent.memory.state_engine import CharacterStateEngine
from hanser_agent.memory.summarizer import ConversationSummarizer
from hanser_agent.models import (
    ChatResponse,
    MemoryCandidate,
    MemoryPatch,
    RelationshipState,
    SceneState,
)
from hanser_agent.retrieval import SQLiteVectorStore


class FakeEmbedder:
    model_name = "memory-test-embedding"

    async def embed_documents(self, texts):
        return [self._vector(value) for value in texts]

    async def embed_queries(self, texts):
        return [self._vector(value) for value in texts]

    @staticmethod
    def _vector(value):
        return [1.0, 0.0] if "考试" in value else [0.0, 1.0]


class FakeChatAgent:
    async def send(self, request):
        return ChatResponse(text=f"收到 {request.message}")


def init_database(path: Path) -> None:
    with db.connect(path) as conn:
        db.init_db(conn)


def settings_for(root: Path) -> Settings:
    task = LLMTaskConfig("test", 64, 0.1, 0.9)
    return Settings(
        root=root,
        db_path=root / "documents.db",
        data_dir=root,
        userdict_path=root / "userdict.txt",
        base_url="http://localhost/v1",
        api_key="test",
        default_model="test",
        bunny=task,
        prometheus=task,
        hanser=task,
    )


class ConversationPersistenceTests(unittest.TestCase):
    def test_messages_and_summary_survive_store_recreation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "documents.db"
            init_database(db_path)
            first = ConversationStore(db_path, max_messages=2)
            for index in range(2):
                first.append_turn(
                    conversation_id="conversation",
                    user_id="user",
                    user_text=f"用户消息{index}",
                    assistant_text=f"回答{index}",
                    model_name="model",
                    trace_id=f"trace-{index}",
                    persona_version="persona_v1",
                )
            second = ConversationStore(db_path, max_messages=2)
            recent = second.get_recent("conversation", user_id="user")
            self.assertEqual([item.content for item in recent], ["用户消息1", "回答1"])

            memory_store = MemoryStore(db_path)
            summary = ConversationSummarizer(
                conversations=second,
                memories=memory_store,
                config=MemoryConfig(
                    recent_messages=2,
                    summary_trigger_messages=4,
                    summary_interval_messages=2,
                ),
            ).update("conversation", user_id="user")
            self.assertIsNotNone(summary)
            self.assertIn("用户消息0", summary.content)
            self.assertEqual(
                MemoryStore(db_path).get_summary("conversation").content,
                summary.content,
            )

    def test_summary_rebuild_marks_revisions_and_closed_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "documents.db"
            init_database(db_path)
            conversations = ConversationStore(db_path, max_messages=20)
            store = MemoryStore(db_path)
            extractor = MemoryCandidateExtractor()

            def append_and_store(text: str):
                message_id, _ = conversations.append_turn(
                    conversation_id="conversation",
                    user_id="user",
                    user_text=text,
                    assistant_text="收到",
                    model_name="model",
                    trace_id=text,
                    persona_version="persona_v1",
                )
                candidates = MemoryWriteGate(MemoryConfig()).select(extractor.extract(
                    user_id="user",
                    conversation_id="conversation",
                    message_id=message_id,
                    message=text,
                ))
                for candidate in candidates:
                    store.upsert_candidate(candidate)
                return message_id

            append_and_store("我喜欢咖啡")
            append_and_store("其实我不喜欢咖啡")
            append_and_store("我明天下午考试")
            completed_id = append_and_store("我已经考完试了")
            store.close_unresolved(
                user_id="user",
                conversation_id="conversation",
                memory_key="unresolved:考试",
                source_message_id=completed_id,
                content="我已经考完试了",
            )

            summary = ConversationSummarizer(
                conversations=conversations,
                memories=store,
                config=MemoryConfig(
                    recent_messages=0,
                    summary_trigger_messages=1,
                    summary_interval_messages=1,
                    summary_max_chars=2000,
                ),
            ).update("conversation", user_id="user")

            self.assertIsNotNone(summary)
            self.assertIn("[历史说法，已被修订] 我喜欢咖啡", summary.content)
            self.assertIn("[当前有效断言] 其实我不喜欢咖啡", summary.content)
            self.assertIn("[历史说法，已被修订] 我明天下午考试", summary.content)
            self.assertIn("[事件已结束] 我已经考完试了", summary.content)


class MemoryMigrationTests(unittest.TestCase):
    def test_address_migration_rekeys_legacy_name_without_auto_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "address.db"
            init_database(db_path)
            with db.connect(db_path) as conn:
                self.assertIsNone(conn.execute(
                    "SELECT 1 FROM schema_migrations WHERE version=3"
                ).fetchone())
                conn.execute(
                    """
                    INSERT INTO memories
                      (id,user_id,type,memory_key,content,importance,confidence,status,
                       created_at,source_message_ids_json,subject,predicate,object_value)
                    VALUES ('legacy-name','user','user_preference','user:name',
                            '用户希望被称为小林',.9,.95,'active',
                            '2026-01-01T00:00:00+00:00','["message-1"]',
                            'user','preferred_name','小林')
                    """
                )
                db._apply_memory_address_migration(conn)
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM memories WHERE id='legacy-name'"
                ).fetchone()
                migration = conn.execute(
                    "SELECT name FROM schema_migrations WHERE version=3"
                ).fetchone()
            self.assertEqual(row["memory_key"], "address:personal_name:小林")
            self.assertEqual(row["predicate"], "preferred_address")
            self.assertEqual(row["address_kind"], "personal_name")
            self.assertEqual(row["memory_scope"], "global")
            self.assertEqual(json.loads(row["context_tags_json"]), ["personal"])
            self.assertEqual(row["address_priority"], 1.0)
            self.assertEqual(migration["name"], "memory_address_options")

    def test_legacy_shared_event_and_invalid_sources_become_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            with db.connect(db_path) as conn:
                conn.execute(db.SCHEMA_MESSAGES)
                conn.execute(
                    """
                    CREATE TABLE memories (
                        id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                        conversation_id TEXT, type TEXT NOT NULL,
                        memory_key TEXT NOT NULL, content TEXT NOT NULL,
                        importance REAL NOT NULL, confidence REAL NOT NULL,
                        status TEXT NOT NULL, created_at TEXT NOT NULL,
                        last_accessed_at TEXT, source_message_ids_json TEXT NOT NULL,
                        embedding_ref TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO messages
                        (id, conversation_id, role, content, created_at, turn_index)
                    VALUES ('source', 'conversation', 'user', '我们上次去了上海',
                            '2026-01-01T00:00:00+00:00', 0)
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO memories
                        (id, user_id, conversation_id, type, memory_key, content,
                         importance, confidence, status, created_at,
                         source_message_ids_json)
                    VALUES (?, 'user', 'conversation', ?, ?, ?, .9, .9, 'active',
                            '2026-01-01T00:00:00+00:00', ?)
                    """,
                    [
                        ("shared", "shared_event", "shared:shanghai",
                         "我们上次去了上海", json.dumps(["source"])),
                        ("missing", "user_fact", "fact:missing",
                         "没有真实来源", json.dumps(["does-not-exist"])),
                    ],
                )
                conn.commit()

            init_database(db_path)
            with db.connect(db_path) as conn:
                rows = {
                    row["id"]: row
                    for row in conn.execute(
                        "SELECT id, validity, source_kind FROM memories"
                    ).fetchall()
                }
                migration = conn.execute(
                    "SELECT name FROM schema_migrations WHERE version = 1"
                ).fetchone()

            self.assertEqual(rows["shared"]["validity"], "unverified")
            self.assertEqual(rows["shared"]["source_kind"], "legacy_import")
            self.assertEqual(rows["missing"]["validity"], "unverified")
            self.assertEqual(migration["name"], "memory_assertion_semantics")


class MemoryPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_write_retrieve_state_and_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "documents.db"
            init_database(db_path)
            config = MemoryConfig(
                recent_messages=4,
                summary_trigger_messages=20,
                summary_interval_messages=4,
            )
            conversations = ConversationStore(db_path, max_messages=4)
            user_message_id, _ = conversations.append_turn(
                conversation_id="conversation",
                user_id="user",
                user_text="我明天下午考试",
                assistant_text="知道啦",
                model_name="model",
                trace_id="trace",
                persona_version="persona_v1",
            )
            store = MemoryStore(db_path)
            retriever = MemoryRetriever(
                store=store,
                embedder=FakeEmbedder(),
                vector_store=SQLiteVectorStore(db_path),
                config=config,
            )
            pipeline = PostTurnPipeline(
                store=store,
                extractor=MemoryCandidateExtractor(),
                write_gate=MemoryWriteGate(config),
                retriever=retriever,
                summarizer=ConversationSummarizer(
                    conversations=conversations,
                    memories=store,
                    config=config,
                ),
                state_engine=CharacterStateEngine(),
            )
            result = await pipeline.process(
                user_id="user",
                conversation_id="conversation",
                user_message_id=user_message_id,
                user_message="我明天下午考试",
                previous_relationship=RelationshipState(),
                previous_scene=SceneState(),
            )
            self.assertEqual(len(result.memory_writes), 1)
            self.assertEqual(result.memory_writes[0].type, "unresolved_thread")
            self.assertIn("考试", result.scene_state.unresolved_threads[0])
            self.assertGreater(result.relationship_state.trust, 0.4)

            recalled = await MemoryRetriever(
                store=MemoryStore(db_path),
                embedder=FakeEmbedder(),
                vector_store=SQLiteVectorStore(db_path),
                config=config,
            ).search(query="考试是什么时候", user_id="user")
            self.assertEqual(recalled[0].memory.content, "我明天下午考试")
            self.assertTrue(recalled[0].memory.source_message_ids)

            extractor = MemoryCandidateExtractor()
            for index, text in enumerate(("我叫小明", "我叫小红"), start=1):
                message_id, _ = conversations.append_turn(
                    conversation_id="conversation",
                    user_id="user",
                    user_text=text,
                    assistant_text="收到",
                    model_name="model",
                    trace_id=f"name-{index}",
                    persona_version="persona_v1",
                )
                candidate = extractor.extract(
                    user_id="user",
                    conversation_id="conversation",
                    message_id=message_id,
                    message=text,
                )[0]
                store.upsert_candidate(candidate)
            active = store.list_memories(user_id="user")
            superseded = store.list_memories(user_id="user", status="superseded")
            self.assertIn("用户名字是小红", [item.content for item in active])
            self.assertIn("用户名字是小明", [item.content for item in active])
            self.assertFalse(
                any(item.predicate == "preferred_address" for item in superseded)
            )


class MemoryApiTests(unittest.TestCase):
    def test_user_can_list_edit_and_delete_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = settings_for(root)
            init_database(settings.db_path)
            store = MemoryStore(settings.db_path)
            conversations = ConversationStore(settings.db_path)
            message_id, _ = conversations.append_turn(
                conversation_id="conversation",
                user_id="user",
                user_text="我叫小明",
                assistant_text="收到",
                model_name="model",
                trace_id="seed",
                persona_version="persona_v1",
            )
            candidate = MemoryCandidateExtractor().extract(
                user_id="user",
                conversation_id="conversation",
                message_id=message_id,
                message="我叫小明",
            )[0]
            memory, _ = store.upsert_candidate(candidate)
            app = create_app(
                settings=settings,
                chat_agent=FakeChatAgent(),
                memory_store=store,
            )
            with TestClient(app) as client:
                listed = client.get("/v1/memories", params={"user_id": "user"})
                edited = client.patch(
                    f"/v1/memories/{memory.id}",
                    json={"content": "用户希望被称为小红"},
                )
                deleted = client.delete(f"/v1/memories/{memory.id}")
                active = client.get("/v1/memories", params={"user_id": "user"})

            self.assertEqual(listed.status_code, 200)
            self.assertEqual(edited.json()["content"], "用户希望被称为小红")
            self.assertEqual(edited.json()["source_kind"], "human_edit")
            self.assertEqual(edited.json()["revision_of_id"], memory.id)
            self.assertEqual(edited.json()["source_message_ids"], [])
            self.assertEqual(deleted.status_code, 200)
            self.assertEqual(active.json(), [])

            null_patch = client.patch(
                f"/v1/memories/{memory.id}", json={"content": None}
            )
            self.assertEqual(null_patch.status_code, 422)

    def test_empty_patch_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MemoryPatch()
        with self.assertRaises(ValueError):
            MemoryPatch(content=None)


class MemoryAssertionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.extractor = MemoryCandidateExtractor()
        self.gate = MemoryWriteGate(MemoryConfig())

    def decisions(self, message: str):
        candidates = self.extractor.extract(
            user_id="user", conversation_id="conversation",
            message_id="message", message=message,
        )
        return self.gate.evaluate(candidates)

    def test_questions_hypotheticals_instructions_and_quotes_are_rejected(self) -> None:
        cases = {
            "你还记得我们之前一起去过上海吗": "question_not_assertion",
            "如果我叫小林你会怎么称呼我": "hypothetical_not_assertion",
            "别记住这句话": "remember_request_is_not_evidence",
            "记住“我喜欢咖啡”这句话": "remember_request_is_not_evidence",
        }
        for message, reason in cases.items():
            with self.subTest(message=message):
                decisions = self.decisions(message)
                self.assertTrue(decisions)
                self.assertFalse(any(item.accepted for item in decisions))
                self.assertIn(reason, {item.reason for item in decisions})

    def test_atomic_preferences_allow_multiple_objects_and_double_negation(self) -> None:
        decisions = self.decisions("我喜欢咖啡也喜欢茶")
        accepted = [item.candidate for item in decisions if item.accepted]
        self.assertEqual({item.object_value for item in accepted}, {"咖啡", "茶"})
        self.assertEqual({item.memory_key for item in accepted}, {
            "preference:like:咖啡", "preference:like:茶",
        })

        double_negative = [
            item.candidate for item in self.decisions("我不是不喜欢咖啡") if item.accepted
        ]
        self.assertEqual(len(double_negative), 1)
        self.assertEqual(double_negative[0].polarity, "positive")
        self.assertEqual(double_negative[0].content, "用户喜欢咖啡")

    def test_multiple_address_options_do_not_supersede_each_other(self) -> None:
        message = "我叫小林 也可以叫我林林 每句话都叫我毛怪们"
        accepted = [
            item.candidate for item in self.decisions(message) if item.accepted
        ]
        self.assertEqual(
            {(item.address_kind, item.object_value) for item in accepted},
            {
                ("personal_name", "小林"),
                ("nickname", "林林"),
                ("fan_identity", "毛怪"),
            },
        )
        self.assertEqual(len({item.memory_key for item in accepted}), 3)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "addresses.db"
            init_database(path)
            conversations = ConversationStore(path)
            message_id, _ = conversations.append_turn(
                conversation_id="conversation",
                user_id="user",
                user_text=message,
                assistant_text="收到",
                model_name="model",
                trace_id="address-options",
                persona_version="persona_v1",
            )
            store = MemoryStore(path)
            candidates = self.extractor.extract(
                user_id="user",
                conversation_id="conversation",
                message_id=message_id,
                message=message,
            )
            for candidate in self.gate.select(candidates):
                store.upsert_candidate(candidate)
            options = store.list_address_options(user_id="user")
            self.assertEqual(len(options), 3)
            self.assertEqual(options[0].object_value, "小林")
            self.assertTrue(all(item.predicate == "preferred_address" for item in options))

    def test_compact_multiple_addresses_and_negative_address_are_not_misparsed(self) -> None:
        compact = [
            item.candidate for item in self.decisions("我叫小林也可以叫我林林")
            if item.accepted and item.candidate.predicate == "preferred_address"
        ]
        self.assertEqual(
            {(item.address_kind, item.object_value) for item in compact},
            {("personal_name", "小林"), ("nickname", "林林")},
        )
        negative = [
            item.candidate for item in self.decisions("以后别再叫我林林")
            if item.accepted and item.candidate.predicate == "preferred_address"
        ]
        self.assertEqual(negative, [])

    async def test_correction_lifecycle_source_validation_and_prefilter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.db"
            init_database(path)
            conversations = ConversationStore(path)
            store = MemoryStore(path)
            retriever = MemoryRetriever(
                store=store, embedder=FakeEmbedder(),
                vector_store=SQLiteVectorStore(path), config=MemoryConfig(),
            )
            pipeline = PostTurnPipeline(
                store=store, extractor=self.extractor, write_gate=self.gate,
                retriever=retriever,
                summarizer=ConversationSummarizer(
                    conversations=conversations, memories=store,
                    config=MemoryConfig(summary_trigger_messages=100),
                ),
                state_engine=CharacterStateEngine(),
            )

            async def turn(text: str):
                message_id, _ = conversations.append_turn(
                    conversation_id="conversation", user_id="user", user_text=text,
                    assistant_text="收到", model_name="model", trace_id=text,
                    persona_version="persona_v1",
                )
                return await pipeline.process(
                    user_id="user", conversation_id="conversation",
                    user_message_id=message_id, user_message=text,
                    previous_relationship=store.get_relationship("user"),
                    previous_scene=store.get_scene("conversation"),
                )

            await turn("我喜欢咖啡")
            await turn("我喜欢茶")
            correction = await turn("我之前说错了 我不喜欢咖啡 我喜欢茶")
            self.assertEqual(
                {item.candidate.object_value for item in correction.memory_decisions if item.accepted},
                {"咖啡", "茶"},
            )
            active = store.list_memories(user_id="user")
            self.assertIn("用户不喜欢咖啡", [item.content for item in active])
            self.assertIn("用户喜欢茶", [item.content for item in active])
            self.assertNotIn("用户喜欢咖啡", [item.content for item in active])

            await turn("我叫小林 也可以叫我林林 每句话都叫我毛怪们")
            retired = await turn("以后别再叫我林林")
            self.assertEqual(
                [(item.address_kind, item.object_value) for item in retired.lifecycle_updates],
                [("nickname", "林林")],
            )
            self.assertEqual(
                {(item.address_kind, item.object_value) for item in store.list_address_options(user_id="user")},
                {("personal_name", "小林"), ("fan_identity", "毛怪")},
            )
            retired_record = retired.lifecycle_updates[0]
            self.assertEqual(retired_record.status, "closed")
            self.assertEqual(retired_record.assertion_type, "correction")
            self.assertEqual(retired_record.polarity, "negative")
            self.assertTrue(retired_record.source_message_ids)

            await turn("我明天下午考试")
            closed = await turn("我已经考完试了")
            self.assertTrue(closed.lifecycle_updates)
            self.assertFalse(any(item.type == "unresolved_thread" for item in store.list_memories(user_id="user")))
            self.assertTrue(store.list_memories(user_id="user", status="closed"))

            with self.assertRaises(ValueError):
                store.upsert_candidate(MemoryCandidate(
                    user_id="user", conversation_id="conversation", type="user_fact",
                    memory_key="missing", content="unsupported", importance=.9,
                    confidence=.9, source_message_ids=["missing"],
                ))

            own = next(item for item in active if item.object_value == "茶")
            vectors = SQLiteVectorStore(path)
            vectors.upsert("memories", FakeEmbedder.model_name, [
                *[(f"foreign-{index}", [0.0, 1.0]) for index in range(30)],
            ])
            results = await retriever.search(query="喜欢喝什么", user_id="user")
            self.assertIn(own.id, [item.memory.id for item in results])


if __name__ == "__main__":
    unittest.main()
