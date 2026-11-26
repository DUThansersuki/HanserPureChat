from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from hanser_agent import db
from hanser_agent.agent.conversation import ConversationStore
from hanser_agent.config import LLMTaskConfig, PerformanceConfig, Settings
from hanser_agent.desktop_api import create_desktop_app
from hanser_agent.models import ChatResponse
from hanser_agent.render_bridge import RenderBridgeFailure
from hanser_agent.responder.performance import (
    AllowedPerformance,
    OutputPreferences,
    ReplySnapshot,
)


class FakeChatAgent:
    def __init__(self):
        self.last_request = None

    async def send(self, request):
        self.last_request = request
        return ChatResponse(text=f"收到 {request.message}")


class FakeRenderBridge:
    def __init__(self, *, failure: RenderBridgeFailure | None = None):
        self.failure = failure

    async def create_voice_job(self, **kwargs):
        if self.failure is not None:
            raise self.failure
        return {
            "schema_version": "1.1",
            "job_id": "job-1",
            "reply_id": kwargs["reply_id"],
            "rendition_id": kwargs["rendition_id"],
            "status": "queued",
            "last_sequence": 0,
            "ready_segments": [],
            "created": True,
        }


def make_test_settings(root: Path) -> Settings:
    task = LLMTaskConfig(
        model="test-model",
        max_tokens=64,
        temperature=0.1,
        top_p=0.9,
    )
    return Settings(
        root=root,
        db_path=root / "documents.db",
        data_dir=root / "data",
        userdict_path=root / "userdict.txt",
        base_url="http://localhost/v1",
        api_key="test",
        default_model="test-model",
        bunny=task,
        prometheus=task,
        hanser=task,
    )


class DesktopApiTests(unittest.TestCase):
    @staticmethod
    def store_speech_snapshot(store: ConversationStore) -> None:
        snapshot = ReplySnapshot(
            request_id="request-voice-1",
            reply_id="assistant-voice-1",
            user_id="local-user",
            conversation_id="conversation-voice-1",
            semantic_text="这是已提交语义正文。",
            display_text="这是已提交语义正文",
            allowed_performance=AllowedPerformance(),
            output_preferences=OutputPreferences(speech=True),
            allow_tts=True,
            language="zh",
            constraints_ref="constraints:test",
            render_profile_revision="hanser-chat-voice-neutral-v1",
            text_source="validated_semantic",
        )
        store.append_turn(
            conversation_id="conversation-voice-1",
            user_id="local-user",
            user_text="请回复",
            assistant_text=snapshot.display_text,
            model_name="test-model",
            trace_id="trace-voice-1",
            persona_version="test-persona",
            user_message_id="user-voice-1",
            assistant_message_id=snapshot.reply_id,
            reply_snapshot=snapshot,
            response=ChatResponse(text=snapshot.display_text, reply_id=snapshot.reply_id),
            request_hash="request-hash-voice-1",
            post_turn_payload={},
        )

    def test_process_token_preserves_speech_and_disables_visual_consumers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_test_settings(Path(tmp))
            chat_agent = FakeChatAgent()
            app = create_desktop_app(
                settings=settings,
                chat_agent=chat_agent,
                desktop_token="desktop-test-token",
            )
            with TestClient(app) as client:
                unauthorized = client.get("/health")
                headers = {"X-Hanser-Desktop-Token": "desktop-test-token"}
                health = client.get("/health", headers=headers)
                chat = client.post(
                    "/v1/chat",
                    headers=headers,
                    json={
                        "conversation_id": "desktop-test",
                        "message": "晚上好",
                        "output_preferences": {
                            "text": True,
                            "speech": True,
                            "dynamic_live2d": True,
                            "offline_performance": True,
                        },
                    },
                )

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["mode"], "chat-voice-desktop")
        self.assertEqual(chat.status_code, 200)
        preferences = chat_agent.last_request.output_preferences
        self.assertTrue(preferences.text)
        self.assertTrue(preferences.speech)
        self.assertFalse(preferences.dynamic_live2d)
        self.assertFalse(preferences.offline_performance)
        self.assertEqual(
            chat_agent.last_request.render_profile_revision,
            settings.performance.render_profile_revision,
        )

    def test_speech_false_remains_the_text_only_request_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_test_settings(Path(tmp))
            settings.performance = PerformanceConfig(
                structured_performance_enabled=True,
                speech_runtime_enabled=True,
                render_profile_revision="hanser-chat-voice-neutral-v1",
            )
            chat_agent = FakeChatAgent()
            app = create_desktop_app(settings=settings, chat_agent=chat_agent)
            with TestClient(app) as client:
                response = client.post(
                    "/v1/chat",
                    json={
                        "conversation_id": "desktop-text-only",
                        "message": "只显示文字",
                        "output_preferences": {"text": True, "speech": False},
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(chat_agent.last_request.output_preferences.speech)

    def test_history_uses_independent_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_test_settings(Path(tmp))
            app = create_desktop_app(settings=settings, chat_agent=FakeChatAgent())
            conversations = ConversationStore(settings.db_path)
            conversations.append_turn(
                conversation_id="00000000-0000-4000-8000-000000000001",
                user_id="local-user",
                user_text="晚上好",
                assistant_text="晚上好呀",
                model_name="test-model",
                trace_id="trace-test",
                persona_version="test-persona",
            )

            with TestClient(app) as client:
                history = client.get("/v1/conversations")
                detail = client.get(
                    "/v1/conversations/00000000-0000-4000-8000-000000000001"
                )

        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["conversations"][0]["title"], "晚上好")
        self.assertEqual(
            [(item["role"], item["content"]) for item in detail.json()["messages"]],
            [("user", "晚上好"), ("assistant", "晚上好呀")],
        )

    def test_voice_job_link_is_written_only_after_runtime_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_test_settings(Path(tmp))
            with db.connect(settings.db_path) as connection:
                db.init_db(connection)
            store = ConversationStore(settings.db_path)
            self.store_speech_snapshot(store)
            app = create_desktop_app(
                settings=settings,
                chat_agent=FakeChatAgent(),
                render_bridge=FakeRenderBridge(),
            )
            with TestClient(app) as client:
                history = client.get("/v1/conversations/conversation-voice-1")
                created = client.post(
                    "/v1/voice/jobs",
                    json={"reply_id": "assistant-voice-1"},
                )
                linked = client.get("/v1/voice/replies/assistant-voice-1")

        assistant = next(
            item for item in history.json()["messages"] if item["role"] == "assistant"
        )
        self.assertEqual(assistant["reply_id"], "assistant-voice-1")
        self.assertEqual(assistant["speech"]["status"], "eligible")
        self.assertEqual(
            assistant["render_profile_revision"],
            "hanser-chat-voice-neutral-v1",
        )
        self.assertEqual(created.status_code, 202)
        self.assertEqual(linked.status_code, 200)
        self.assertEqual(linked.json()["job_id"], "job-1")

    def test_voice_job_failure_does_not_create_success_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_test_settings(Path(tmp))
            with db.connect(settings.db_path) as connection:
                db.init_db(connection)
            store = ConversationStore(settings.db_path)
            self.store_speech_snapshot(store)
            app = create_desktop_app(
                settings=settings,
                chat_agent=FakeChatAgent(),
                render_bridge=FakeRenderBridge(
                    failure=RenderBridgeFailure(503, "voice runtime is unavailable")
                ),
            )
            with TestClient(app) as client:
                created = client.post(
                    "/v1/voice/jobs",
                    json={"reply_id": "assistant-voice-1"},
                )
                linked = client.get("/v1/voice/replies/assistant-voice-1")

        self.assertEqual(created.status_code, 503)
        self.assertEqual(linked.status_code, 404)


if __name__ == "__main__":
    unittest.main()


class DesktopRecoveryTests(unittest.TestCase):
    def test_startup_recovers_pending_and_maintenance_routes_share_token_boundary(self):
        from threading import Event

        class RecoverableAgent(FakeChatAgent):
            def __init__(self):
                super().__init__()
                self.finished = Event()
                self.done = False

            def pending_post_turn_failures(self):
                return [] if self.done else [{"id": "pending"}]

            async def retry_post_turn(self, failure_id):
                if failure_id != "pending":
                    raise KeyError(failure_id)
                self.done = True
                self.finished.set()
                return {"id": failure_id, "status": "completed"}

        with tempfile.TemporaryDirectory() as tmp:
            agent = RecoverableAgent()
            app = create_desktop_app(settings=make_test_settings(Path(tmp)), chat_agent=agent, desktop_token="test-token")
            with TestClient(app) as client:
                self.assertTrue(agent.finished.wait(timeout=2))
                self.assertEqual(client.get("/v1/maintenance/post-turn-failures").status_code, 401)
                headers = {"x-hanser-desktop-token": "test-token"}
                self.assertEqual(client.get("/v1/maintenance/post-turn-failures", headers=headers).json(), [])
                response = client.post("/v1/maintenance/post-turn-failures/pending/retry", headers=headers)
                self.assertEqual(response.json()["status"], "completed")
                self.assertEqual(client.post("/v1/maintenance/post-turn-failures/missing/retry", headers=headers).status_code, 404)
