import hashlib
import json
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from hanser_qq.corpus import build_corpus, corpus_status, search_corpus
from test_headless import app_with_models


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


@pytest.fixture
def corpus_inputs(tmp_path):
    source, bundle = tmp_path / "source", tmp_path / "bundle"
    source.mkdir()
    bundle.mkdir()
    texts = ["晚上好呀，全量库独有的紫色火星茶杯", "我喜欢星空", "小明今天来过", "晚上好呀，全量库独有的紫色火星茶杯", "节目预告", "占位"]
    content = "\n".join(texts)
    revision = hashlib.sha256(content.encode("utf-8")).hexdigest()
    documents = [{"source_id": "DOC-1", "text": content, "content_sha256": revision,
                  "split": "corpus_build", "filename": "first.docx"},
                 {"source_id": "DOC-2", "text": "未知说话人", "content_sha256": "second",
                  "split": "corpus_build", "filename": "second.docx"}]
    rows = []
    offset = 0
    for index, text in enumerate(texts):
        span = {"start": offset, "end": offset + len(text), "text": text}
        rows.append({"unit_id": f"u{index}", "source_id": "DOC-1", "source_revision": revision,
                     "text": text, "source_spans": [span], "trigger_source_spans": [],
                     "reaction_source_start_char": offset, "reaction_source_end_char": offset + len(text),
                     "explicit_source_trigger": "", "qa_status": "a_only", "review_status": "pending",
                     "runtime_eligible": False, "attribution_basis": "source_prior"})
        offset += len(text) + 1
    rows.append({"unit_id": "u6", "source_id": "DOC-2", "text": "未知说话人"})
    classified = [row | {"label_number": label, "label_name": str(label)} for row, label in zip(rows, (9, 2, 13))]
    write_rows(source / "sources.snapshot.jsonl", documents)
    write_rows(source / "candidates.jsonl", rows)
    write_rows(bundle / "tagged_candidates.jsonl", classified)
    write_rows(bundle / "preparation_exclusions.jsonl", [
        {"unit_id": "u4", "source_id": "DOC-1", "reason": "stage_direction"},
        {"unit_id": "u6", "source_id": "DOC-2", "reason": "unknown_speaker"},
    ])
    write_rows(bundle / "duplicate_lineage.jsonl", [{"unit_id": "u3", "canonical_unit_id": "u0"}])
    write_rows(bundle / "input_quarantine.jsonl", [rows[5] | {"quarantine_reason": "recording_marker"}])
    coverage = [{"source_id": "DOC-1", "raw_candidates": 6, "labeled_unique_A": 3,
                 "mechanical_exclusions": {"stage_direction": 1}, "duplicate_aliases": 1, "known_input_quarantine": 1},
                {"source_id": "DOC-2", "raw_candidates": 1, "labeled_unique_A": 0,
                 "mechanical_exclusions": {"unknown_speaker": 1}, "duplicate_aliases": 0, "known_input_quarantine": 0}]
    write_rows(bundle / "source_coverage.jsonl", coverage)
    (bundle / "selection_manifest.json").write_text(json.dumps({"selected": 3, "all_candidates": 7, "input_documents": 2}))
    (bundle / "full_validation.json").write_text(json.dumps({"remaining": 0, "total": 3}))
    (bundle / "labels.frozen.json").write_text(json.dumps({str(i): {"name": str(i)} for i in range(1, 14)}))
    (bundle / "rule.frozen.txt").write_text("frozen classification rule")
    return bundle, source, tmp_path / "full_corpus.sqlite3"


def test_all_sources_and_dispositions_are_retained_without_approving_candidates(corpus_inputs):
    bundle, source, destination = corpus_inputs
    manifest = build_corpus(bundle, source, destination)
    assert manifest["source_documents"] == 2
    assert manifest["classified_documents"] == 1
    assert manifest["source_slices"] == 7 and manifest["classified"] == 3
    assert manifest["dispositions"] == {"classified": 3, "duplicate": 1, "mechanical_exclusion": 2, "quarantine": 1}
    assert not manifest["runtime_injection_enabled"]
    with closing(sqlite3.connect(destination)) as conn:
        assert conn.execute("SELECT count(*) FROM units").fetchone()[0] == 7
        assert conn.execute("SELECT count(*) FROM sources").fetchone()[0] == 2
        duplicate = json.loads(conn.execute("SELECT disposition_json FROM units WHERE unit_id='u3'").fetchone()[0])
        assert duplicate["canonical_unit_id"] == "u0"
    hits = search_corpus(destination, '晚上好呀 " OR *', label=9)["hits"]
    assert hits and all(not hit["runtime_eligible"] for hit in hits)
    candidate = hits[0]["candidate"]
    assert candidate["review_status"] == "pending"
    assert candidate["attribution_basis"] == "source_prior"
    assert candidate["source_spans"] and candidate["explicit_source_trigger"] == ""
    assert not search_corpus(destination, "小明")["hits"]
    assert search_corpus(destination, "小明", label=13)["hits"][0]["candidate"]["label_number"] == 13
    status = corpus_status(destination)
    assert status["indexed"] and "input_sha256" not in status
    assert build_corpus(bundle, source, destination)["generation"] == manifest["generation"]


@pytest.mark.parametrize("fault", ["holdout", "span", "coverage", "partial", "revision"])
def test_failed_rebuild_preserves_existing_index(corpus_inputs, fault):
    bundle, source, destination = corpus_inputs
    original = build_corpus(bundle, source, destination)
    if fault == "holdout":
        path, field, value = source / "sources.snapshot.jsonl", "split", "holdout"
    elif fault == "coverage":
        path, field, value = bundle / "source_coverage.jsonl", "raw_candidates", 100
    elif fault == "revision":
        path, field, value = source / "sources.snapshot.jsonl", "content_sha256", "changed"
    else:
        path, field, value = bundle / "tagged_candidates.jsonl", "text", "forged source text"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if fault == "partial":
        rows.pop()
    else:
        rows[0][field] = value
    write_rows(path, rows)
    with pytest.raises(ValueError):
        build_corpus(bundle, source, destination)
    assert corpus_status(destination)["generation"] == original["generation"]
    assert search_corpus(destination, "星空")["hits"]


def test_candidate_search_does_not_call_model_or_leak_into_reply(config, tmp_path, corpus_inputs):
    build_corpus(*corpus_inputs)
    app, calls = app_with_models(config, tmp_path)
    headers = {"X-Hanser-QQ-Token": config.backend.token}
    with TestClient(app) as client:
        assert client.get("/health", headers=headers).json()["corpus"]["classified"] == 3
        result = client.get("/v1/maintenance/corpus/search", headers=headers, params={"q": "晚上好呀"})
        assert result.status_code == 200 and result.json()["hits"]
        assert not calls
        reply = client.post("/v1/chat", headers=headers, json={"user_id": "qq:100:200", "conversation_id": "qq:dm:100:200",
                            "request_id": "qq-candidate-boundary", "message": "晚上好呀"})
        assert reply.status_code == 200, reply.text
        assert calls and "全量库独有的紫色火星茶杯" not in json.dumps(calls, ensure_ascii=False)
