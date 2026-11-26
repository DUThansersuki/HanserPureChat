from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
from pathlib import Path


RUNTIME_TABLES = (
    "conversation_summaries",
    "conversations",
    "evaluation_outputs",
    "evaluation_runs",
    "memories",
    "messages",
    "persona_permission_events",
    "post_turn_commits",
    "post_turn_failures",
    "relationship_states",
    "reply_snapshots",
    "request_executions",
    "scene_states",
)


def export_seed(source: Path, destination: Path) -> None:
    source = source.resolve()
    destination = destination.resolve()
    if source == destination:
        raise ValueError("seed destination must differ from the source database")
    if not source.is_file():
        raise FileNotFoundError(source)

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    shutil.copy2(source, temporary)

    connection = sqlite3.connect(temporary)
    try:
        existing = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        with connection:
            for table in RUNTIME_TABLES:
                if table in existing:
                    connection.execute(f'DELETE FROM "{table}"')
            if "sqlite_sequence" in existing:
                placeholders = ",".join("?" for _ in RUNTIME_TABLES)
                connection.execute(
                    f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})",
                    RUNTIME_TABLES,
                )
        connection.execute("VACUUM")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise RuntimeError(f"seed integrity check failed: {integrity}")
    finally:
        connection.close()

    os.replace(temporary, destination)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a clean desktop DB seed")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    arguments = parser.parse_args()
    export_seed(arguments.source, arguments.destination)
    print(arguments.destination.resolve())


if __name__ == "__main__":
    main()
