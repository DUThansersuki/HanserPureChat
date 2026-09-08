from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hanser_agent.config import load_settings
from hanser_agent.retrieval import (
    HybridRetriever,
    RerankCandidate,
    SQLiteVectorStore,
    build_embedder,
    build_reranker,
)


@dataclass(frozen=True, slots=True)
class EvalCase:
    query: str
    keywords: list[str]
    relevant_filenames: set[str]


def load_cases(path: Path) -> list[EvalCase]:
    values = json.loads(path.read_text(encoding="utf-8"))
    return [
        EvalCase(
            query=str(item["query"]),
            keywords=[str(value) for value in item["keywords"]],
            relevant_filenames={str(value) for value in item["relevant_filenames"]},
        )
        for item in values
    ]


def rank_metrics(
    ranking: list[str],
    relevant: set[str],
    cutoff: int,
) -> tuple[float, float, float]:
    top = ranking[:cutoff]
    recall = len(set(top) & relevant) / len(relevant)
    reciprocal_rank = next(
        (
            1.0 / rank
            for rank, filename in enumerate(ranking, start=1)
            if filename in relevant
        ),
        0.0,
    )
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, filename in enumerate(top, start=1)
        if filename in relevant
    )
    ideal = sum(
        1.0 / math.log2(rank + 1)
        for rank in range(1, min(len(relevant), cutoff) + 1)
    )
    return recall, reciprocal_rank, dcg / ideal if ideal else 0.0


def unique_filenames(values) -> list[str]:
    return list(dict.fromkeys(item.filename for item in values))


async def evaluate(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    embedding = replace(
        settings.embedding,
        device=args.device or settings.embedding.device,
        dtype=args.embedding_dtype or settings.embedding.dtype,
        batch_size=args.embedding_batch_size or settings.embedding.batch_size,
    )
    embedder = build_embedder(embedding)
    retriever = HybridRetriever(
        db_path=settings.db_path,
        userdict_path=settings.userdict_path,
        config=settings.retrieval,
        embedder=embedder,
        vector_store=SQLiteVectorStore(settings.db_path),
    )
    reranker = build_reranker(settings.reranker)
    cases = load_cases(args.dataset)
    profile_names = [
        value.strip()
        for value in args.profiles.split(",")
        if value.strip()
    ]
    exit_code = 0
    for profile in profile_names:
        started = time.perf_counter()
        metrics5 = []
        metrics20 = []
        latencies = []
        misses = []
        for case in cases:
            case_started = time.perf_counter()
            if profile == "hybrid_reranker":
                candidates = await retriever.search(
                    case.query,
                    case.keywords,
                    mode="hybrid",
                    top_k=settings.retrieval.candidate_pool_size,
                )
                hits = await reranker.rerank(
                    case.query,
                    [
                        RerankCandidate(
                            document_id=item.document_id,
                            filename=item.filename,
                            text=f"文件名：{item.filename}\n{item.text}",
                            retrieval_score=item.retrieval_score,
                            chunk_id=item.chunk_id,
                        )
                        for item in candidates
                    ],
                    top_k=20,
                )
                ranking = unique_filenames(hits)
            else:
                candidates = await retriever.search(
                    case.query,
                    case.keywords,
                    mode=profile,
                    top_k=40,
                )
                ranking = unique_filenames(candidates)
            latencies.append(time.perf_counter() - case_started)
            metrics5.append(rank_metrics(ranking, case.relevant_filenames, 5))
            metrics20.append(rank_metrics(ranking, case.relevant_filenames, 20))
            if not set(ranking[:5]) & case.relevant_filenames:
                misses.append(case.query)

        recall5 = statistics.mean(item[0] for item in metrics5)
        recall20 = statistics.mean(item[0] for item in metrics20)
        mrr = statistics.mean(item[1] for item in metrics20)
        ndcg5 = statistics.mean(item[2] for item in metrics5)
        print(
            f"{profile}: Recall@5={recall5:.3f} Recall@20={recall20:.3f} "
            f"MRR={mrr:.3f} NDCG@5={ndcg5:.3f} "
            f"p50={statistics.median(latencies):.2f}s "
            f"elapsed={time.perf_counter() - started:.2f}s"
        )
        for query in misses:
            print(f"  miss@5: {query}")
        if profile == "hybrid_reranker" and (
            recall5 < args.min_recall5 or mrr < args.min_mrr
        ):
            exit_code = 1
    return exit_code


def parse_args() -> argparse.Namespace:
    backend_dir = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=backend_dir / "config.yml",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=backend_dir / "data" / "eval" / "retrieval_phase2.json",
    )
    parser.add_argument(
        "--profiles",
        default="bm25,dense,hybrid,hybrid_reranker",
    )
    parser.add_argument("--device", choices=["cpu", "cuda"])
    parser.add_argument(
        "--embedding-dtype",
        choices=["float16", "bfloat16", "float32"],
    )
    parser.add_argument("--embedding-batch-size", type=int)
    parser.add_argument("--min-recall5", type=float, default=0.8)
    parser.add_argument("--min-mrr", type=float, default=0.7)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(evaluate(parse_args())))
