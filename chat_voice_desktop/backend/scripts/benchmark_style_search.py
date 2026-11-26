from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from hanser_agent import db
from hanser_agent.agent.tools.style_search import StyleSearchTool
from hanser_agent.models import DialoguePlan


def _read_cases(path: Path) -> list[str]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [str(row.get("input") or row.get("message")) for row in rows]


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * quantile))]


def _plan(message: str) -> DialoguePlan:
    return DialoguePlan(
        intent="chitchat",
        need_wiki=False,
        standalone_query=message,
        keywords=[],
        response_mode="casual",
        fact_sensitivity="low",
        target_length="short",
    )


async def run(args: argparse.Namespace) -> None:
    database = args.database.resolve()
    cases_path = args.cases.resolve()
    messages = _read_cases(cases_path)
    with db.connect(database) as conn:
        active = conn.execute(
            "SELECT generation,revision FROM active_index_generations WHERE collection='style_examples'"
        ).fetchone()
    if active is None:
        raise ValueError("active Style generation is unavailable")
    generation = args.generation or str(active["generation"])
    revision = int(active["revision"])
    settings = SimpleNamespace(
        db_path=database,
        userdict_path=args.userdict.resolve() if args.userdict else None,
        style=SimpleNamespace(enabled=True, min_quality=args.min_quality, top_k=args.top_k),
    )
    tool = StyleSearchTool(
        settings=settings,
        strict_v2=True,
        pinned_generation=generation,
    )

    cold_started = time.perf_counter()
    cold_result = await tool.search(messages[0], _plan(messages[0]))
    cold_ms = (time.perf_counter() - cold_started) * 1000

    for _ in range(args.warmup_rounds):
        for message in messages:
            await tool.search(message, _plan(message))

    warm_ms: list[float] = []
    for _ in range(args.iterations):
        for message in messages:
            started = time.perf_counter()
            await tool.search(message, _plan(message))
            warm_ms.append((time.perf_counter() - started) * 1000)

    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": str(database),
        "cases": str(cases_path),
        "generation": generation,
        "revision": revision,
        "case_count": len(messages),
        "warmup_rounds": args.warmup_rounds,
        "iterations": args.iterations,
        "warm_measurements": len(warm_ms),
        "cold_first_ms": cold_ms,
        "cold_first_selected": [item.id for item in cold_result.examples],
        "warm_mean_ms": statistics.mean(warm_ms),
        "warm_p50_ms": _percentile(warm_ms, 0.50),
        "warm_p95_ms": _percentile(warm_ms, 0.95),
        "warm_max_ms": max(warm_ms),
        "target_p95_ms": 50.0,
        "target_met": _percentile(warm_ms, 0.95) <= 50.0,
    }
    args.output.parent.mkdir(parents=True, exist_ok=False)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark cold and revision-cached Style retrieval")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--generation")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--userdict", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--warmup-rounds", type=int, default=1)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--min-quality", type=float, default=0.68)
    parser.add_argument("--top-k", type=int, default=3)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
