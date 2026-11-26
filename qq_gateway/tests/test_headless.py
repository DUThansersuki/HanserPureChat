import json
import shutil
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from hanser_qq.headless import create_app, install_backend
from hanser_qq.events import compose_batch
from hanser_qq.readiness import check_prepared
from test_events import incoming


def app_with_models(config, tmp_path, *, reply_text="好，我记住了。"):
    install_backend(config.backend.package_path)
    from hanser_agent.config import load_settings
    from hanser_agent.model_gateway import ModelGateway
    import yaml
    raw = yaml.safe_load((Path(__file__).resolve().parents[1] / "hanser.example.yml").read_text(encoding="utf-8"))
    raw["llm"] |= {"base_url": "http://model.invalid/v1", "api_key": "fake", "model": "fake"}
    raw["data"] = {"db_path": str(tmp_path / "hanser.db"), "userdict_path": str(config.backend.package_path / "userdict.txt")}
    config.backend.config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    shutil.copy2(config.backend.package_path / "documents.seed.db", tmp_path / "hanser.db")
    settings = load_settings(config.backend.config_path)
    calls = []
    def model(request):
        payload = json.loads(request.content)
        calls.append(payload)
        content = json.dumps({"intent": "chitchat", "mode": "casual", "length": "short", "dialogue_function": "share"}) if payload.get("response_format") else (reply_text(payload) if callable(reply_text) else reply_text)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 10, "completion_tokens": 8}})
    gateway = ModelGateway({"planner": settings.planner, "responder": settings.responder},
                           client=httpx.AsyncClient(transport=httpx.MockTransport(model)))
    return create_app(config, settings=settings, gateway=gateway), calls


def test_real_agent_snapshot_idempotency_memory_and_group_isolation(config, tmp_path):
    app, calls = app_with_models(config, tmp_path)
    headers = {"X-Hanser-QQ-Token": config.backend.token}
    with TestClient(app) as client:
        assert client.get("/health").status_code == 401
        health = client.get("/health", headers=headers).json()
        assert health["chat_ready"] and health["mode"] == "qq-headless"
        assert health["production"]["frozen_production"]
        assert health["production"]["style_static_eligible"] <= health["production"]["style_outer_approved"]
        assert health["corpus"] == {"indexed": False, "runtime_injection_enabled": False}
        assert client.get("/v1/maintenance/corpus/search?q=你好").status_code == 401
        assert client.get("/v1/maintenance/corpus/search?q=你好", headers=headers).status_code == 404
        private = incoming(text="我喜欢独立游戏")
        payload = {"user_id": private.user_id, "conversation_id": private.conversation_id,
                   "request_id": "qq-test-1", "message": private.text}
        first = client.post("/v1/chat", headers=headers, json=payload)
        assert first.status_code == 200, first.text
        before = len(calls)
        replay = client.post("/v1/chat", headers=headers, json=payload)
        assert replay.json() == first.json() and len(calls) == before
        assert first.json()["reply_id"]
        agent = app.state.agent
        assert agent.conversations.get_reply_snapshot(first.json()["reply_id"], user_id=private.user_id)
        assert agent.memory_store.list_memories(user_id=private.user_id)
        group = incoming(kind="group", addressed=True)
        assert not agent.memory_store.list_memories(user_id=group.user_id)
        conflict = client.post("/v1/chat", headers=headers, json=payload | {"message": "换一段"})
        assert conflict.status_code == 409
        mismatch = client.post("/v1/chat", headers=headers, json=payload | {"user_id": group.user_id})
        assert mismatch.status_code == 422
        missing = client.post("/v1/chat", headers=headers, json={"message": "你好"})
        assert missing.status_code == 422
        # A changed generation must immediately revoke readiness; don't report
        # the Persona's expected generation as proof of the actual database.
        from hanser_agent import db
        with db.connect(tmp_path / "hanser.db") as conn:
            conn.execute("UPDATE active_index_generations SET generation='incompatible' WHERE collection='style_examples'")
        assert not client.get("/health", headers=headers).json()["chat_ready"]


def test_quoted_preference_is_not_extracted_as_current_users(config):
    install_backend(config.backend.package_path)
    from hanser_agent.memory.extractor import MemoryCandidateExtractor
    message = incoming(text="这个是别人说的")
    message.quote = '我喜欢榴莲，以后叫我小明'
    message.reply_to = "88"
    candidates = MemoryCandidateExtractor().extract(user_id=message.user_id,
        conversation_id=message.conversation_id, message_id="m1", message=compose_batch([message]))
    assert not candidates


def test_offline_check_does_not_construct_model_clients(config, tmp_path, monkeypatch):
    app, calls = app_with_models(config, tmp_path)
    import hanser_agent.model_gateway as model_gateway
    def forbidden(*args, **kwargs):
        raise AssertionError("offline check must not construct a model gateway")
    monkeypatch.setattr(model_gateway, "build_model_gateway", forbidden)
    result = check_prepared(config)
    assert result["production"]["chat_ready"]
    assert result["model_configuration_valid"] and not result["model_network_verified"]
    assert not result["qq_delivery_verified"] and not calls
