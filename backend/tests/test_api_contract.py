from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from hanser_agent.api import create_app
from hanser_agent.agent.conversation import ConversationStore
from hanser_agent.config import LLMTaskConfig, Settings
from hanser_agent.models import ChatResponse


class FakeChatAgent:
    def __init__(self):
        self.last_request = None

    async def send(self, request):
        self.last_request = request
        return ChatResponse(text=f"收到 {request.message}")


class ApiContractTests(unittest.TestCase):
    def test_import_has_no_configuration_or_database_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing_config = root / "does-not-exist.yml"
            env = os.environ.copy()
            env["HANSER_CONFIG"] = str(missing_config)
            backend_dir = Path(__file__).resolve().parents[1]
            existing_pythonpath = env.get("PYTHONPATH")
            env["PYTHONPATH"] = os.pathsep.join(
                part
                for part in (str(backend_dir), existing_pythonpath)
                if part
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import hanser_agent.api as api; "
                        "assert callable(api.create_app); "
                        "assert not hasattr(api, 'app')"
                    ),
                ],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((root / "documents.db").exists())

    def test_health_and_chat_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = LLMTaskConfig(
                model="test-model",
                max_tokens=64,
                temperature=0.1,
                top_p=0.9,
            )
            settings = Settings(
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
            chat_agent = FakeChatAgent()
            application = create_app(settings=settings, chat_agent=chat_agent)

            with TestClient(application) as client:
                health = client.get("/health")
                chat = client.post(
                    "/v1/chat",
                    json={
                        "conversation_id": "api-test",
                        "message": "晚上好",
                        "persona_settings": {
                            "adult_innuendo_opt_in": True,
                        },
                    },
                )

        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()["ok"])
        self.assertEqual(chat.status_code, 200)
        self.assertTrue(
            chat_agent.last_request.persona_settings.adult_innuendo_opt_in
        )
        self.assertEqual(
            chat.json(),
            {
                "text": "收到 晚上好",
                "keywords": [],
                "anchored": [],
                "sources": [],
            },
        )

    def test_conversation_history_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = LLMTaskConfig(
                model="test-model",
                max_tokens=64,
                temperature=0.1,
                top_p=0.9,
            )
            settings = Settings(
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
            application = create_app(settings=settings, chat_agent=FakeChatAgent())
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

            with TestClient(application) as client:
                history = client.get("/v1/conversations")
                detail = client.get(
                    "/v1/conversations/00000000-0000-4000-8000-000000000001"
                )

        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["conversations"][0]["title"], "晚上好")
        self.assertFalse(history.json()["has_more"])
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(
            [(item["role"], item["content"]) for item in detail.json()["messages"]],
            [("user", "晚上好"), ("assistant", "晚上好呀")],
        )


if __name__ == "__main__":
    unittest.main()
