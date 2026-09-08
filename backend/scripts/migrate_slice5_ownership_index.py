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


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def build_preview(database: Path) -> dict[str, object]:
    conn = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conversations = int(conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0])
        missing_owner_rows = [
            dict(row)
            for row in conn.execute(
                "SELECT id, user_id FROM conversations "
                "WHERE user_id IS NULL OR trim(user_id)='' ORDER BY id"
            ).fetchall()
        ]
        vector_groups = [
            dict(row)
            for row in conn.execute(
                "SELECT collection, model, dimensions, COUNT(*) AS item_count "
                "FROM vector_embeddings GROUP BY collection, model, dimensions "
                "ORDER BY collection, model, dimensions"
            ).fetchall()
        ]
        vector_conflicts = [
            dict(row)
            for row in conn.execute(
                "SELECT collection, COUNT(DISTINCT model || ':' || dimensions) AS layouts "
                "FROM vector_embeddings GROUP BY collection HAVING layouts > 1"
            ).fetchall()
        ]
        orphan_chunk_tokens = int(
            conn.execute(
                "SELECT COUNT(*) FROM chunk_tokens t LEFT JOIN document_chunks c "
                "ON c.id=t.chunk_id WHERE c.id IS NULL"
            ).fetchone()[0]
        )
        applied = (
            conn.execute("SELECT 1 FROM schema_migrations WHERE version=4").fetchone()
            if _has_table(conn, "schema_migrations")
            else None
        )
    finally:
        conn.close()

    return {
        "preview_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": str(database.resolve()),
        "database_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
        "migration_already_applied": applied is not None,
        "conversation_count": conversations,
        "missing_owner_count": len(missing_owner_rows),
        "missing_owner_rows": missing_owner_rows,
        "legacy_vector_groups": vector_groups,
        "vector_layout_conflicts": vector_conflicts,
        "orphan_chunk_token_count": orphan_chunk_tokens,
        "blockers": {
            "missing_owner_requires_manual_resolution": bool(missing_owner_rows),
            "ambiguous_vector_layout_requires_manual_resolution": bool(vector_conflicts),
        },
        "planned_changes": [
            "create generation-scoped vector table and active-generation pointer",
            "copy legacy vectors without deleting the legacy table",
            "record source revision, model, dimensions, config hash and item count",
            "reject conversation owner changes; never infer a missing owner",
        ],
        "planned_deletes": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview Slice 5 ownership/index migration")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    preview = build_preview(args.database)
    if args.apply:
        if any(preview["blockers"].values()):
            raise SystemExit("migration blocked by preview; no changes made")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = args.database.with_name(
            f"{args.database.stem}.pre_slice5.{stamp}{args.database.suffix}"
        )
        shutil.copy2(args.database, backup)
        try:
            with db.connect(args.database) as conn:
                db.apply_slice5_ownership_index_migration(conn)
        except Exception:
            raise SystemExit(f"migration failed; untouched backup: {backup}")
        preview["applied"] = True
        preview["backup"] = str(backup.resolve())
        preview["post_migration_sha256"] = hashlib.sha256(args.database.read_bytes()).hexdigest()
    rendered = json.dumps(preview, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
