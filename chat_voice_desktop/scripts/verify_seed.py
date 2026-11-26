from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


RUNTIME_TABLES = (
    "conversations",
    "messages",
    "memories",
    "relationship_states",
    "scene_states",
    "request_executions",
    "reply_snapshots",
    "post_turn_commits",
    "post_turn_failures",
    "persona_permission_events",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify the clean desktop DB seed")
    parser.add_argument("database", type=Path)
    arguments = parser.parse_args()
    connection = sqlite3.connect(arguments.database)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"integrity_check={integrity}")
        counts = {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in RUNTIME_TABLES
        }
        populated = {table: count for table, count in counts.items() if count}
        if populated:
            raise RuntimeError(f"runtime tables are not empty: {populated}")
        generations = connection.execute(
            "SELECT collection, generation FROM active_index_generations ORDER BY collection"
        ).fetchall()
        print(f"integrity=ok runtime_tables=empty active_generations={generations}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
