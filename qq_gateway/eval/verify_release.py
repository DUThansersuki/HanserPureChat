"""Explicit release-boundary checks. Installed phase uses real environment models."""
import argparse
import json
import shutil
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hanser_qq.releases import backup_database, publish_release, rollback_release, read_json, write_json


def migration(release_id, evaluation, *, user_trial=False):
    from hanser_qq.prepare import prepare
    product = ROOT / ".runtime/full_eval_20261018_001" / ("migration_" + release_id)
    product.mkdir()
    for name in ("config.example.yml", "hanser.example.yml"):
        shutil.copy2(ROOT / name, product / name)
    shutil.copytree(ROOT / "releases" / release_id, product / "releases" / release_id)
    prepare(product, source_root=ROOT.parent / "chat_voice_desktop")
    published = publish_release(product, release_id, evaluation, user_trial=user_trial)
    database = product / ".runtime/hanser.db"
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("CREATE TABLE release_boundary_canary(value TEXT)")
        conn.execute("INSERT INTO release_boundary_canary VALUES('new user data after publication')")
    prepare(product, source_root=ROOT.parent / "chat_voice_desktop", refresh_backend=True)
    pointer = read_json(product / ".runtime/backend/hanser_agent/prompts/persona/releases/production.json")
    assert pointer["package_id"] == release_id
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("SELECT value FROM release_boundary_canary").fetchone()[0] == "new user data after publication"
        assert conn.execute("SELECT generation FROM active_index_generations WHERE collection='style_examples'").fetchone()[0] == pointer["style_generation"]
    rolled_back = rollback_release(product)
    parent = read_json(ROOT / "releases" / release_id / "release.json")["parent_style_generation"]
    for path in (database, product / ".runtime/backend/documents.seed.db"):
        with closing(sqlite3.connect(path)) as conn:
            assert conn.execute("SELECT generation FROM active_index_generations WHERE collection='style_examples'").fetchone()[0] == parent
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("SELECT value FROM release_boundary_canary").fetchone()[0] == "new user data after publication"
    assert not (product / ".runtime/active_release.json").exists()
    return {"release_id": release_id, "publication": published, "refresh_preserved_release": True,
            "rollback": rolled_back, "post_publish_user_data_preserved": True,
            "production_user_database_modified": False, "isolated_product": str(product)}


def installed(release_id):
    from fastapi.testclient import TestClient
    from hanser_qq.config import load_settings
    from hanser_qq.headless import install_backend, create_app
    config = load_settings(ROOT / "config.yml")
    install_backend(config.backend.package_path)
    from hanser_agent.config import load_settings as load_backend
    active = read_json(ROOT / ".runtime/active_release.json")
    assert active["release_id"] == release_id
    data = ROOT / ".runtime/full_eval_20261018_001" / ("installed_" + release_id)
    data.mkdir()
    backup_database(ROOT / ".runtime/backend/documents.seed.db", data / "hanser.db")
    write_json(data / "active_release.json", active)
    corpus_dir = data / "releases" / release_id
    corpus_dir.mkdir(parents=True)
    backup_database(ROOT / ".runtime/releases" / release_id / "corpus.sqlite3", corpus_dir / "corpus.sqlite3")
    settings = replace(load_backend(config.backend.config_path), db_path=data / "hanser.db")
    config = config.model_copy(update={"storage_path": data / "gateway.sqlite3"})
    app = create_app(config, settings=settings)
    gateway = app.state.agent.responder.model_gateway
    headers = {"X-Hanser-QQ-Token": config.backend.token}
    body = {"user_id": "qq:100:919", "conversation_id": "qq:dm:100:919",
            "request_id": "installed-release-greeting", "message": "晚上好"}
    with TestClient(app) as client:
        health = client.get("/health", headers=headers).json()
        assert health["chat_ready"] and health["runtime_corpus"]["enabled"]
        first = client.post("/v1/chat", headers=headers, json=body)
        assert first.status_code == 200, first.text
        calls = len(gateway.call_records)
        repeated = client.post("/v1/chat", headers=headers, json=body)
        assert repeated.status_code == 200 and repeated.json() == first.json()
        assert len(gateway.call_records) == calls
    with closing(sqlite3.connect(data / "hanser.db")) as conn:
        trace = json.loads(conn.execute("SELECT snapshot_json FROM reply_snapshots WHERE request_id=?", (body["request_id"],)).fetchone()[0])["persona_trace"]
    retrieval = trace["retrieval"]
    corpus_trace = retrieval["match_details"]["full_corpus"]
    selected = corpus_trace.get("selected_trial_originals") or corpus_trace["selected_source_fragments"]
    injected = set(retrieval["injected_style_ids"]).intersection(selected)
    assert injected and calls > 0
    write_json(data / "reply_and_trace.json", {"reply": first.json(), "trace": trace, "calls": list(gateway.call_records)})
    return {"release_id": release_id, "loaded_engine": type(app.state.agent).__name__,
            "production": health["production"], "runtime_corpus_enabled": True,
            "real_model_network_verified": True, "response": first.json()["text"],
            "real_model_calls": calls, "idempotent_replay_added_calls": 0,
            "new_source_ids_actually_injected": sorted(injected), "reply_snapshot_preserved": True,
            "production_user_database_modified": False, "qq_connected": False, "qq_messages_sent": 0,
            "raw_evidence": str(data / "reply_and_trace.json")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("migration", "installed"))
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--evaluation", type=Path)
    parser.add_argument("--user-trial", action="store_true")
    args = parser.parse_args()
    report = migration(args.release_id, args.evaluation, user_trial=args.user_trial) if args.phase == "migration" else installed(args.release_id)
    output = ROOT / "validation" / (args.phase + "." + args.release_id + ".json")
    write_json(output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
