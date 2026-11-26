"""Finish pending descendants of explicit frozen roots within a provider budget."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.prepare_style_reaction_resume import root_window
from scripts.run_style_reaction_pipeline_v2 import all_windows, extract, read_jsonl


async def resume_families(output: Path, roots: list[str], max_calls: int,
                          max_total_tokens: int, batch_size: int = 3,
                          concurrency: int = 3) -> dict:
    records_before = len(read_jsonl(output / "extract_call_records.jsonl"))
    windows = all_windows(output)
    for identity in roots:
        if identity not in windows or windows[identity].get("parent_window_id"):
            raise ValueError(f"not_a_known_base_window:{identity}")
    calls = tokens = 0
    while calls < max_calls and (not max_total_tokens or tokens < max_total_tokens):
        windows = all_windows(output)
        pending = [row["window_id"] for row in read_jsonl(output / "window_status.jsonl")
                   if row["status"] == "pending" and root_window(row["window_id"], windows) in roots]
        if not pending:
            break
        await extract(SimpleNamespace(output=output, window_ids=pending, window_id=None,
            retry_failures=False, max_attempts=2, derived_first=True,
            max_calls=min(batch_size, max_calls - calls), batch_size=batch_size, concurrency=concurrency,
            model_max_tokens=5000, max_total_tokens=0))
        records = read_jsonl(output / "extract_call_records.jsonl")[records_before:]
        calls = len(records)
        tokens = sum(int(row.get("total_tokens") or 0) for row in records)
    windows = all_windows(output)
    statuses = {row["window_id"]: row for row in read_jsonl(output / "window_status.jsonl")}
    return {"roots": {identity: statuses[identity]["status"] for identity in roots},
            "unfinished": {identity: row["status"] for identity, row in statuses.items()
                if root_window(identity, windows) in roots
                and row["status"] not in {"success", "no_unit", "explicitly_excluded"}},
            "calls": calls, "tokens": tokens}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--roots", nargs="+", required=True)
    parser.add_argument("--max-calls", type=int, required=True)
    parser.add_argument("--max-total-tokens", type=int, required=True)
    parser.add_argument("--batch-size", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=3)
    options = parser.parse_args()
    print(json.dumps(asyncio.run(resume_families(**vars(options))), ensure_ascii=False, indent=2))
