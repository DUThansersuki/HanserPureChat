from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


PATTERNS = {
    "numeric_or_ratio": re.compile(r"\d|[一二三四五六七八九十百]+(?:成|分之)|百分之"),
    "time_or_date": re.compile(r"(?:今天|昨天|明天|现在|最近|当时|\d{4}年|\d{1,2}月|\d{1,2}[号日点])"),
    "third_party_generalization": re.compile(r"(?:女生|男生|很多人|大多数|一般人|他们|她们).{0,18}(?:都|会|是|有|没有)"),
    "reality_state": re.compile(r"(?:已经|正在|现在|目前|以前|上次|小时候).{0,24}(?:在|做|去|来|吃|喝|看|玩|病|痛|工作|录|唱)"),
    "opinion_followed_by_fact": re.compile(r"我觉得.{0,30}(?:是|会|有|都|已经|现在|以前|百分之|\d)"),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    corpus_path = run_dir / "style_examples.reviewed.jsonl"
    decisions_path = run_dir / "review_decisions.jsonl"
    db_path = run_dir / "candidate.db"
    rows = [json.loads(line) for line in corpus_path.read_text(encoding="utf-8").splitlines() if line]

    risky: dict[str, list[str]] = {}
    for row in rows:
        if "style_runtime" not in row.get("runtime_scope", []):
            continue
        reasons = [name for name, pattern in PATTERNS.items() if pattern.search(row["character_response"])]
        if reasons:
            risky[str(row["id"])] = reasons
    if "style:2002" not in risky:
        raise RuntimeError("required confirmed defect style:2002 was not selected")

    stamp = datetime.now(timezone.utc).isoformat()
    source_digest = hashlib.sha256(
        json.dumps(risky, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    generation = f"persona-v2-style-safe-{source_digest[:16]}"
    for row in rows:
        item_id = str(row["id"])
        if item_id in risky:
            row["review_status"] = "quarantined"
            row["schema_review_status"] = "rejected"
            row["payload_class"] = "third_party_fact"
            row["runtime_scope"] = []
            row["index_generation"] = None
            row["review_notes"] = (
                "deterministic high-exposure factual-payload quarantine; "
                + ",".join(risky[item_id])
            )
        elif "style_runtime" in row.get("runtime_scope", []):
            row["index_generation"] = generation
    corpus_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )

    decision_lines = []
    if decisions_path.exists():
        decision_lines = decisions_path.read_text(encoding="utf-8").splitlines()
    for item_id, reasons in sorted(risky.items()):
        decision_lines.append(json.dumps({
            "item_id": int(item_id.split(":", 1)[1]),
            "decision": "quarantined",
            "dimension": "high_exposure_fact_payload_0909",
            "reason_codes": reasons,
            "reviewer_kind": "deterministic_fail_closed_rule",
            "reviewer_id": "persona_v2_quarantine_fact_payloads_v1",
            "decided_at": stamp,
        }, ensure_ascii=False, separators=(",", ":")))
    decisions_path.write_text("\n".join(decision_lines) + "\n", encoding="utf-8")

    risky_numeric = [int(item_id.split(":", 1)[1]) for item_id in risky]
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        active = conn.execute(
            "SELECT generation, revision FROM active_index_generations WHERE collection='style_examples'"
        ).fetchone()
        if active is None:
            raise RuntimeError("candidate DB has no active style generation")
        old_generation = str(active["generation"])
        placeholders = ",".join("?" for _ in risky_numeric)
        for item_id in risky_numeric:
            row = conn.execute("SELECT metadata_json FROM style_examples WHERE id=?", (item_id,)).fetchone()
            metadata = json.loads(str(row["metadata_json"]))
            metadata.update({
                "payload_class": "third_party_fact",
                "schema_review_status": "rejected",
                "runtime_scope": [],
                "payload_quarantine_reasons": risky[f"style:{item_id}"],
            })
            conn.execute(
                "UPDATE style_examples SET review_status='quarantined', review_notes=?, "
                "metadata_json=?, index_generation=NULL WHERE id=?",
                (
                    "deterministic high-exposure factual-payload quarantine; "
                    + ",".join(risky[f"style:{item_id}"]),
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    item_id,
                ),
            )
        conn.execute(
            "INSERT INTO vector_embeddings_v2(collection,generation,item_id,model,dimensions,vector) "
            "SELECT collection,?,item_id,model,dimensions,vector FROM vector_embeddings_v2 "
            f"WHERE collection='style_examples' AND generation=? AND CAST(item_id AS INTEGER) NOT IN ({placeholders})",
            [generation, old_generation, *risky_numeric],
        )
        item_count = conn.execute(
            "SELECT COUNT(*) FROM vector_embeddings_v2 WHERE collection='style_examples' AND generation=?",
            (generation,),
        ).fetchone()[0]
        old_meta = conn.execute(
            "SELECT model, dimensions, config_hash FROM index_generations "
            "WHERE collection='style_examples' AND generation=?",
            (old_generation,),
        ).fetchone()
        conn.execute("UPDATE index_generations SET status='retired' WHERE collection='style_examples' AND generation=?", (old_generation,))
        conn.execute(
            "INSERT INTO index_generations(collection,generation,status,model,dimensions,source_revision,config_hash,item_count,created_at,published_at) "
            "VALUES('style_examples',?,'active',?,?,?,?,?,datetime('now'),datetime('now'))",
            (generation, old_meta["model"], old_meta["dimensions"], f"fact_payload_quarantine:{source_digest}", old_meta["config_hash"], item_count),
        )
        conn.execute(
            "UPDATE active_index_generations SET generation=?, revision=?, updated_at=datetime('now') WHERE collection='style_examples'",
            (generation, int(active["revision"]) + 1),
        )
        conn.execute(
            "UPDATE style_examples SET index_generation=? WHERE review_status='approved' AND id NOT IN (" + placeholders + ")",
            [generation, *risky_numeric],
        )
        conn.commit()
    print(json.dumps({"quarantined": len(risky), "active_generation": generation, "item_count": item_count}, ensure_ascii=False))


if __name__ == "__main__":
    main()
