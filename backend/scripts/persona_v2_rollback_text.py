"""Roll back the Text Persona/Style combination without reverting user data."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def rollback(args: argparse.Namespace) -> None:
    database = args.production_db.resolve()
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(database) as conn:
        exists = conn.execute(
            "SELECT 1 FROM index_generations WHERE collection='style_examples' AND generation=?",
            (args.generation,),
        ).fetchone()
        if exists is None:
            raise ValueError(f"rollback generation does not exist: {args.generation}")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE index_generations SET status='retired' "
            "WHERE collection='style_examples' AND status='active'"
        )
        conn.execute(
            "UPDATE index_generations SET status='active',published_at=? "
            "WHERE collection='style_examples' AND generation=?",
            (now, args.generation),
        )
        conn.execute(
            "UPDATE active_index_generations SET generation=?,revision=revision+1,updated_at=? "
            "WHERE collection='style_examples'",
            (args.generation, now),
        )
        conn.commit()
        active = conn.execute(
            "SELECT generation,revision FROM active_index_generations WHERE collection='style_examples'"
        ).fetchone()
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("messages", "memories", "conversations")
        }
    print(json.dumps({
        "active_generation": active[0],
        "active_generation_revision": active[1],
        "preserved_counts": counts,
        "next_config_action": "set persona.active_package=persona-v1-production",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--production-db", type=Path, required=True)
    parser.add_argument("--generation", default="legacy-v1")
    rollback(parser.parse_args())
