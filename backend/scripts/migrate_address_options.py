"""Preview/apply migration 3 for typed, concurrently active address options."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


COLUMNS = {
    "memory_scope": "TEXT NOT NULL DEFAULT 'global'",
    "address_kind": "TEXT",
    "context_tags_json": "TEXT NOT NULL DEFAULT '[]'",
    "address_priority": "REAL NOT NULL DEFAULT 0",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect(database: Path) -> dict:
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(memories)")}
        migration = conn.execute(
            "SELECT version,name,applied_at FROM schema_migrations WHERE version=3"
        ).fetchone()
        legacy = [dict(row) for row in conn.execute(
            """
            SELECT id,user_id,memory_key,object_value,status
            FROM memories
            WHERE memory_key='user:name' OR predicate='preferred_name'
            ORDER BY user_id,created_at,id
            """
        )]
        return {
            "database": str(database),
            "database_sha256": sha(database),
            "migration": dict(migration) if migration else None,
            "missing_columns": sorted(set(COLUMNS) - columns),
            "memory_count": conn.execute("SELECT count(*) FROM memories").fetchone()[0],
            "legacy_name_rows": legacy,
            "planned_rekeys": len([row for row in legacy if row["object_value"]]),
            "physical_deletes": 0,
        }


def apply(database: Path) -> dict:
    before = inspect(database)
    if before["migration"] is not None:
        return {"mode": "already-applied", **before, "backup": None}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = database.with_name(f"{database.stem}.pre_address_v3.{stamp}{database.suffix}")
    shutil.copy2(database, backup)
    with sqlite3.connect(database) as conn:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(memories)")}
        for name, definition in COLUMNS.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE memories ADD COLUMN {name} {definition}")
        conn.execute(
            """
            UPDATE memories
            SET address_kind='personal_name', address_priority=1.0,
                memory_scope='global', context_tags_json='["personal"]',
                predicate='preferred_address',
                memory_key='address:personal_name:' || lower(object_value)
            WHERE (memory_key='user:name' OR predicate='preferred_name')
              AND object_value IS NOT NULL
            """
        )
        conn.execute(
            "INSERT INTO schema_migrations(version,name,applied_at) "
            "VALUES (3,'memory_address_options',datetime('now'))"
        )
        conn.commit()
    return {
        "mode": "apply",
        "before": before,
        "after": inspect(database),
        "backup": str(backup),
        "backup_sha256": sha(backup),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["preview", "apply"])
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    database = args.database.resolve()
    report = ({"mode": "preview", **inspect(database)} if args.mode == "preview" else apply(database))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True))


if __name__ == "__main__":
    main()
