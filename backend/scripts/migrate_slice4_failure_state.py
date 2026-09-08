"""Preview/apply the Slice 4 request and post-turn failure-state migration."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from hanser_agent import db  # noqa: E402


def preview(path: Path) -> dict[str, object]:
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        applied = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version=5"
        ).fetchone()
        messages = int(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
        duplicate_trace_groups = int(
            conn.execute(
                "SELECT COUNT(*) FROM (SELECT trace_id FROM messages "
                "WHERE trace_id IS NOT NULL GROUP BY trace_id HAVING COUNT(*)>2)"
            ).fetchone()[0]
        )
    finally:
        conn.close()
    return {
        "preview_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": str(path.resolve()),
        "database_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "migration_already_applied": applied is not None,
        "message_count": messages,
        "trace_groups_with_more_than_one_turn": duplicate_trace_groups,
        "blockers": [],
        "planned_changes": [
            "add request execution records for idempotency and trace status",
            "add retryable post-turn failure records with stage and payload",
            "preserve all existing API fields, messages, memories and failures",
        ],
        "planned_deletes": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = preview(args.database)
    if args.apply:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = args.database.with_name(
            f"{args.database.stem}.pre_slice4.{stamp}{args.database.suffix}"
        )
        shutil.copy2(args.database, backup)
        with db.connect(args.database) as conn:
            db.apply_slice4_failure_state_migration(conn)
        result.update(
            applied=True,
            backup=str(backup.resolve()),
            post_migration_sha256=hashlib.sha256(args.database.read_bytes()).hexdigest(),
        )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
