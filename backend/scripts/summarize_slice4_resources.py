from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from pathlib import Path


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int((len(ordered) - 1) * fraction)))]


def summarize(path: Path) -> dict[str, object]:
    data = json.loads((path / "resource_report.json").read_text(encoding="utf-8"))
    output: dict[str, object] = {
        "policy": data["policy"],
        "gpu_min_mib": data["gpu_min_mib"],
        "gpu_peak_mib": data["gpu_peak_mib"],
    }
    for name, rows in (
        ("all", data["turns"]),
        ("wiki", [row for row in data["turns"] if row["kind"] == "wiki"]),
        ("casual", [row for row in data["turns"] if row["kind"] == "casual"]),
    ):
        values = [float(row["seconds"]) for row in rows]
        output[name] = {
            "n": len(values),
            "p50": statistics.median(values),
            "p95": percentile(values, 0.95),
            "max": max(values),
            "http_failures": sum(int(row["status"]) != 200 for row in rows),
        }
    stages: dict[str, object] = {}
    for stage in ("planner", "wiki", "style", "memory", "responder", "post_turn"):
        values = [
            float(row["stage_seconds"][stage])
            for row in data["turns"]
            if stage in row["stage_seconds"]
        ]
        stages[stage] = (
            {
                "n": len(values),
                "p50": statistics.median(values),
                "p95": percentile(values, 0.95),
                "max": max(values),
            }
            if values
            else None
        )
    output["stages"] = stages
    database = sqlite3.connect(path / "audit.db")
    try:
        try:
            responses = [
                json.loads(row[0])
                for row in database.execute(
                    "SELECT response_json FROM request_executions "
                    "WHERE user_id='slice4-resource' AND response_json IS NOT NULL"
                )
            ]
        except sqlite3.OperationalError:
            responses = []
    finally:
        database.close()
    output["degraded_responses"] = sum(
        response.get("status") == "degraded" for response in responses
    )
    reasons: dict[str, int] = {}
    for response in responses:
        for reason in response.get("degraded_reasons", []):
            reasons[reason] = reasons.get(reason, 0) + 1
    output["degraded_reasons"] = reasons
    (path / "summary.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directories", nargs="+", type=Path)
    args = parser.parse_args()
    print(json.dumps([summarize(path) for path in args.directories], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
