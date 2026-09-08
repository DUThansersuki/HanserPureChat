from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hanser_agent.agent.tools.wiki_search import WikiSearchTool
from hanser_agent.config import Settings, load_settings
from hanser_agent.llm import LLMClient
from hanser_agent.retrieval import (
    BM25Reranker,
    HybridRetriever,
    Reranker,
    SQLiteVectorStore,
    build_embedder,
    build_reranker,
)
from hanser_agent.retrieval.legacy_prometheus import LegacyPrometheusReranker


@dataclass(slots=True)
class EvalCase:
    query: str
    keywords: list[str]
    relevant_filenames: set[str]


def load_cases(path: Path) -> list[EvalCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        EvalCase(
            query=str(item["query"]),
            keywords=[str(value) for value in item["keywords"]],
            relevant_filenames={
                str(value)
                for value in item["relevant_filenames"]
            },
        )
        for item in raw
    ]


def score_ranking(
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
    ideal_count = min(len(relevant), cutoff)
    ideal_dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank in range(1, ideal_count + 1)
    )
    ndcg = dcg / ideal_dcg if ideal_dcg else 0.0
    return recall, reciprocal_rank, ndcg


async def build_profiles(
    names: list[str],
    settings: Settings,
) -> tuple[dict[str, Reranker], LLMClient | None]:
    profiles: dict[str, Reranker] = {}
    llm: LLMClient | None = None
    package_dir = Path(__file__).resolve().parents[1] / "hanser_agent"

    for name in names:
        if name == "bm25":
            profiles[name] = BM25Reranker()
        elif name == "local":
            profiles[name] = build_reranker(settings.reranker)
        elif name == "prometheus":
            llm = llm or LLMClient(settings)
            profiles[name] = LegacyPrometheusReranker(
                llm=llm,
                model_profile=settings.prometheus,
                prompt=(package_dir / "prompts" / "prometheus.md").read_text(
                    encoding="utf-8"
                ),
            )
        else:
            raise ValueError(f"unknown profile: {name}")
    return profiles, llm


async def evaluate(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    cases = load_cases(args.dataset)
    names = [value.strip() for value in args.profiles.split(",") if value.strip()]
    profiles, llm = await build_profiles(names, settings)
    embedder = build_embedder(settings.embedding)
    retriever = HybridRetriever(
        db_path=settings.db_path,
        userdict_path=settings.userdict_path,
        config=settings.retrieval,
        embedder=embedder,
        vector_store=SQLiteVectorStore(settings.db_path),
    )
    prepared = []
    candidate_recall = []
    for case in cases:
        candidates = await retriever.search(
            case.query,
            case.keywords,
            mode="hybrid",
            top_k=settings.retrieval.candidate_pool_size,
        )
        passages = WikiSearchTool.prepare_rerank_candidates(candidates)
        prepared.append((case, passages))
        candidate_recall.append(
            float(
                bool(
                    {item.filename for item in passages}
                    & case.relevant_filenames
                )
            )
        )

    print(f"cases={len(cases)} candidate_recall={sum(candidate_recall) / len(cases):.3f}")
    exit_code = 0
    try:
        for name, reranker in profiles.items():
            started = time.perf_counter()
            metrics = []
            misses = []
            profile_error: Exception | None = None
            for case, passages in prepared:
                try:
                    hits = await reranker.rerank(
                        case.query,
                        passages,
                        top_k=args.cutoff,
                    )
                except Exception as exc:
                    profile_error = exc
                    break
                ranking = [item.filename for item in hits]
                metrics.append(
                    score_ranking(
                        ranking,
                        case.relevant_filenames,
                        args.cutoff,
                    )
                )
                if not set(ranking) & case.relevant_filenames:
                    misses.append(case.query)

            if profile_error is not None:
                elapsed = time.perf_counter() - started
                print(
                    f"{name}: FAILED after {elapsed:.2f}s: "
                    f"{type(profile_error).__name__}: {profile_error}"
                )
                exit_code = 1
                continue

            recall = sum(item[0] for item in metrics) / len(metrics)
            mrr = sum(item[1] for item in metrics) / len(metrics)
            ndcg = sum(item[2] for item in metrics) / len(metrics)
            elapsed = time.perf_counter() - started
            print(
                f"{name}: Recall@{args.cutoff}={recall:.3f} "
                f"MRR={mrr:.3f} NDCG@{args.cutoff}={ndcg:.3f} "
                f"elapsed={elapsed:.2f}s"
            )
            for query in misses:
                print(f"  miss: {query}")
            if name == "local" and (
                recall < args.min_local_recall
                or mrr < args.min_local_mrr
            ):
                exit_code = 1
    finally:
        if llm is not None:
            await llm.close()
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
        default="bm25,local",
        help="comma-separated: bm25,local,prometheus",
    )
    parser.add_argument("--cutoff", type=int, default=5)
    parser.add_argument("--min-local-recall", type=float, default=0.8)
    parser.add_argument("--min-local-mrr", type=float, default=0.7)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(evaluate(parse_args())))
