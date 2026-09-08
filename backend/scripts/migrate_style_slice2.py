"""Preview/apply the additive Slice 2 style-review schema migration."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONFIRMED_DEFECT_IDS = {1518, 1308, 1513, 1151, 1189, 1394, 1417, 1506, 919}
STRUCTURAL_MARKER = re.compile(r"弹幕[：:]|(?:海|凉果|于尔丹|憨憨)[：:]")
REVIEW_COLUMNS = {
    "review_status": "TEXT NOT NULL DEFAULT 'pending'",
    "source_tier": "TEXT NOT NULL DEFAULT 'unknown'",
    "source_document_id": "INTEGER",
    "source_start_line": "INTEGER",
    "source_end_line": "INTEGER",
    "source_start_char": "INTEGER",
    "source_end_char": "INTEGER",
    "source_speaker": "TEXT",
    "source_user_turn": "TEXT",
    "source_response_turn": "TEXT",
    "source_raw_text": "TEXT",
    "cleaning_operations_json": "TEXT NOT NULL DEFAULT '[]'",
    "reviewer_id": "TEXT",
    "review_notes": "TEXT",
    "reviewed_at": "TEXT",
    "index_generation": "TEXT",
}


def connect_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def preview(path: Path) -> dict:
    with connect_readonly(path) as conn:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(style_examples)")
        }
        rows = conn.execute(
            "SELECT id, prompt, response, scene, source_type, source_ref, metadata_json "
            "FROM style_examples ORDER BY id"
        ).fetchall()
        has_migrations = bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone())
        migration_applied = bool(
            has_migrations
            and conn.execute("SELECT 1 FROM schema_migrations WHERE version=2").fetchone()
        )

    structural_candidates = [
        int(row["id"])
        for row in rows
        if STRUCTURAL_MARKER.search(str(row["response"]))
    ]
    scene_counts = Counter(str(row["scene"]) for row in rows)
    return {
        "mode": "dry-run",
        "database": str(path.resolve()),
        "style_count": len(rows),
        "schema_columns": sorted(columns),
        "migration_applied": migration_applied,
        "planned_actions": {
            "add_columns": sorted(set(REVIEW_COLUMNS) - columns),
            "legacy_rows_left_pending": len(rows),
            "confirmed_defects_to_quarantine": sorted(
                CONFIRMED_DEFECT_IDS & {int(row["id"]) for row in rows}
            ),
            "structural_candidates_for_review": structural_candidates,
            "greeting_rows_for_review": scene_counts["greeting"],
            "comfort_rows_for_review": scene_counts["comfort"],
            "auto_approved_rows": 0,
            "physical_deletes": 0,
        },
        "scene_counts": dict(scene_counts),
    }


def apply(path: Path) -> dict:
    before = preview(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(style_examples)")
        }
        for name, definition in REVIEW_COLUMNS.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE style_examples ADD COLUMN {name} {definition}")
        placeholders = ",".join("?" for _ in CONFIRMED_DEFECT_IDS)
        conn.execute(
            f"UPDATE style_examples SET review_status='quarantined', "
            f"review_notes='confirmed structural mismatch from F02 audit' "
            f"WHERE id IN ({placeholders})",
            sorted(CONFIRMED_DEFECT_IDS),
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) "
            "VALUES (2,'style_review_provenance',datetime('now'))"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_style_review_eligibility "
            "ON style_examples(review_status,source_tier,source_type)"
        )
        conn.commit()
        migration = conn.execute(
            "SELECT version,name,applied_at FROM schema_migrations WHERE version=2"
        ).fetchone()
    finally:
        conn.close()
    return {
        "mode": "apply",
        "database": str(path.resolve()),
        "before": before,
        "after": preview(path),
        "migration": dict(migration) if migration else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=ROOT / "source_data/documents.db")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = apply(args.database) if args.apply else preview(args.database)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
