from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from datetime import datetime

from fastapi.testclient import TestClient

from hanser_agent import db
from hanser_agent.agent.conversation import ConversationStore
from hanser_agent.agent.request_state import RequestStateStore
from hanser_agent.desktop_api import create_desktop_app
from hanser_agent.memory import MemoryCandidateExtractor, MemoryStore
from hanser_agent.models import ChatResponse
from hanser_agent.responder.performance import AllowedPerformance, OutputPreferences, ReplySnapshot
from hanser_agent.persona import PersonaCompiler
from hanser_agent.persona.packages import resolve_persona_package
from hanser_agent.persona.permissions import infer_expression_permissions
from hanser_agent.persona.signals import extract_explicit_preference_events
from tests.test_desktop_api import FakeChatAgent, make_test_settings


class MemoryEndpointTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        settings = make_test_settings(Path(temporary.name))
        self.app = create_desktop_app(settings=settings, chat_agent=FakeChatAgent(), desktop_token="token")
        self.path = settings.db_path
        self.store = MemoryStore(self.path)
        self.conversations = ConversationStore(self.path)
        self.headers = {"X-Hanser-Desktop-Token": "token"}

    def add(self, text):
        message_id, _ = self.conversations.append_turn(conversation_id="c", user_id="u", user_text=text,
            assistant_text="收到", model_name="test", trace_id="test", persona_version="test")
        candidate = MemoryCandidateExtractor().extract(user_id="u", conversation_id="c", message_id=message_id, message=text)[0]
        memory, _ = self.store.upsert_candidate(candidate)
        self.store.save_permission_events(user_id="u", conversation_id="c", events=extract_explicit_preference_events(text, source_message_id=message_id))
        return memory

    def test_owner_token_revision_and_forget_preserve_conversation(self):
        memory = self.add("我喜欢咖啡")
        with TestClient(self.app) as client:
            self.assertEqual(client.get("/v1/memories?user_id=u").status_code, 401)
            for method in ("patch", "delete"):
                arguments = {"json": {"content": "用户喜欢茶"}} if method == "patch" else {}
                self.assertEqual(getattr(client, method)(f"/v1/memories/{memory.id}?user_id=other", headers=self.headers, **arguments).status_code, 404)
            changed = client.patch(f"/v1/memories/{memory.id}?user_id=u", headers=self.headers, json={"content": "用户喜欢茶"})
            self.assertEqual(changed.status_code, 200)
            revision = changed.json()
            self.assertNotEqual(revision["id"], memory.id)
            self.assertEqual(revision["source_kind"], "human_edit")
            self.assertEqual(client.delete(f"/v1/memories/{revision['id']}?user_id=u", headers=self.headers).status_code, 200)
            self.assertEqual(client.get("/v1/memories?user_id=u", headers=self.headers).json(), [])
        self.assertEqual(self.conversations.message_count("c", user_id="u"), 2)

    def test_forgetting_expression_preference_also_removes_durable_event(self):
        memory = self.add("以后别说脏话")
        self.assertEqual(infer_expression_permissions(self.store.list_permission_events(user_id="u", conversation_id="c"))["profanity"], "deny")
        with TestClient(self.app) as client:
            self.assertEqual(client.delete(f"/v1/memories/{memory.id}?user_id=u", headers=self.headers).status_code, 200)
        self.assertNotIn("profanity", infer_expression_permissions(self.store.list_permission_events(user_id="u", conversation_id="c")))

    def test_missing_reply_evidence_is_owner_scoped(self):
        with TestClient(self.app) as client:
            self.assertEqual(client.get("/v1/replies/missing/evidence?user_id=u", headers=self.headers).status_code, 404)

    def test_structured_length_edit_preserves_deterministic_behavior(self):
        memory = self.add("以后简短回答")
        with TestClient(self.app) as client:
            result = client.patch(f"/v1/memories/{memory.id}?user_id=u", headers=self.headers,
                json={"object_value": "用户偏好详细回答"})
            self.assertEqual(result.status_code, 200)
        current = self.store.get_answer_length_preference(user_id="u", conversation_id="another")
        self.assertEqual(current.object_value, "用户偏好详细回答")
        self.assertEqual(current.source_kind, "human_edit")
        self.assertEqual(current.revision_of_id, memory.id)

    def test_structured_permission_edit_replaces_old_durable_event(self):
        memory = self.add("以后别说脏话")
        with db.connect(self.path) as conn:
            source_time = datetime.fromisoformat(conn.execute("SELECT created_at FROM messages WHERE id=?",
                (memory.source_message_ids[0],)).fetchone()[0])
        old_events = extract_explicit_preference_events("以后别说脏话", source_message_id=memory.source_message_ids[0],
                                                        created_at=source_time)
        with TestClient(self.app) as client:
            result = client.patch(f"/v1/memories/{memory.id}?user_id=u", headers=self.headers, json={"object_value": "allow"})
            self.assertEqual(result.status_code, 200)
        permissions = infer_expression_permissions(self.store.list_permission_events(user_id="u", conversation_id="c"),
            persistent_preferences=self.store.list_memories(user_id="u"))
        self.assertEqual(permissions["profanity"], "allow")
        self.store.save_permission_events(user_id="u", conversation_id="c", events=old_events)
        self.assertEqual(self.store.filter_permission_events(user_id="u", events=old_events), [])
        permissions = infer_expression_permissions(self.store.list_permission_events(user_id="u", conversation_id="c"),
            persistent_preferences=self.store.list_memories(user_id="u"))
        self.assertEqual(permissions["profanity"], "allow")

    def test_forget_blocks_permission_from_an_older_queued_turn_but_allows_new_instruction(self):
        memory = self.add("以后别说脏话")
        queued_id, _ = self.conversations.append_turn(conversation_id="c", user_id="u", user_text="以后别说脏话",
            assistant_text="收到", model_name="test", trace_id="queued", persona_version="test")
        with db.connect(self.path) as conn:
            source_time = datetime.fromisoformat(conn.execute("SELECT created_at FROM messages WHERE id=?", (queued_id,)).fetchone()[0])
        old_events = extract_explicit_preference_events("以后别说脏话", source_message_id=queued_id, created_at=source_time)
        with TestClient(self.app) as client:
            self.assertEqual(client.delete(f"/v1/memories/{memory.id}?user_id=u", headers=self.headers).status_code, 200)
        self.store.save_permission_events(user_id="u", conversation_id="c", events=old_events)
        self.assertEqual(self.store.filter_permission_events(user_id="u", events=old_events), [])
        self.assertNotIn("profanity", infer_expression_permissions(self.store.list_permission_events(user_id="u", conversation_id="c")))
        self.add("以后别说脏话")
        self.assertEqual(infer_expression_permissions(self.store.list_permission_events(user_id="u", conversation_id="c"))["profanity"], "deny")

    def test_structured_address_edit_and_invalid_value_do_not_drop_semantics(self):
        memory = self.add("我叫小明")
        with TestClient(self.app) as client:
            invalid = client.patch(f"/v1/memories/{memory.id}?user_id=u", headers=self.headers, json={"object_value": ""})
            self.assertEqual(invalid.status_code, 422)
            self.assertEqual(self.store.list_address_options(user_id="u")[0].object_value, "小明")
            result = client.patch(f"/v1/memories/{memory.id}?user_id=u", headers=self.headers, json={"object_value": "小林"})
            self.assertEqual(result.status_code, 200)
        current = self.store.list_address_options(user_id="u")[0]
        self.assertEqual(current.object_value, "小林")
        self.assertEqual(current.memory_key, "address:personal_name:小林")

    def test_evidence_history_and_manual_recovery_read_actual_commit_state(self):
        snapshot = ReplySnapshot(request_id="evidence-request", reply_id="evidence-reply", user_id="u",
            conversation_id="evidence-conversation", semantic_text="收到", display_text="收到",
            allowed_performance=AllowedPerformance(), output_preferences=OutputPreferences(), allow_tts=False,
            language="zh", constraints_ref="test", render_profile_revision="test", text_source="validated_semantic")
        self.conversations.append_turn(conversation_id=snapshot.conversation_id, user_id="u", user_text="记住",
            assistant_text="收到", model_name="test", trace_id="evidence-trace", persona_version="test",
            user_message_id="evidence-user", assistant_message_id=snapshot.reply_id, reply_snapshot=snapshot,
            response=ChatResponse(text="收到", request_id=snapshot.request_id, reply_id=snapshot.reply_id),
            request_hash="test", post_turn_payload={})
        retry_id = RequestStateStore(self.path).record_post_turn_failure(request_id=snapshot.request_id,
            trace_id="evidence-trace", stage="post_turn", payload={}, error=RuntimeError("test"))
        attempts = []

        async def recover(failure_id):
            attempts.append(failure_id)
            self.conversations.mark_post_turn_completed(snapshot.request_id)
            with db.connect(self.path) as conn:
                conn.execute("UPDATE post_turn_failures SET status='completed' WHERE id=?", (failure_id,))

        # The test app keeps this agent; background discovery is absent on the fake.
        settings = make_test_settings(self.path.parent)
        agent = FakeChatAgent()
        agent.retry_post_turn = recover
        app = create_desktop_app(settings=settings, chat_agent=agent, desktop_token="token")
        with TestClient(app) as client:
            base = f"/v1/replies/{snapshot.reply_id}"
            self.assertEqual(client.post(base + "/recover-memory?user_id=other", headers=self.headers).status_code, 404)
            state = client.get(base + "/evidence?user_id=u", headers=self.headers).json()
            self.assertEqual(state["post_turn_retry_id"], retry_id)
            history = client.get("/v1/conversations/evidence-conversation?user_id=u", headers=self.headers).json()
            self.assertEqual(history["messages"][1]["post_turn_status"], "pending_retry")
            restored = client.post(base + "/recover-memory?user_id=u", headers=self.headers).json()
            self.assertEqual(restored["post_turn_status"], "completed")
            self.assertIsNone(restored["post_turn_retry_id"])
            history = client.get("/v1/conversations/evidence-conversation?user_id=u", headers=self.headers).json()
            self.assertEqual(history["messages"][1]["post_turn_status"], "completed")
            self.assertEqual(history["messages"][1]["reply_id"], snapshot.reply_id)
        self.assertEqual(attempts, [retry_id])


class ProductionPackageTests(unittest.TestCase):
    def test_candidate_changes_cannot_change_frozen_production(self):
        root = Path(__file__).resolve().parents[1] / "hanser_agent/prompts/persona"
        with tempfile.TemporaryDirectory() as temporary:
            isolated = Path(temporary) / "persona"
            shutil.copytree(root, isolated)
            production = resolve_persona_package(isolated, "persona-v2-production")
            candidate = resolve_persona_package(isolated, "hanser-persona-v2-candidate")
            expected = (production / "core.md").read_bytes()
            (candidate / "core.md").write_text("mutable candidate", encoding="utf-8")
            self.assertNotEqual(candidate, production)
            self.assertEqual((production / "core.md").read_bytes(), expected)
            loaded = PersonaCompiler(production, settings_lifecycle="production")
            pointer = json.loads((isolated / "releases/production.json").read_text(encoding="utf-8"))
            self.assertEqual(loaded.manifest.style_generation, pointer["style_generation"])
            pointer["style_generation"] = "wrong-generation"
            (isolated / "releases/production.json").write_text(json.dumps(pointer), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "disagrees"):
                resolve_persona_package(isolated, "persona-v2-production")


if __name__ == "__main__":
    unittest.main()
