"""Apply the current static Style guard to prior retrieved top-k without models."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from hanser_agent.models import StyleExample  # noqa: E402
from p0_external_guard_replay import guard  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-outputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.source_outputs.resolve()
    rows = [
        json.loads(line)
        for line in source.read_text(encoding="utf-8").splitlines()
        if line
    ]
    cases = []
    reasons = Counter()
    for row in rows:
        if row.get("variant") != "C" or row.get("status") != "MEASURED":
            continue
        removed = []
        for raw in row.get("style_examples", []):
            example = StyleExample.model_validate(raw)
            reason = guard(example, row["message"])
            if reason:
                reasons[reason] += 1
                removed.append({"id": example.id, "reason": reason})
        cases.append({
            "case_id": row["case_id"],
            "source_examples": len(row.get("style_examples", [])),
            "removed": removed,
        })
    report = {
        "scope": "prior_retrieved_top_k_only",
        "model_calls": 0,
        "source_outputs": str(source),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "style_search_source_sha256": hashlib.sha256(
            (ROOT / "backend/hanser_agent/agent/tools/style_search.py").read_bytes()
        ).hexdigest(),
        "cases": len(cases),
        "source_examples": sum(row["source_examples"] for row in cases),
        "removed_examples": sum(len(row["removed"]) for row in cases),
        "affected_cases": [row["case_id"] for row in cases if row["removed"]],
        "reasons": dict(reasons),
        "details": [row for row in cases if row["removed"]],
        "limitation": "Does not search or backfill lower-ranked candidates.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: report[key] for key in (
        "cases", "source_examples", "removed_examples", "affected_cases", "reasons"
    )}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
