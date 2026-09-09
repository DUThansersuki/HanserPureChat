"""Publish a frozen Persona v2 Style generation without replacing chat data."""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


STYLE_COLUMNS = (
    "id", "prompt", "response", "context_before", "context_after", "scene",
    "speech_act", "tone_json", "relationship_level", "energy", "teasing_level",
    "answer_length", "response_mode", "source_type", "source_ref",
    "authenticity_score", "quality_score", "metadata_json", "embedding_ref",
    "review_status", "source_tier", "source_document_id", "source_start_line",
    "source_end_line", "source_start_char", "source_end_char", "source_speaker",
    "source_user_turn", "source_response_turn", "source_raw_text",
    "cleaning_operations_json", "reviewer_id", "review_notes", "reviewed_at",
    "index_generation",
)


def promote(args: argparse.Namespace) -> None:
    production = args.production_db.resolve()
    candidate = args.candidate_db.resolve()
    generation = args.generation
    backup_dir = args.backup_dir.resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_dir / f"{production.stem}.pre_persona_v2.{timestamp}{production.suffix}"
    shutil.copy2(production, backup)

    with sqlite3.connect(production) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("ATTACH DATABASE ? AS candidate", (str(candidate),))
        source_index = conn.execute(
            "SELECT model,dimensions,source_revision,config_hash,item_count,created_at,published_at "
            "FROM candidate.index_generations WHERE collection='style_examples' AND generation=? AND status='active'",
            (generation,),
        ).fetchone()
        if source_index is None:
            raise ValueError(f"candidate generation is not active: {generation}")
        style_count = conn.execute(
            "SELECT COUNT(*) FROM candidate.style_examples WHERE index_generation=?",
            (generation,),
        ).fetchone()[0]
        vector_count = conn.execute(
            "SELECT COUNT(*) FROM candidate.vector_embeddings_v2 WHERE collection='style_examples' AND generation=?",
            (generation,),
        ).fetchone()[0]
        collision_count = conn.execute(
            "SELECT COUNT(*) FROM main.style_examples p JOIN candidate.style_examples c ON c.id=p.id "
            "WHERE c.index_generation=?",
            (generation,),
        ).fetchone()[0]
        if collision_count:
            raise ValueError(f"candidate style id collisions: {collision_count}")
        if vector_count != int(source_index[4]):
            raise ValueError(
                f"candidate vector count mismatch: manifest={source_index[4]} actual={vector_count}"
            )

        columns = ",".join(STYLE_COLUMNS)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            f"INSERT INTO main.style_examples ({columns}) "
            f"SELECT {columns} FROM candidate.style_examples WHERE index_generation=?",
            (generation,),
        )
        conn.execute(
            "INSERT INTO main.vector_embeddings_v2(collection,generation,item_id,model,dimensions,vector) "
            "SELECT collection,generation,item_id,model,dimensions,vector "
            "FROM candidate.vector_embeddings_v2 WHERE collection='style_examples' AND generation=?",
            (generation,),
        )
        conn.execute(
            "UPDATE main.index_generations SET status='retired' "
            "WHERE collection='style_examples' AND status='active'"
        )
        conn.execute(
            "INSERT INTO main.index_generations"
            "(collection,generation,status,model,dimensions,source_revision,config_hash,item_count,created_at,published_at) "
            "VALUES ('style_examples',?,'active',?,?,?,?,?,?,?)",
            (generation, *source_index),
        )
        conn.execute(
            "UPDATE main.active_index_generations SET generation=?,revision=revision+1,updated_at=? "
            "WHERE collection='style_examples'",
            (generation, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()

        active = conn.execute(
            "SELECT generation,revision FROM main.active_index_generations WHERE collection='style_examples'"
        ).fetchone()
        installed_styles = conn.execute(
            "SELECT COUNT(*) FROM main.style_examples WHERE index_generation=?",
            (generation,),
        ).fetchone()[0]
        installed_vectors = conn.execute(
            "SELECT COUNT(*) FROM main.vector_embeddings_v2 WHERE collection='style_examples' AND generation=?",
            (generation,),
        ).fetchone()[0]

    result = {
        "schema_version": 1,
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "production_database": str(production),
        "backup_database": str(backup),
        "previous_generation": "legacy-v1",
        "active_generation": active[0],
        "active_generation_revision": active[1],
        "installed_style_rows": installed_styles,
        "installed_vectors": installed_vectors,
        "chat_database_replaced": False,
        "preserve_chat_history_and_memory": True,
    }
    args.output.resolve().write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--production-db", type=Path, required=True)
    parser.add_argument("--candidate-db", type=Path, required=True)
    parser.add_argument("--generation", required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    promote(parser.parse_args())
