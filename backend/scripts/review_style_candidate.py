"""Export/apply traceable human review decisions for a Slice 2 candidate."""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sqlite3
from pathlib import Path


SPEAKER_LEAK = re.compile(
    r"弹幕[：:]|(?:海|凉果|凉菓|于尔丹|憨憨|yousa|包包|小缘|缘|H|Y)[：:]",
    re.IGNORECASE,
)


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def replay_ok(conn: sqlite3.Connection, row: sqlite3.Row) -> tuple[bool, str]:
    document = conn.execute(
        "SELECT content FROM documents WHERE id=?", (row["source_document_id"],)
    ).fetchone()
    if document is None:
        return False, "source_document_missing"
    lines = str(document["content"]).splitlines()
    start = int(row["source_start_line"] or 0)
    end = int(row["source_end_line"] or 0)
    if start < 1 or end < start or end > len(lines):
        return False, "source_line_range_invalid"
    replay = "\n".join(lines[start - 1:end])
    if replay != str(row["source_raw_text"]):
        return False, "source_span_replay_mismatch"
    if row["source_speaker"] != "hanser":
        return False, "speaker_not_hanser"
    if row["prompt"] != row["source_user_turn"]:
        return False, "user_turn_mismatch"
    if row["response"] != row["source_response_turn"]:
        return False, "response_turn_mismatch"
    if SPEAKER_LEAK.search(str(row["response"])):
        return False, "embedded_speaker_marker"
    return True, "ok"


def export_queue(database: Path, output: Path, *, limit: int | None, seed: int) -> None:
    with connect(database) as conn:
        rows = conn.execute(
            """
            SELECT id,index_generation,scene,speech_act,prompt,response,
                   context_before,context_after,source_ref,source_tier,
                   source_raw_text,cleaning_operations_json
            FROM style_examples
            WHERE review_status='pending'
            ORDER BY CASE scene
                WHEN 'greeting' THEN 0 WHEN 'comfort' THEN 1
                WHEN 'receiving_praise' THEN 2 WHEN 'teasing' THEN 3 ELSE 4 END,
                id
            """
        ).fetchall()
    if limit is not None and len(rows) > limit:
        priority = [row for row in rows if row["scene"] in {"greeting", "comfort"}]
        remainder = [row for row in rows if row["scene"] not in {"greeting", "comfort"}]
        rng = random.Random(seed)
        sampled = rng.sample(remainder, max(0, limit - len(priority)))
        rows = priority + sorted(sampled, key=lambda row: int(row["id"]))
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "id", "index_generation", "scene", "speech_act", "prompt", "response",
        "context_before", "context_after", "source_ref", "source_tier",
        "source_raw_text", "cleaning_operations_json", "decision", "reviewer_id",
        "review_notes",
    ]
    with output.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            record = dict(row)
            record.update(decision="", reviewer_id="", review_notes="")
            writer.writerow(record)
    print(json.dumps({"output": str(output.resolve()), "rows": len(rows)}, ensure_ascii=False))


def apply_decisions(database: Path, decisions: Path, report_path: Path) -> None:
    accepted = rejected = invalid = 0
    errors: list[dict] = []
    with decisions.open(encoding="utf-8-sig", newline="") as handle:
        records = list(csv.DictReader(handle))
    with connect(database) as conn:
        for record in records:
            decision = record["decision"].strip().lower()
            if not decision:
                continue
            if decision not in {"approved", "rejected"} or not record["reviewer_id"].strip():
                errors.append({"id": record["id"], "reason": "decision_or_reviewer_invalid"})
                invalid += 1
                continue
            row = conn.execute("SELECT * FROM style_examples WHERE id=?", (record["id"],)).fetchone()
            if row is None or row["review_status"] != "pending":
                errors.append({"id": record["id"], "reason": "row_missing_or_not_pending"})
                invalid += 1
                continue
            if decision == "approved":
                valid, reason = replay_ok(conn, row)
                if not valid:
                    errors.append({"id": record["id"], "reason": reason})
                    invalid += 1
                    continue
                accepted += 1
            else:
                rejected += 1
            conn.execute(
                """
                UPDATE style_examples
                SET review_status=?,reviewer_id=?,review_notes=?,reviewed_at=datetime('now')
                WHERE id=?
                """,
                (decision, record["reviewer_id"].strip(), record["review_notes"].strip(), record["id"]),
            )
        conn.commit()
    report = {
        "database": str(database.resolve()), "decisions_file": str(decisions.resolve()),
        "approved": accepted, "rejected": rejected, "invalid": invalid, "errors": errors,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("--database", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--limit", type=int)
    export.add_argument("--seed", type=int, default=602)
    apply = sub.add_parser("apply")
    apply.add_argument("--database", type=Path, required=True)
    apply.add_argument("--decisions", type=Path, required=True)
    apply.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "export":
        export_queue(args.database, args.output, limit=args.limit, seed=args.seed)
    else:
        apply_decisions(args.database, args.decisions, args.report)


if __name__ == "__main__":
    main()
