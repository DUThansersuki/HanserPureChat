from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from hanser_agent.agent.conversation import ConversationStore
from hanser_agent.config import LLMTaskConfig, Settings
from hanser_agent.desktop_api import create_desktop_app
from hanser_agent.models import ChatResponse


class FakeChatAgent:
    def __init__(self):
        self.last_request = None

    async def send(self, request):
        self.last_request = request
        return ChatResponse(text=f"收到 {request.message}")


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
    def test_process_token_and_chat_only_preferences(self) -> None:
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
        self.assertEqual(health.json()["mode"], "pure-chat-desktop")
        self.assertEqual(chat.status_code, 200)
        preferences = chat_agent.last_request.output_preferences
        self.assertTrue(preferences.text)
        self.assertFalse(preferences.speech)
        self.assertFalse(preferences.dynamic_live2d)
        self.assertFalse(preferences.offline_performance)

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


if __name__ == "__main__":
    unittest.main()
