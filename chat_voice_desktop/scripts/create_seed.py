"""Create a clean database for a new checkout."""
import json
import os
from contextlib import closing
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from hanser_agent import db


def main() -> None:
    target = ROOT / "resources/database/documents.seed.db"
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)

    pointer = ROOT / "backend/hanser_agent/prompts/persona/releases/production.json"
    generation = json.loads(pointer.read_text(encoding="utf-8"))["style_generation"]
    with NamedTemporaryFile(dir=target.parent, suffix=".db", delete=False) as file:
        staging = Path(file.name)
    try:
        with closing(db.connect(staging)) as conn, conn:
            db.init_db(conn)
            conn.execute(
                """INSERT INTO index_generations
                   (collection, generation, status, model, dimensions, source_revision,
                    config_hash, item_count, created_at, published_at)
                   VALUES ('style_examples', ?, 'active', 'bm25', 0, 'empty-bootstrap',
                           'empty-bootstrap', 0, datetime('now'), datetime('now'))""",
                (generation,),
            )
            conn.execute(
                """INSERT INTO active_index_generations
                   (collection, generation, revision, updated_at)
                   VALUES ('style_examples', ?, 1, datetime('now'))""",
                (generation,),
            )
        # Windows rename fails if another setup has already initialized the seed.
        try:
            os.rename(staging, target)
        except FileExistsError:
            pass
    finally:
        staging.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
