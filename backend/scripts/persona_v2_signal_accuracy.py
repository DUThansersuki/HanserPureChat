from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from hanser_agent.models import ChatMessage
from hanser_agent.persona.signals import build_turn_signals


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-score frozen Signal gold labels with recorded Planner outputs."
    )
    parser.add_argument("cases", type=Path)
    parser.add_argument("traces", type=Path)
    parser.add_argument("--variant", default="A")
    args = parser.parse_args()

    traces = _jsonl(args.traces)
    planner_by_case = {
        str(row["case_id"]): row.get("plan", {}).get("persona_signals", {})
        for row in traces
        if row.get("variant") == args.variant
    }
    exact: list[dict[str, object]] = []
    unknown: list[dict[str, object]] = []
    overlaps: list[dict[str, str]] = []

    for case in _jsonl(args.cases):
        case_id = str(case["case_id"])
        gold = dict(case.get("signal_gold", {}))
        unknown_names = set(case.get("signal_unknown", []))
        for name in sorted(set(gold) & unknown_names):
            overlaps.append({"case_id": case_id, "signal": name})
        history = [
            ChatMessage(role=str(item["role"]), content=str(item["content"]))
            for item in case.get("history", [])
        ]
        signals = build_turn_signals(
            str(case["input"]),
            current_message_ref=f"eval:{case_id}",
            planner_payload=planner_by_case.get(case_id, {}),
            history_messages=history,
        )
        for name, expected in gold.items():
            item = signals.get(name)
            actual = item.value if item is not None and item.status == "observed" else None
            exact.append(
                {
                    "case_id": case_id,
                    "signal": name,
                    "expected": expected,
                    "actual": actual,
                    "correct": actual == expected,
                    "source": item.source if item is not None else None,
                    "hard_rule_eligible": item.hard_rule_eligible if item is not None else False,
                }
            )
        for name in unknown_names - set(gold):
            item = signals.get(name)
            actual = item.value if item is not None and item.status == "observed" else None
            unknown.append(
                {
                    "case_id": case_id,
                    "signal": name,
                    "actual": actual,
                    "correct": actual in {None, "unknown"},
                    "source": item.source if item is not None else None,
                    "hard_rule_eligible": item.hard_rule_eligible if item is not None else False,
                }
            )

    result = {
        "cases": str(args.cases),
        "traces": str(args.traces),
        "variant": args.variant,
        "gold": {
            "correct": sum(bool(row["correct"]) for row in exact),
            "total": len(exact),
            "accuracy": round(sum(bool(row["correct"]) for row in exact) / len(exact), 6)
            if exact
            else None,
            "misses": [row for row in exact if not row["correct"]],
        },
        "unknown": {
            "correct": sum(bool(row["correct"]) for row in unknown),
            "total": len(unknown),
            "accuracy": round(sum(bool(row["correct"]) for row in unknown) / len(unknown), 6)
            if unknown
            else None,
            "false_observations": [row for row in unknown if not row["correct"]],
        },
        "label_overlaps_excluded_from_unknown_metric": overlaps,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
