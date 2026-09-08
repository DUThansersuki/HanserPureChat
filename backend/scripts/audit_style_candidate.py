"""Deterministic provenance/defect audit for a Slice 2 candidate generation."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path


CONFIRMED_DEFECT_IDS = {1518, 1308, 1513, 1151, 1189, 1394, 1417, 1506, 919}
STRUCTURAL_MARKER = re.compile(r"弹幕[：:]|(?:海|凉果|凉菓|于尔丹|憨憨)[：:]")


def connection(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-database", type=Path, required=True)
    parser.add_argument("--candidate-database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with connection(args.baseline_database) as baseline:
        legacy = baseline.execute(
            "SELECT id,prompt,response,scene,source_ref FROM style_examples ORDER BY id"
        ).fetchall()
    with connection(args.candidate_database) as candidate:
        rows = candidate.execute("SELECT * FROM style_examples ORDER BY id").fetchall()
        documents = {
            int(row["id"]): str(row["content"])
            for row in candidate.execute("SELECT id,content FROM documents")
        }
        migrated = candidate.execute(
            "SELECT 1 FROM schema_migrations WHERE version=4"
        ).fetchone()
        vector_count = candidate.execute(
            "SELECT count(*) FROM vector_embeddings_v2 v JOIN active_index_generations p "
            "ON p.collection=v.collection AND p.generation=v.generation "
            "WHERE v.collection='style_examples'"
            if migrated else
            "SELECT count(*) FROM vector_embeddings WHERE collection='style_examples'"
        ).fetchone()[0]

    replay_failures = []
    for row in rows:
        content = documents.get(int(row["source_document_id"] or -1))
        if content is None:
            replay_failures.append({"id": row["id"], "reason": "missing_document"})
            continue
        lines = content.splitlines()
        start = int(row["source_start_line"] or 0)
        end = int(row["source_end_line"] or 0)
        if start < 1 or end < start or end > len(lines):
            replay_failures.append({"id": row["id"], "reason": "invalid_lines"})
            continue
        if "\n".join(lines[start - 1:end]) != str(row["source_raw_text"]):
            replay_failures.append({"id": row["id"], "reason": "raw_replay_mismatch"})
        elif row["prompt"] != row["source_user_turn"] or row["response"] != row["source_response_turn"]:
            replay_failures.append({"id": row["id"], "reason": "clean_turn_mismatch"})

    candidate_pairs = {(str(row["prompt"]), str(row["response"])) for row in rows}
    confirmed = [row for row in legacy if int(row["id"]) in CONFIRMED_DEFECT_IDS]
    structural = [row for row in legacy if STRUCTURAL_MARKER.search(str(row["response"]))]
    confirmed_disposition = [
        {
            "legacy_id": int(row["id"]),
            "exact_bad_pair_absent": (str(row["prompt"]), str(row["response"])) not in candidate_pairs,
            "source_ref": row["source_ref"],
        }
        for row in confirmed
    ]
    structural_exact_absent = sum(
        (str(row["prompt"]), str(row["response"])) not in candidate_pairs
        for row in structural
    )
    embedded = [int(row["id"]) for row in rows if STRUCTURAL_MARKER.search(str(row["response"]))]
    statuses = Counter(str(row["review_status"]) for row in rows)
    scenes = Counter(str(row["scene"]) for row in rows)
    report = {
        "baseline_rows": len(legacy),
        "candidate_rows": len(rows),
        "confirmed_defects": confirmed_disposition,
        "confirmed_defects_fixed_or_isolated": all(
            row["exact_bad_pair_absent"] for row in confirmed_disposition
        ),
        "legacy_structural_candidates": len(structural),
        "legacy_structural_exact_pairs_absent": structural_exact_absent,
        "candidate_embedded_speaker_markers": embedded,
        "source_replay_failures": replay_failures,
        "review_status_counts": dict(statuses),
        "scene_counts": dict(scenes),
        "greeting_coverage_gap": scenes["greeting"] == 0,
        "vector_count": vector_count,
        "production_switched": False,
        "deterministic_pass": (
            all(row["exact_bad_pair_absent"] for row in confirmed_disposition)
            and not embedded and not replay_failures and statuses["approved"] == 0
            and vector_count == 0
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["deterministic_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
