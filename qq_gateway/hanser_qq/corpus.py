"""Offline, source-grounded A13 candidate index; never a Style approval path."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from collections import Counter
from contextlib import closing
from pathlib import Path

from .locking import service_lock


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _tokens(text: str) -> list[str]:
    import jieba
    return list(dict.fromkeys(
        word.casefold() for word in jieba.cut(text)
        if word.strip() and any(char.isalnum() for char in word)
    ))


def _read(path: Path, inputs: dict):
    content = path.read_bytes()
    # Once per explicit index build, at the frozen-input boundary.
    inputs[str(path.resolve())] = hashlib.sha256(content).hexdigest()
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in content.decode("utf-8").splitlines() if line.strip()]
    return json.loads(content)


def _by_id(rows: list[dict], key: str) -> dict[str, dict]:
    indexed = {row[key]: row for row in rows}
    if len(indexed) != len(rows):
        raise ValueError(f"duplicate {key} in frozen input")
    return indexed


def _check_spans(row: dict, source: dict):
    if row["source_revision"] != source["content_sha256"]:
        raise ValueError(f"source revision mismatch: {row['unit_id']}")
    for span in [*row["source_spans"], *row.get("trigger_source_spans", [])]:
        start, end = span["start"], span["end"]
        if (not 0 <= start < end <= len(source["text"])
                or ("text" in span and source["text"][start:end] != span["text"])):
            raise ValueError(f"source span mismatch: {row['unit_id']}")
    if "".join(span["text"] for span in row["source_spans"]).strip() != row["text"]:
        raise ValueError(f"candidate text differs from source spans: {row['unit_id']}")


def build_corpus(bundle: Path, source: Path, destination: Path) -> dict:
    """Import ALL classified and excluded slices, preserving the original ledgers.

    The staging database replaces only this rebuildable candidate index. Chat,
    Memory, production Style tables and the source artifacts are never opened
    for writing.
    """
    bundle, source, destination = bundle.resolve(), source.resolve(), destination.resolve()
    inputs: dict[str, str] = {}
    documents = _by_id(_read(source / "sources.snapshot.jsonl", inputs), "source_id")
    raw = _by_id(_read(source / "candidates.jsonl", inputs), "unit_id")
    tagged = _by_id(_read(bundle / "tagged_candidates.jsonl", inputs), "unit_id")
    ledgers = {
        "mechanical_exclusion": _read(bundle / "preparation_exclusions.jsonl", inputs),
        "duplicate": _read(bundle / "duplicate_lineage.jsonl", inputs),
        "quarantine": _read(bundle / "input_quarantine.jsonl", inputs),
    }
    coverage = _by_id(_read(bundle / "source_coverage.jsonl", inputs), "source_id")
    selection = _read(bundle / "selection_manifest.json", inputs)
    validation = _read(bundle / "full_validation.json", inputs)
    labels = _read(bundle / "labels.frozen.json", inputs)
    rule = bundle / "rule.frozen.txt"
    inputs[str(rule)] = hashlib.sha256(rule.read_bytes()).hexdigest()
    if any(doc["split"] != "corpus_build" for doc in documents.values()):
        raise ValueError("candidate index must not include holdout or excluded source documents")
    if (validation["remaining"] != 0 or len(tagged) != validation["total"]
            or len(tagged) != selection["selected"] or len(raw) != selection["all_candidates"]
            or len(documents) != selection["input_documents"] or set(coverage) != set(documents)):
        raise ValueError("incomplete full-corpus bundle")
    buckets = {uid: "classified" for uid in tagged}
    ledger_by_id = {}
    for kind, rows in ledgers.items():
        for row in rows:
            uid = row["unit_id"]
            if uid in buckets:
                raise ValueError(f"overlapping corpus dispositions: {uid}")
            if kind == "duplicate" and row["canonical_unit_id"] not in raw:
                raise ValueError(f"missing duplicate canonical source: {uid}")
            buckets[uid], ledger_by_id[uid] = kind, row
    if set(buckets) != set(raw):
        raise ValueError("full-corpus dispositions do not account for every source slice")
    counts: Counter = Counter()
    per_source: dict[str, Counter] = {sid: Counter() for sid in documents}
    for uid, row in raw.items():
        sid, bucket = row["source_id"], buckets[uid]
        if sid not in documents:
            raise ValueError(f"missing source document: {sid}")
        counts[bucket] += 1
        per_source[sid][bucket] += 1
        if bucket == "classified":
            candidate = tagged[uid]
            for field in ("source_id", "source_revision", "text", "source_spans", "trigger_source_spans",
                          "reaction_source_start_char", "reaction_source_end_char", "explicit_source_trigger"):
                if candidate[field] != row[field]:
                    raise ValueError(f"classification changed {field}: {uid}")
            _check_spans(candidate, documents[sid])
            if candidate["label_number"] not in range(1, 14):
                raise ValueError(f"invalid A13 label: {uid}")
    for sid, expected in coverage.items():
        actual = per_source[sid]
        if (sum(actual.values()) != expected["raw_candidates"]
                or actual["classified"] != expected["labeled_unique_A"]
                or actual["mechanical_exclusion"] != sum(expected["mechanical_exclusions"].values())
                or actual["duplicate"] != expected["duplicate_aliases"]
                or actual["quarantine"] != expected["known_input_quarantine"]):
            raise ValueError(f"source coverage mismatch: {sid}")
    label_counts = Counter(row["label_number"] for row in tagged.values())
    manifest = {
        "schema_version": 1,
        "generation": "a13-" + hashlib.sha256(_json(inputs).encode("utf-8")).hexdigest()[:16],
        "bundle": str(bundle), "source": str(source), "input_sha256": inputs,
        "source_documents": len(documents), "classified_documents": sum(bool(c["classified"]) for c in per_source.values()),
        "source_slices": len(raw), "classified": len(tagged), "dispositions": dict(counts),
        "label_counts": {str(key): value for key, value in sorted(label_counts.items())},
        "labels_1_to_12": len(tagged) - label_counts[13], "label_13": label_counts[13],
        "classification_only": True, "runtime_injection_enabled": False,
        "runtime_block_reason": "payload_and_applicability_review_and_release_evaluation_required",
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with service_lock(destination.with_suffix(".lock")):
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".staging", delete=False) as handle:
            staging = Path(handle.name)
        try:
            with closing(sqlite3.connect(staging)) as conn, conn:
                conn.executescript("""
                    CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE sources(source_id TEXT PRIMARY KEY, record_json TEXT NOT NULL, coverage_json TEXT NOT NULL);
                    CREATE TABLE units(unit_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, bucket TEXT NOT NULL,
                        label_number INTEGER, record_json TEXT NOT NULL, disposition_json TEXT);
                    CREATE INDEX units_bucket_label ON units(bucket, label_number);
                    CREATE VIRTUAL TABLE candidate_fts USING fts5(unit_id UNINDEXED, answer, trigger_text);
                """)
                conn.executemany("INSERT INTO sources VALUES(?,?,?)", (
                    (sid, _json(doc), _json(coverage[sid])) for sid, doc in documents.items()
                ))
                for uid, original in raw.items():
                    row, bucket = tagged.get(uid, original), buckets[uid]
                    conn.execute("INSERT INTO units VALUES(?,?,?,?,?,?)", (
                        uid, row["source_id"], bucket, row.get("label_number"), _json(row),
                        _json(ledger_by_id[uid]) if uid in ledger_by_id else None,
                    ))
                    if bucket == "classified":
                        # A text is candidate-search evidence, never a fabricated Q.
                        trigger = row["explicit_source_trigger"] if row["qa_status"] == "source_pair" else ""
                        conn.execute("INSERT INTO candidate_fts VALUES(?,?,?)", (
                            uid, " ".join(_tokens(row["text"])), " ".join(_tokens(trigger)),
                        ))
                conn.execute("INSERT INTO metadata VALUES('manifest',?)", (_json(manifest),))
                conn.execute("INSERT INTO metadata VALUES('labels',?)", (_json(labels),))
                conn.execute("INSERT INTO candidate_fts(candidate_fts) VALUES('optimize')")
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("candidate index integrity check failed")
            staging.replace(destination)
        finally:
            staging.unlink(missing_ok=True)
    return manifest


def corpus_status(path: Path) -> dict:
    if not path.is_file():
        return {"indexed": False, "runtime_injection_enabled": False}
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        manifest = json.loads(conn.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])
    # Health never exposes source text, filesystem layout or the full input ledger.
    return {"indexed": True, **{key: value for key, value in manifest.items()
                               if key not in {"input_sha256", "bundle", "source"}}}


def search_corpus(path: Path, query: str, *, limit: int = 10, label: int | None = None,
                  include_not_recommended: bool = False) -> dict:
    if not 1 <= limit <= 50 or (label is not None and label not in range(1, 14)):
        raise ValueError("limit must be 1..50 and label must be 1..13")
    if not path.is_file():
        raise FileNotFoundError("candidate index missing; run corpus-build first")
    terms = _tokens(query)
    hits = []
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        if terms:
            expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
            rows = conn.execute("""
                SELECT u.record_json, bm25(candidate_fts, 0, 1, 1.5) AS rank
                FROM candidate_fts JOIN units u ON u.unit_id=candidate_fts.unit_id
                WHERE candidate_fts MATCH ? AND (? IS NULL OR u.label_number=?)
                    AND (? OR u.label_number<>13)
                ORDER BY rank, u.unit_id LIMIT ?
            """, (expression, label, label, include_not_recommended or label == 13, limit)).fetchall()
            for row in rows:
                record = json.loads(row["record_json"])
                hits.append({"candidate": record, "rank": row["rank"], "runtime_eligible": False,
                             "block_reason": "classification_is_not_runtime_approval"})
    return {"candidate_only": True, "runtime_injection_enabled": False, "hits": hits}
