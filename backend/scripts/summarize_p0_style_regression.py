"""Summarize an existing order-balanced style regression without model calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path


CLUSTERS = {
    "social": {"greeting", "casual_chat", "short_reply", "long_reply", "being_teased", "self_deprecation", "being_praised", "user_upset", "user_excited", "comforting", "uncertainty"},
    "factual": {"known_fact", "ambiguous_fact", "unknown_fact", "followup_factual", "mixed_fact"},
    "memory": {"recall", "correction", "conflict", "false_memory_trap", "cross_session_recall"},
    "relationship": {"stranger", "familiar", "warm_interaction", "disagreement", "temporary_tension"},
    "boundary": {"customer_service", "identity_challenge", "style_demand", "prompt_injection"},
    "narrative": {"awkward_silence", "deep_conversation", "nostalgia", "late_night", "storytelling", "invented_emotion", "invented_condition"},
}


def mapped(row: dict) -> str:
    preferred = row["verdict"]["preferred"]
    return "tie" if preferred == "tie" else row[f"{preferred}_variant"]


def bootstrap(values: list[float], seed: int = 906, runs: int = 10000) -> list[float]:
    if not values:
        return [0.0, 0.0]
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(values) for _ in values) / len(values) for _ in range(runs))
    return [round(means[int(.025 * runs)], 4), round(means[int(.975 * runs)], 4)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    rows = [json.loads(line) for line in (run / "judgments.jsonl").read_text(encoding="utf-8").splitlines() if line]
    by_case: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_case[row["case_id"]].append(row)
    missing = [
        {"case_id": row["case_id"], "reverse": row["reverse"], "error": row.get("error")}
        for row in rows if row["status"] != "MEASURED"
    ]
    complete: dict[str, list[dict]] = {
        case_id: items for case_id, items in by_case.items()
        if len([item for item in items if item["status"] == "MEASURED"]) == 2
    }
    effects: dict[str, float] = {}
    outcomes = Counter()
    inconsistent = []
    for case_id, items in complete.items():
        values = [mapped(row) for row in items if row["status"] == "MEASURED"]
        numeric = [{"C": 1.0, "B": -1.0, "tie": 0.0}[value] for value in values]
        effects[case_id] = sum(numeric) / 2
        if values[0] == values[1]:
            outcomes[values[0]] += 1
        else:
            outcomes["inconsistent"] += 1
            inconsistent.append(case_id)
    non_auto = [row for row in rows if row["status"] == "MEASURED" and not row.get("auto_identical")]
    raw_positions = Counter(row["verdict"]["preferred"] for row in non_auto)
    candidate_only_unsafe = set()
    candidate_style_leakage = set()
    for row in non_auto:
        c_side = "left" if row["left_variant"] == "C" else "right"
        b_side = "right" if c_side == "left" else "left"
        c_scores, b_scores = row["verdict"][c_side], row["verdict"][b_side]
        c_unsafe = c_scores["unsupported_first_person"] or c_scores["style_fact_leakage"]
        b_unsafe = b_scores["unsupported_first_person"] or b_scores["style_fact_leakage"]
        if c_unsafe and not b_unsafe:
            candidate_only_unsafe.add(row["case_id"])
        if c_scores["style_fact_leakage"]:
            candidate_style_leakage.add(row["case_id"])
    clusters = {}
    for name, ids in CLUSTERS.items():
        values = [effects[case_id] for case_id in ids if case_id in effects]
        clusters[name] = {
            "complete_cases": len(values),
            "mean_directional_effect": round(sum(values) / len(values), 4) if values else None,
            "bootstrap_95_ci": bootstrap(values) if values else None,
        }
    values = list(effects.values())
    report = {
        "source_run": str(run),
        "judgment_rows": len(rows), "measured_rows": sum(row["status"] == "MEASURED" for row in rows),
        "missing_rows": missing, "complete_case_pairs": len(complete),
        "order_consistent_pairs": sum(outcomes[key] for key in ("B", "C", "tie")),
        "order_consistency_rate": round(sum(outcomes[key] for key in ("B", "C", "tie")) / len(complete), 4) if complete else 0,
        "case_outcomes": dict(outcomes), "inconsistent_cases": sorted(inconsistent),
        "raw_position_preferences": dict(raw_positions),
        "mean_directional_effect": round(sum(values) / len(values), 4) if values else None,
        "bootstrap_95_ci": bootstrap(values), "clusters": clusters,
        "candidate_only_unsafe_cases": sorted(candidate_only_unsafe),
        "candidate_style_leakage_cases": sorted(candidate_style_leakage),
        "regression_verdict": (
            "INCOMPLETE_AND_HARD_GATE_FINDINGS" if missing and candidate_style_leakage
            else "INCOMPLETE" if missing
            else "HARD_GATE_FINDINGS" if candidate_style_leakage
            else "MEASURED"
        ),
        "release_verdict": "HOLD",
        "reasons": [
            *(
                [f"{len(missing)} order-balanced judgments remain missing."]
                if missing else []
            ),
            *(
                ["Candidate output has judge-flagged style fact leakage in: "
                 + ", ".join(sorted(candidate_style_leakage)) + "."]
                if candidate_style_leakage else []
            ),
            "Independent human review was explicitly waived by the owner; this run is an automated proxy.",
        ],
        "production_switched": False,
        "source_hashes": {
            name: hashlib.sha256((run / name).read_bytes()).hexdigest()
            for name in ("outputs.jsonl", "judgments.jsonl", "retrieval.json", "summary.json")
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("complete_case_pairs", "order_consistency_rate", "case_outcomes", "bootstrap_95_ci", "regression_verdict", "release_verdict")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
