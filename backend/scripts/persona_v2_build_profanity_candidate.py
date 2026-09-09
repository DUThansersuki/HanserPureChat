"""Build a new candidate Style generation for the 15% light-profanity target."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


SOURCE_GENERATION = "persona-v2-style-safe-7bd0970e2164b249"
TRANSFORMATION_ID = "persona_v2_profanity15_v1"
TARGET_EXAMPLE_ID = "style:2010"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(args: argparse.Namespace) -> None:
    source_db = args.source_db.resolve()
    source_reviewed = args.source_reviewed.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)

    revision = hashlib.sha256(
        source_reviewed.read_bytes() + b"\n" + TRANSFORMATION_ID.encode("ascii")
    ).hexdigest()
    generation = f"persona-v2-style-profanity15-{revision[:16]}"
    reviewed_path = output_dir / "style_examples.reviewed.jsonl"
    database_path = output_dir / "candidate.db"
    shutil.copy2(source_db, database_path)

    target_seen = False
    output_rows: list[dict[str, object]] = []
    for line in source_reviewed.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("index_generation") == SOURCE_GENERATION:
            row["index_generation"] = generation
        if row.get("id") == TARGET_EXAMPLE_ID:
            if row.get("schema_review_status") != "approved" or "style_runtime" not in row.get("runtime_scope", []):
                raise ValueError(f"{TARGET_EXAMPLE_ID} is not an approved runtime example")
            row["expression_tags"] = list(
                dict.fromkeys([*row.get("expression_tags", []), "profanity"])
            )
            row["behavior_tags"] = list(
                dict.fromkeys([*row.get("behavior_tags", []), "light_profanity_release"])
            )
            target_seen = True
        output_rows.append(row)
    if not target_seen:
        raise ValueError(f"missing {TARGET_EXAMPLE_ID} in reviewed source")
    reviewed_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in output_rows),
        encoding="utf-8",
    )

    with sqlite3.connect(database_path) as conn:
        conn.row_factory = sqlite3.Row
        active = conn.execute(
            "SELECT revision FROM active_index_generations WHERE collection='style_examples' AND generation=?",
            (SOURCE_GENERATION,),
        ).fetchone()
        source_index = conn.execute(
            "SELECT * FROM index_generations WHERE collection='style_examples' AND generation=?",
            (SOURCE_GENERATION,),
        ).fetchone()
        if active is None or source_index is None:
            raise ValueError("source Style generation is not active")

        target = conn.execute(
            "SELECT metadata_json FROM style_examples WHERE id=?",
            (int(TARGET_EXAMPLE_ID.split(":", 1)[1]),),
        ).fetchone()
        if target is None:
            raise ValueError(f"missing database row {TARGET_EXAMPLE_ID}")
        metadata = json.loads(target["metadata_json"] or "{}")
        metadata["expression_tags"] = list(
            dict.fromkeys([*metadata.get("expression_tags", []), "profanity"])
        )
        metadata["behavior_tags"] = list(
            dict.fromkeys([*metadata.get("behavior_tags", []), "light_profanity_release"])
        )
        conn.execute(
            "UPDATE style_examples SET metadata_json=? WHERE id=?",
            (
                json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
                int(TARGET_EXAMPLE_ID.split(":", 1)[1]),
            ),
        )
        conn.execute(
            "UPDATE style_examples SET index_generation=? WHERE index_generation=?",
            (generation, SOURCE_GENERATION),
        )
        conn.execute(
            "INSERT INTO vector_embeddings_v2(collection,generation,item_id,model,dimensions,vector) "
            "SELECT collection,?,item_id,model,dimensions,vector FROM vector_embeddings_v2 "
            "WHERE collection='style_examples' AND generation=?",
            (generation, SOURCE_GENERATION),
        )
        item_count = conn.execute(
            "SELECT COUNT(*) FROM vector_embeddings_v2 WHERE collection='style_examples' AND generation=?",
            (generation,),
        ).fetchone()[0]
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE index_generations SET status='retired' WHERE collection='style_examples' AND status='active'"
        )
        conn.execute(
            "INSERT INTO index_generations(collection,generation,status,model,dimensions,source_revision,config_hash,item_count,created_at,published_at) "
            "VALUES ('style_examples',?,'active',?,?,?,?,?,?,?)",
            (
                generation,
                source_index["model"],
                source_index["dimensions"],
                f"{TRANSFORMATION_ID}:{revision}",
                revision,
                item_count,
                now,
                now,
            ),
        )
        conn.execute(
            "UPDATE active_index_generations SET generation=?,revision=revision+1,updated_at=? WHERE collection='style_examples'",
            (generation, now),
        )
        conn.commit()

        tagged = conn.execute(
            "SELECT metadata_json FROM style_examples WHERE id=?",
            (int(TARGET_EXAMPLE_ID.split(":", 1)[1]),),
        ).fetchone()[0]

    summary = {
        "schema_version": 1,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "transformation_id": TRANSFORMATION_ID,
        "source_generation": SOURCE_GENERATION,
        "style_generation": generation,
        "source_database": str(source_db),
        "candidate_database": str(database_path),
        "candidate_database_sha256": sha256(database_path),
        "reviewed_source": str(source_reviewed),
        "reviewed_output_sha256": sha256(reviewed_path),
        "vector_item_count": item_count,
        "vector_backed_runtime_candidate_count": item_count,
        "updated_example": TARGET_EXAMPLE_ID,
        "updated_metadata": json.loads(tagged),
        "production_database_modified": False,
    }
    (output_dir / "build_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--source-reviewed", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    build(parser.parse_args())
