"""Summarize an external-only targeted guard replay without model calls."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def mapped(row: dict) -> str:
    preferred = row["verdict"]["preferred"]
    return "tie" if preferred == "tie" else row[f"{preferred}_variant"]


def unsafe(score: dict) -> bool:
    return bool(score["unsupported_first_person"] or score["style_fact_leakage"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    rows = read_jsonl(run / "judgments.jsonl")
    retrieval = json.loads((run / "retrieval_replay.json").read_text(encoding="utf-8"))
    by_case: dict[str, list[dict]] = defaultdict(list)
    flags: dict[str, dict[str, list[bool]]] = defaultdict(
        lambda: {"B": [], "C": [], "B_leak": [], "C_leak": []}
    )
    for row in rows:
        by_case[row["case_id"]].append(row)
        if row["status"] != "MEASURED":
            continue
        for side in ("left", "right"):
            variant = row[f"{side}_variant"]
            score = row["verdict"][side]
            flags[row["case_id"]][variant].append(unsafe(score))
            flags[row["case_id"]][f"{variant}_leak"].append(
                bool(score["style_fact_leakage"])
            )

    outcomes = Counter()
    inconsistent = []
    for case_id, items in by_case.items():
        measured = [row for row in items if row["status"] == "MEASURED"]
        values = [mapped(row) for row in measured]
        if len(values) == 2 and values[0] == values[1]:
            outcomes[values[0]] += 1
        else:
            outcomes["inconsistent"] += 1
            inconsistent.append(case_id)
    candidate_unsafe = {case_id for case_id, value in flags.items() if any(value["C"])}
    baseline_unsafe = {case_id for case_id, value in flags.items() if any(value["B"])}
    candidate_leakage = {case_id for case_id, value in flags.items() if any(value["C_leak"])}
    missing = [row for row in rows if row["status"] != "MEASURED"]
    report = {
        "evaluation_type": "external_only_conservative_replay",
        "not_full_end_to_end": True,
        "cases": len(by_case),
        "judgment_rows": len(rows),
        "measured_rows": len(rows) - len(missing),
        "missing_rows": missing,
        "case_outcomes": dict(outcomes),
        "inconsistent_cases": sorted(inconsistent),
        "candidate_unsafe_cases": sorted(candidate_unsafe),
        "baseline_unsafe_cases": sorted(baseline_unsafe),
        "candidate_only_unsafe_cases": sorted(candidate_unsafe - baseline_unsafe),
        "baseline_only_unsafe_cases": sorted(baseline_unsafe - candidate_unsafe),
        "shared_unsafe_cases": sorted(candidate_unsafe & baseline_unsafe),
        "candidate_style_leakage_cases": sorted(candidate_leakage),
        "retrieval_guard": {
            "source_examples": sum(len(row["source_example_ids"]) for row in retrieval),
            "removed_examples": sum(len(row["removed"]) for row in retrieval),
            "backfilled": False,
        },
        "replay_verdict": "HARD_GATE_FINDINGS" if candidate_unsafe or missing else "MEASURED",
        "release_verdict": "HOLD",
        "reasons": [
            "The latest static Style guard removed no examples in this targeted prior top-k replay.",
            "Candidate outputs still contain unsupported first-person claims in: "
            + ", ".join(sorted(candidate_unsafe)) + ".",
            "This is a conservative replay over prior top-k and is not full end-to-end retrieval.",
        ],
        "production_switched": False,
        "source_hashes": {
            name: hashlib.sha256((run / name).read_bytes()).hexdigest()
            for name in ("outputs.jsonl", "judgments.jsonl", "retrieval_replay.json", "manifest.json")
        },
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
