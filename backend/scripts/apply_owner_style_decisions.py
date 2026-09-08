"""Apply the owner's bulk acceptance of the complete machine A/R review.

Machine results remain identified as the review basis. Deterministic source replay
and speaker-leak checks can quarantine an A decision instead of approving it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend/scripts"))
from review_style_candidate import replay_ok


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_results(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not rows or any(not row.get("valid_output") or row.get("decision") not in {"A", "R"} for row in rows):
        raise ValueError("machine results must all contain an exact valid A/R decision")
    if len({int(row["id"]) for row in rows}) != len(rows):
        raise ValueError("duplicate machine result ids")
    return rows


def evaluate(database: Path, machine_results: Path) -> dict:
    decisions = load_results(machine_results)
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        candidate_rows = {
            int(row["id"]): row
            for row in conn.execute("SELECT * FROM style_examples ORDER BY id")
        }
        missing = sorted(set(int(row["id"]) for row in decisions) - set(candidate_rows))
        extra = sorted(set(candidate_rows) - set(int(row["id"]) for row in decisions))
        dispositions = []
        for decision in decisions:
            item_id = int(decision["id"])
            row = candidate_rows.get(item_id)
            if row is None:
                continue
            if decision["decision"] == "R":
                status, reason = "rejected", "owner_accepted_machine_R"
            else:
                valid, replay_reason = replay_ok(conn, row)
                status = "approved" if valid else "quarantined"
                reason = "owner_accepted_machine_A" if valid else replay_reason
            dispositions.append({"id": item_id, "machine": decision["decision"], "status": status, "reason": reason})
    return {
        "database": str(database),
        "database_sha256": sha(database),
        "machine_results": str(machine_results),
        "machine_results_sha256": sha(machine_results),
        "rows": len(decisions),
        "missing_ids": missing,
        "extra_ids": extra,
        "status_counts": dict(Counter(item["status"] for item in dispositions)),
        "quarantined": [item for item in dispositions if item["status"] == "quarantined"],
        "dispositions": dispositions,
    }


def apply(database: Path, machine_results: Path, reviewer_id: str) -> dict:
    preview = evaluate(database, machine_results)
    if preview["missing_ids"] or preview["extra_ids"]:
        raise ValueError("machine result ids do not exactly cover the candidate corpus")
    backup = database.with_name(
        database.stem + ".pre_owner_review." +
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + database.suffix
    )
    shutil.copy2(database, backup)
    with sqlite3.connect(database) as conn:
        for item in preview["dispositions"]:
            conn.execute(
                """
                UPDATE style_examples
                SET review_status=?, reviewer_id=?, review_notes=?,
                    reviewed_at=datetime('now')
                WHERE id=? AND review_status='pending'
                """,
                (
                    item["status"], reviewer_id,
                    "owner bulk acceptance of deepseek-v4-flash A/R; " + item["reason"],
                    item["id"],
                ),
            )
        conn.commit()
    return {
        "mode": "apply",
        "reviewer_id": reviewer_id,
        "backup": str(backup),
        "backup_sha256": sha(backup),
        "preview": {key: value for key, value in preview.items() if key != "dispositions"},
        "after": evaluate(database, machine_results),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["preview", "apply"])
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--machine-results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reviewer-id", default="owner_bulk_acceptance:2026-09-06")
    args = parser.parse_args()
    database = args.database.resolve()
    results = args.machine_results.resolve()
    report = evaluate(database, results) if args.mode == "preview" else apply(database, results, args.reviewer_id)
    if args.mode == "preview":
        report = {"mode": "preview", **report}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    printable = report if "dispositions" not in report else {k: v for k, v in report.items() if k != "dispositions"}
    print(json.dumps(printable, ensure_ascii=True))


if __name__ == "__main__":
    main()
