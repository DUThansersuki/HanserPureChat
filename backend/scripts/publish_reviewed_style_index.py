"""Build a complete index from approved real rows and explicit synthetic fallbacks."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from hanser_agent import db  # noqa: E402
from hanser_agent.config import load_settings  # noqa: E402
from hanser_agent.persona.data_pipeline import STYLE_COLLECTION  # noqa: E402
from hanser_agent.retrieval import SQLiteVectorStore, build_embedder  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    database = args.database.resolve()
    settings = load_settings(args.config)
    backup = database.with_name(
        database.stem + ".pre_reviewed_index." +
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + database.suffix
    )
    shutil.copy2(database, backup)
    with db.connect(database) as conn:
        rows = conn.execute(
            """
            SELECT id,scene,response_mode,speech_act,prompt,index_generation,source_type
            FROM style_examples
            WHERE review_status='approved'
              AND ((source_tier='primary' AND source_type='real')
                   OR (source_tier='synthetic' AND source_type='synthetic'))
              AND source_speaker='hanser'
              AND source_user_turn IS NOT NULL AND source_response_turn IS NOT NULL
            ORDER BY id
            """
        ).fetchall()
    if not rows:
        raise SystemExit("no approved primary rows; index was not modified")
    embedder = build_embedder(settings.embedding)
    vectors = []
    for start in range(0, len(rows), settings.embedding.batch_size):
        batch = rows[start:start + settings.embedding.batch_size]
        texts = [
            f"scene={row['scene']} mode={row['response_mode']} "
            f"speech_act={row['speech_act']}\nUser: {row['prompt']}"
            for row in batch
        ]
        embedded = await embedder.embed_documents(texts)
        vectors.extend(
            (str(row["id"]), vector)
            for row, vector in zip(batch, embedded, strict=True)
        )
    store = SQLiteVectorStore(database)
    source_revision = hashlib.sha256(
        "\n".join(
            f"{row['id']}:{row['index_generation']}:{row['source_type']}"
            for row in rows
        ).encode("utf-8")
    ).hexdigest()
    generation = store.begin_generation(
        STYLE_COLLECTION,
        embedder.model_name,
        len(vectors[0][1]),
        source_revision=source_revision,
        config_hash=hashlib.sha256(str(args.config.resolve()).encode()).hexdigest(),
    )
    try:
        store.stage_upsert(STYLE_COLLECTION, generation, embedder.model_name, vectors)
        store.publish_generation(STYLE_COLLECTION, generation, expected_count=len(vectors))
    except Exception:
        store.fail_generation(STYLE_COLLECTION, generation)
        raise
    ids = [str(row["id"]) for row in rows]
    placeholders = ",".join("?" for _ in ids)
    with db.connect(database) as conn:
        conn.execute("UPDATE style_examples SET embedding_ref=NULL")
        conn.execute(
            f"UPDATE style_examples SET embedding_ref='style_examples:' || id "
            f"WHERE id IN ({placeholders})",
            ids,
        )
        conn.commit()
        vector_count = store.count(STYLE_COLLECTION)
        eligible_count = conn.execute(
            "SELECT count(*) FROM style_examples WHERE embedding_ref IS NOT NULL"
        ).fetchone()[0]
    report = {
        "database": str(database), "backup": str(backup),
        "model": embedder.model_name, "approved_rows": len(rows),
        "real_rows": sum(1 for row in rows if row["source_type"] == "real"),
        "synthetic_rows": sum(1 for row in rows if row["source_type"] == "synthetic"),
        "vector_count": vector_count, "eligible_count": eligible_count,
        "complete": vector_count == eligible_count == len(rows),
        "production_switched": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
