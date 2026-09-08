from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hanser_agent.config import load_settings
from hanser_agent.retrieval import SQLiteVectorStore, build_embedder
from hanser_agent.retrieval.indexer import (
    rebuild_document_chunks,
    rebuild_fact_embeddings,
)


async def build(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    embedding = replace(
        settings.embedding,
        device=args.device or settings.embedding.device,
        dtype=args.dtype or settings.embedding.dtype,
        batch_size=args.batch_size or settings.embedding.batch_size,
    )
    started = time.perf_counter()
    chunk_count = rebuild_document_chunks(
        db_path=settings.db_path,
        userdict_path=settings.userdict_path,
        config=settings.retrieval,
    )
    embedder = build_embedder(embedding)
    vector_count = await rebuild_fact_embeddings(
        db_path=settings.db_path,
        embedder=embedder,
        vector_store=SQLiteVectorStore(settings.db_path),
        batch_size=embedding.batch_size,
    )
    print(
        f"documents_db={settings.db_path} chunks={chunk_count} "
        f"vectors={vector_count} model={embedding.model} "
        f"device={embedding.device} elapsed={time.perf_counter() - started:.2f}s"
    )
    return 0


def parse_args() -> argparse.Namespace:
    backend_dir = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=backend_dir / "config.yml",
    )
    parser.add_argument("--device", choices=["cpu", "cuda"])
    parser.add_argument(
        "--dtype",
        choices=["float16", "bfloat16", "float32"],
    )
    parser.add_argument("--batch-size", type=int)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(build(parse_args())))
