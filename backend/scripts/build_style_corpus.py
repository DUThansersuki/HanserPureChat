from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hanser_agent.config import load_settings
from hanser_agent.persona.data_pipeline import (
    inventory_sources,
    persona_statistics,
    rebuild_style_embeddings,
    rebuild_style_examples,
)
from hanser_agent.retrieval import SQLiteVectorStore, build_embedder


async def build(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    output_dir = settings.style.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    manifest = inventory_sources(
        db_path=settings.db_path,
        data_dir=settings.data_dir,
    )
    (output_dir / "raw_manifest.jsonl").write_text(
        "".join(
            json.dumps(item, ensure_ascii=False) + "\n"
            for item in manifest
        ),
        encoding="utf-8",
    )

    examples = rebuild_style_examples(
        db_path=settings.db_path,
        min_quality=settings.style.min_quality,
    )
    embedding = replace(
        settings.embedding,
        device=args.device or settings.embedding.device,
        dtype=args.dtype or settings.embedding.dtype,
        batch_size=args.batch_size or settings.embedding.batch_size,
    )
    vector_count = await rebuild_style_embeddings(
        db_path=settings.db_path,
        rows=examples,
        embedder=build_embedder(embedding),
        vector_store=SQLiteVectorStore(settings.db_path),
        batch_size=embedding.batch_size,
    )
    stats = persona_statistics(examples)
    (output_dir / "persona_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"sources={len(manifest)} real_style_examples={len(examples)} "
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
