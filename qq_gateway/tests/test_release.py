import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hanser_qq.corpus import build_corpus
from hanser_qq.headless import create_app
from hanser_qq.releases import backup_database, migrate_style, publish_release, rollback_release, read_json, write_json, runtime_engine_hashes
from test_corpus import corpus_inputs
from test_headless import app_with_models


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "releases/qq-full-corpus-20261018-v8"


@pytest.mark.parametrize("user_trial", [False, True])
@pytest.mark.skipif(not RELEASE.exists(), reason="requires the separately supplied full-corpus release")
def test_full_corpus_payload_reaches_real_context_with_explicit_trial_boundary(config, tmp_path, corpus_inputs, user_trial):
    build_corpus(*corpus_inputs)
    original, calls = app_with_models(config, tmp_path)
    from hanser_agent.desktop_api import build_desktop_chat_agent
    from hanser_agent.memory.store import MemoryStore
    from hanser_qq.runtime_style import attach_release_engine
    settings = original.state.agent.style_tool.settings
    gateway = original.state.agent.responder.model_gateway
    manifest = read_json(RELEASE / "release.json")
    migrate_style(settings.db_path, read_json(RELEASE / "style_rows.json"), manifest)
    agent = build_desktop_chat_agent(settings, gateway, MemoryStore(settings.db_path), persona_dir_override=RELEASE / "persona")
    agent = attach_release_engine(agent, settings, manifest, corpus_inputs[2], user_trial=user_trial)
    app = create_app(config, agent=agent, settings=settings)
    with TestClient(app) as client:
        reply = client.post("/v1/chat", headers={"X-Hanser-QQ-Token": config.backend.token},
                            json={"user_id": "qq:100:200", "conversation_id": "qq:dm:100:200",
                                  "request_id": "full-runtime-positive", "message": "晚上好"})
        assert reply.status_code == 200, reply.text
        with closing(sqlite3.connect(settings.db_path)) as conn:
            trace = json.loads(conn.execute("SELECT snapshot_json FROM reply_snapshots WHERE request_id='full-runtime-positive'").fetchone()[0])["persona_trace"]
        retrieval = trace["retrieval"]
        assert retrieval["injected_style_ids"]
        corpus_trace = retrieval["match_details"]["full_corpus"]
        prompt = json.dumps(calls, ensure_ascii=False)
        assert 'qq-conversation-direct-turn-v1' in prompt
        if user_trial:
            assert '不照搬其中邀请续聊' in prompt
        audit = read_json(tmp_path / 'retrieval_logs/latest.json')
        assert audit['user_message'] == '晚上好'
        assert audit['full_corpus']['hits']
        assert audit['injected']['style_ids'] == retrieval['injected_style_ids']
        assert audit['selected']['style_examples']
        assert '全量库独有的紫色火星茶杯' in json.dumps(audit['full_corpus']['hits'], ensure_ascii=False)
        assert ('全量库独有的紫色火星茶杯' in audit['injected']['prompt_blocks']['style_examples']) == user_trial
        if user_trial:
            sources = corpus_trace["selected_trial_originals"]
            assert set(sources).intersection(retrieval["injected_style_ids"])
            assert "全量库独有的紫色火星茶杯" in prompt
            assert "user_trial_original_A" in prompt and 'review_status=\\"pending\\"' in prompt
            assert not corpus_trace["formal_approval"]
            assert sources["qq-trial:u0"]["source_spans"][0]["start"] == 0
        else:
            assert retrieval["candidate_counts"]["full_corpus_selected"] >= 1
            assert corpus_trace["selected_source_fragments"]
            assert "全量库独有的紫色火星茶杯" not in prompt


def test_publish_rejects_missing_quality_evidence_before_writing(tmp_path):
    root = tmp_path / "product"
    target = root / "releases/test"
    target.mkdir(parents=True)
    write_json(target / "release.json", {"release_id": "test"})
    evidence = tmp_path / "evidence.json"
    write_json(evidence, {"release_id": "test", "decision": "EFFECT_NOT_PROVEN"})
    with pytest.raises(ValueError, match="passing evaluation"):
        publish_release(root, "test", evidence)
    assert not (root / ".runtime").exists()


@pytest.mark.skipif(not RELEASE.exists(), reason="requires the separately supplied full-corpus release")
async def test_full_source_lane_nominates_only_reviewed_payload_and_keeps_turn_gates(config, tmp_path, corpus_inputs, monkeypatch):
    build_corpus(*corpus_inputs)
    original, _ = app_with_models(config, tmp_path)
    from hanser_agent.agent.tools.style_search import StyleSearchResult
    from hanser_agent.models import DialoguePlan
    from hanser_qq.runtime_style import FullCorpusStyleTool
    import hanser_qq.runtime_style as runtime_style
    settings = original.state.agent.style_tool.settings
    manifest = read_json(RELEASE / "release.json")
    migrate_style(settings.db_path, read_json(RELEASE / "style_rows.json"), manifest)
    original.state.agent.style_tool.pinned_generation = manifest["style_generation"]
    tool = FullCorpusStyleTool(original.state.agent.style_tool, corpus_inputs[2])
    example = next(item for item in tool.by_unit.values() if tool.reviews[item.id]["rule_id"] == "greeting")
    unit = tool.reviews[example.id]["unit_id"]
    async def empty(*args, **kwargs):
        return StyleSearchResult(empty_reason="no_applicable_candidate")
    tool.original.search = empty
    monkeypatch.setattr(runtime_style, "search_corpus", lambda *args, **kwargs: {
        "hits": [{"candidate": {"unit_id": unit, "text": "UNREVIEWED_RAW_CANARY"}, "rank": -3.0}]})
    plan = DialoguePlan(intent="chitchat", need_wiki=False, standalone_query="你好", keywords=[],
                        response_mode="casual", fact_sensitivity="low", target_length="short", dialogue_function="other")
    selected = await tool.search("你好", plan)
    assert [item.id for item in selected.examples] == [example.id]
    assert selected.match_details[example.id]["full_A_lexical"]
    assert selected.matched_by[example.id] == ["A"]
    assert "UNREVIEWED_RAW_CANARY" not in selected.model_dump_json()
    assert not (await tool.search('他说“你好”，这不是我在打招呼', plan)).examples
    assert not (await tool.search("你好", plan.model_copy(update={"response_mode": "factual"}))).examples


@pytest.mark.parametrize("user_trial", [False, True])
@pytest.mark.skipif(not RELEASE.exists(), reason="requires the separately supplied full-corpus release")
def test_style_migration_and_matched_rollback_preserve_new_user_data(config, tmp_path, user_trial, monkeypatch):
    root = tmp_path / "product"
    runtime = root / ".runtime"
    backend = runtime / "backend"
    package_root = backend / "hanser_agent/prompts/persona/releases"
    package_root.mkdir(parents=True)
    manifest = read_json(RELEASE / "release.json")
    release_id = manifest["release_id"]
    target = root / "releases" / release_id
    shutil.copytree(RELEASE, target)
    source = config.backend.package_path / "documents.seed.db"
    backup_database(source, runtime / "hanser.db")
    backup_database(source, backend / "documents.seed.db")
    pointer = read_json(config.backend.package_path / "hanser_agent/prompts/persona/releases/production.json")
    write_json(package_root / "production.json", pointer)
    evidence = tmp_path / "evidence.json"
    write_json(evidence, {"release_id": release_id, "decision": "HOLD" if user_trial else "PASS", "hard_failures": 0, "missing_outputs": 0,
                          "engine_sha256": {} if user_trial else runtime_engine_hashes(), "payload_sha256": manifest["payload_sha256"]})
    published = publish_release(root, release_id, evidence, user_trial=user_trial)
    assert published["user_data_preserved"]
    assert publish_release(root, release_id, evidence, user_trial=user_trial)["already_active"]
    active = read_json(runtime / "active_release.json")
    assert active["full_corpus_originals"] == user_trial
    assert active["formal_approval"] != user_trial
    assert active["evaluation_decision"] == ("HOLD" if user_trial else "PASS")
    if user_trial:
        import hanser_qq.releases as releases
        changed = {**runtime_engine_hashes(), 'consumer_revision': 'segmented-delivery'}
        monkeypatch.setattr(releases, 'runtime_engine_hashes', lambda: changed)
        assert publish_release(root, release_id, evidence, user_trial=True)['runtime_refreshed']
        assert read_json(runtime / 'active_release.json')['engine_sha256'] == changed
    with closing(sqlite3.connect(runtime / "hanser.db")) as conn, conn:
        before = conn.execute("SELECT COUNT(*) FROM style_examples").fetchone()[0]
        conn.execute("CREATE TABLE user_data_after_release(note TEXT)")
        conn.execute("INSERT INTO user_data_after_release VALUES('keep latest chat and memory')")
    rollback_release(root)
    with closing(sqlite3.connect(runtime / "hanser.db")) as conn:
        assert conn.execute("SELECT note FROM user_data_after_release").fetchone()[0] == "keep latest chat and memory"
        assert conn.execute("SELECT COUNT(*) FROM style_examples").fetchone()[0] == before
        assert conn.execute("SELECT generation FROM active_index_generations WHERE collection='style_examples'").fetchone()[0] == pointer["style_generation"]
    assert read_json(package_root / "production.json") == pointer
    assert not (runtime / "active_release.json").exists()
