"""Build an isolated, review-pending Slice 2 style corpus generation."""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from hanser_agent import db  # noqa: E402
from hanser_agent.persona.data_pipeline import (  # noqa: E402
    persona_statistics,
    rebuild_style_examples,
)
from hanser_agent.retrieval import SQLiteVectorStore  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-database", type=Path, required=True)
    parser.add_argument("--candidate-database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--min-quality", type=float, default=0.68)
    parser.add_argument("--generation")
    args = parser.parse_args()

    source = args.source_database.resolve()
    candidate = args.candidate_database.resolve()
    output = args.output_dir.resolve()
    if candidate.exists():
        raise SystemExit(f"candidate database already exists: {candidate}")
    output.mkdir(parents=True, exist_ok=True)
    candidate.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, candidate)
    generation = args.generation or datetime.now(timezone.utc).strftime(
        "style-candidate-%Y%m%dT%H%M%SZ"
    )

    with db.connect(candidate) as conn:
        db.init_db(conn)
    rows = rebuild_style_examples(
        db_path=candidate,
        min_quality=args.min_quality,
        index_generation=generation,
    )
    with db.connect(candidate) as conn:
        conn.execute("UPDATE style_examples SET embedding_ref=NULL")
        conn.commit()
        counts = {
            "rows": conn.execute("SELECT count(*) FROM style_examples").fetchone()[0],
            "pending": conn.execute(
                "SELECT count(*) FROM style_examples WHERE review_status='pending'"
            ).fetchone()[0],
            "approved": conn.execute(
                "SELECT count(*) FROM style_examples WHERE review_status='approved'"
            ).fetchone()[0],
        }
        scenes = dict(conn.execute(
            "SELECT scene,count(*) FROM style_examples GROUP BY scene ORDER BY scene"
        ).fetchall())
        invalid = conn.execute(
            """
            SELECT count(*) FROM style_examples
            WHERE source_document_id IS NULL OR source_start_line IS NULL
               OR source_end_line IS NULL OR source_start_char IS NULL
               OR source_end_char IS NULL OR source_speaker != 'hanser'
               OR source_user_turn IS NULL OR source_response_turn IS NULL
               OR source_raw_text IS NULL
            """
        ).fetchone()[0]
    SQLiteVectorStore(candidate).clear("style_examples")

    stats = persona_statistics(rows)
    report = {
        "generation": generation,
        "source_database": str(source),
        "candidate_database": str(candidate),
        "counts": counts,
        "scene_counts": scenes,
        "invalid_provenance_rows": invalid,
        "vectors_published": 0,
        "production_switched": False,
        "note": "All newly extracted rows remain pending until a named human review decision.",
    }
    (output / "candidate_build.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "persona_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
