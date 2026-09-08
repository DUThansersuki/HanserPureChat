"""Preview/apply Slice 1 memory migration with an SQLite backup."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from hanser_agent import db  # noqa: E402


def classify(text: str, memory_type: str) -> list[str]:
    labels = []
    patterns = {
        "hypothetical": r"(?:如果|假如|要是|倘若|万一)",
        "question": r"[？?]|(?:吗|么|是不是|还记得|记得).{0,4}$",
        "negated": r"(?:不|没|没有|从来没|别)",
        "corrected": r"(?:说错|更正|纠正|改成|其实)",
        "quoted": r"“[^”]*”|「[^」]*」|\"[^\"]*\"|这句话|原话|引用",
    }
    for label, pattern in patterns.items():
        if re.search(pattern, text):
            labels.append(label)
    if memory_type in {"shared_event", "relationship_event", "episode"}:
        labels.append("shared_reality_requires_review")
    if not labels:
        labels.append("asserted_user_fact")
    return labels


def preview(path: Path) -> dict:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(memories)")}
    has_migration_table = bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone())
    migration_applied = bool(
        has_migration_table
        and conn.execute("SELECT 1 FROM schema_migrations WHERE version = 1").fetchone()
    )
    rows = conn.execute("SELECT * FROM memories ORDER BY created_at").fetchall()
    report_rows = []
    counts: Counter[str] = Counter()
    missing_sources = 0
    non_user_sources = 0
    for row in rows:
        labels = classify(str(row["content"]), str(row["type"]))
        counts.update(labels)
        sources = []
        for source_id in json.loads(str(row["source_message_ids_json"])):
            source = conn.execute(
                "SELECT role, content, created_at FROM messages WHERE id = ?",
                (source_id,),
            ).fetchone()
            if source is None:
                missing_sources += 1
                sources.append({"id": source_id, "found": False})
            else:
                if str(source["role"]) != "user":
                    non_user_sources += 1
                sources.append({
                    "id": source_id,
                    "found": True,
                    "role": source["role"],
                    "text": source["content"],
                    "created_at": source["created_at"],
                })
        report_rows.append({
            "id": row["id"], "type": row["type"], "status": row["status"],
            "content": row["content"], "classifications": labels, "sources": sources,
        })
    conn.close()
    return {
        "mode": "dry-run",
        "database": str(path.resolve()),
        "schema_columns": sorted(columns),
        "migration_applied": migration_applied,
        "memory_count": len(rows),
        "classifications": dict(counts),
        "missing_sources": missing_sources,
        "non_user_sources": non_user_sources,
        "planned_actions": {
            "add_assertion_columns": sorted(set(db.MEMORY_ASSERTION_COLUMNS) - columns),
            "mark_legacy_shared_reality_unverified": 0 if migration_applied else sum(
                row["type"] in {"shared_event", "relationship_event", "episode"}
                for row in rows
            ),
            "physical_memory_deletes": 0,
        },
        "rows": report_rows,
    }


def backup_database(path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = backup_dir / f"{path.stem}.pre_slice1.{stamp}{path.suffix}"
    if target.exists():
        raise FileExistsError(target)
    source = sqlite3.connect(str(path))
    destination = sqlite3.connect(str(target))
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return target


def apply(path: Path, backup_dir: Path) -> dict:
    before = preview(path)
    backup = backup_database(path, backup_dir)
    with db.connect(path) as conn:
        db.init_db(conn)
        conn.execute(
            """
            DELETE FROM vector_embeddings
            WHERE collection = 'memories' AND item_id IN (
                SELECT id FROM memories
                WHERE status != 'active' OR validity NOT IN ('asserted', 'verified')
            )
            """
        )
        conn.commit()
        migration = conn.execute(
            "SELECT version, name, applied_at FROM schema_migrations WHERE version = 1"
        ).fetchone()
    after = preview(path)
    return {
        "mode": "apply",
        "database": str(path.resolve()),
        "backup": str(backup.resolve()),
        "before": before,
        "after": after,
        "migration": dict(migration) if migration is not None else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=ROOT / "source_data" / "documents.db")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "source_data" / "backups")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = apply(args.database, args.backup_dir) if args.apply else preview(args.database)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
