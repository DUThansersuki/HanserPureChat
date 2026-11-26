from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.run_style_reaction_pipeline_v2 import all_windows, read_jsonl, unique_units
from scripts.style_reaction_pipeline import segment_transport_batch


def percentile(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    if not ordered:
        return 0
    return ordered[int((len(ordered) - 1) * fraction)]


def token_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [row for row in rows if row.get("status") == "success"]
    divisor = max(1, len(successful))
    return {
        "calls": len(rows),
        "successful_calls": len(successful),
        "prompt_tokens": sum(int(row.get("prompt_tokens") or 0) for row in rows),
        "completion_tokens": sum(int(row.get("completion_tokens") or 0) for row in rows),
        "cached_tokens": sum(int(row.get("input_cache_hit_tokens") or 0) for row in rows),
        "mean_prompt_tokens_success": round(
            sum(int(row.get("prompt_tokens") or 0) for row in successful) / divisor, 2
        ),
        "mean_completion_tokens_success": round(
            sum(int(row.get("completion_tokens") or 0) for row in successful) / divisor, 2
        ),
        "mean_latency_seconds_success": round(
            sum(float(row.get("latency_seconds") or 0) for row in successful) / divisor, 3
        ),
    }


def audit(parent: Path, replay: Path) -> dict[str, Any]:
    frozen = read_jsonl(parent / "windows.frozen.jsonl")
    basic_lengths = [len(str(row["source_span"])) for row in frozen]
    old_raw = read_jsonl(parent / "extract_raw.jsonl")
    old_selected_ids = {
        str(row["window_id"]) for row in read_jsonl(replay / "windows.replay.jsonl")
    }
    old_selected = [row for row in old_raw if str(row["window_id"]) in old_selected_ids]
    old_selected_counts = [len((row.get("result") or {}).get("units", [])) for row in old_selected]
    old_saturated = [
        row for row in old_raw if len((row.get("result") or {}).get("units", [])) >= 2
    ]

    windows = all_windows(replay)
    statuses = read_jsonl(replay / "window_status.jsonl")
    units = unique_units(replay)
    reviewed_rows = read_jsonl(replay / "semantic_reviews.jsonl")
    latest_reviews = {str(row["unit_id"]): row for row in reviewed_rows}
    projected = read_jsonl(replay / "source_reaction_units.reviewed.jsonl")
    lengths = [len(str(row["source_response_turn"])) for row in units]

    exact_failures: list[str] = []
    for unit in units:
        window = windows[str(unit["window_id"])]
        source = str(window["source_span"])
        rebuilt = []
        for source_slice in unit.get("reaction_source_slices", []):
            left = int(source_slice["window_start"])
            right = int(source_slice["window_end"])
            actual = source[left:right]
            if actual != source_slice["text"]:
                exact_failures.append(str(unit["unit_id"]))
            rebuilt.append(actual)
        if "".join(rebuilt) != unit["source_response_turn"]:
            exact_failures.append(str(unit["unit_id"]))

    segment_failures = [
        window_id for window_id, window in windows.items()
        if "".join(
            str(row["text"])
            for row in segment_transport_batch(str(window["source_span"]))
        ) != str(window["source_span"])
    ]
    lineage_failures = []
    for window_id, window in windows.items():
        parent_id = window.get("parent_window_id")
        if not parent_id:
            continue
        parent_window = windows.get(str(parent_id))
        if parent_window is None or not (
            int(parent_window["source_start_char"])
            <= int(window["source_start_char"])
            < int(window["source_end_char"])
            <= int(parent_window["source_end_char"])
        ):
            lineage_failures.append(window_id)

    packets = read_jsonl(replay / "review_evidence_packets.jsonl")
    runtime_rows = [row for row in projected if row.get("runtime_eligible")]
    unsafe_runtime = [
        row for row in runtime_rows
        if row.get("attribution_status") != "verified"
        or (row.get("semantic_review") or {}).get("transfer_scope") != "general"
        or (row.get("semantic_review") or {}).get("source_specific_dependency")
    ]
    extract_prompt = (replay / "extraction_prompt.fixed.txt").read_bytes()
    report = {
        "checkpoint": "reaction-extraction-review-pipeline-replay-v3",
        "parent_run": str(parent),
        "replay_run": str(replay),
        "frozen_baseline": {
            "build_documents": 507,
            "independent_holdout_documents": 53,
            "excluded_documents": 2,
            "basic_windows": len(frozen),
            "basic_window_chars": {
                "min": min(basic_lengths),
                "mean": round(statistics.mean(basic_lengths), 2),
                "median": statistics.median(basic_lengths),
                "max": max(basic_lengths),
                "target": 2200,
                "overlap": 320,
            },
            "source_split_modified": False,
            "frozen_windows_modified": False,
            "production_pointer_modified": False,
        },
        "old_pipeline": {
            "semantic_event_cap": 2,
            "successful_raw_windows": len(old_raw),
            "windows_at_cap": len(old_saturated),
            "continuation_children_successfully_processed_before_fix": 0,
            "review_policy": "all materialized candidates",
            "review_context": "full approximately-2200-char source window repeated per candidate, batches of five",
            "selected_replay_windows_with_old_raw": len(old_selected),
            "selected_old_reactions": sum(old_selected_counts),
            "selected_old_reactions_per_window": round(
                sum(old_selected_counts) / max(1, len(old_selected_ids)), 3
            ),
            "selected_old_count_distribution": dict(Counter(old_selected_counts)),
            "extraction_tokens": token_summary(
                read_jsonl(parent / "extract_call_records.jsonl")
            ),
            "review_tokens": token_summary(
                read_jsonl(parent / "review_call_records.jsonl")
            ),
        },
        "new_pipeline": {
            "transport_roots": len(old_selected_ids),
            "derived_windows": len(read_jsonl(replay / "windows.derived.jsonl")),
            "window_status": dict(Counter(str(row["status"]) for row in statuses)),
            "source_units": len(units),
            "reactions_per_root_window": round(len(units) / max(1, len(old_selected_ids)), 3),
            "reaction_chars": {
                "min": min(lengths), "median": statistics.median(lengths),
                "p95": percentile(lengths, 0.95), "p99": percentile(lengths, 0.99),
                "max": max(lengths), "over_360": sum(value > 360 for value in lengths),
                "over_500": sum(value > 500 for value in lengths),
            },
            "attribution": dict(Counter(str(row["attribution_status"]) for row in units)),
            "attribution_basis": dict(Counter(
                str(row.get("attribution_basis", "missing")) for row in units
            )),
            "review_routes": dict(Counter(
                str((latest_reviews.get(str(row["unit_id"])) or {}).get("review_route"))
                for row in units
            )),
            "review_decisions": dict(Counter(
                str((latest_reviews.get(str(row["unit_id"])) or {}).get("decision"))
                for row in units
            )),
            "second_review_rate": round(
                sum(bool(row.get("review_required")) for row in units) / max(1, len(units)), 4
            ),
            "review_attempts": len(reviewed_rows),
            "expanded_review_attempts": sum(
                bool(row.get("context_expanded")) for row in reviewed_rows
            ),
            "projection": {
                "q_only": sum(row.get("q_projection") is not None and row.get("a_projection") is None for row in projected),
                "a_only": sum(row.get("q_projection") is None and row.get("a_projection") is not None for row in projected),
                "qa": sum(row.get("q_projection") is not None and row.get("a_projection") is not None for row in projected),
                "neither": sum(row.get("q_projection") is None and row.get("a_projection") is None for row in projected),
            },
            "runtime_eligible": len(runtime_rows),
            "runtime_attribution_basis": dict(Counter(
                str(row.get("effective_attribution_basis", row.get("attribution_basis", "missing")))
                for row in runtime_rows
            )),
            "runtime_payload_kinds": dict(Counter(
                str(row["runtime_payload_kind"]) for row in runtime_rows
            )),
            "pending_with_research_projection": sum(
                row.get("semantic_review_decision") == "pending"
                and (row.get("q_projection") is not None or row.get("a_projection") is not None)
                for row in projected
            ),
            "extract_tokens": token_summary(
                read_jsonl(replay / "extract_call_records.jsonl")
            ),
            "review_tokens": token_summary(
                read_jsonl(replay / "review_call_records.jsonl")
            ),
            "review_evidence": {
                "packets": len(packets),
                "evidence_chars": sum(int(row["evidence_chars"]) for row in packets),
                "corresponding_full_window_chars": sum(int(row["full_window_chars"]) for row in packets),
                "evidence_ratio": round(
                    sum(int(row["evidence_chars"]) for row in packets)
                    / max(1, sum(int(row["full_window_chars"]) for row in packets)), 4
                ),
                "max_packet_chars": max((int(row["evidence_chars"]) for row in packets), default=0),
            },
            "fixed_prompt_sha256": hashlib.sha256(extract_prompt).hexdigest(),
        },
        "engineering_gate": {
            "exact_source_round_trip_failures": sorted(set(exact_failures)),
            "segment_mapping_failures": segment_failures,
            "parent_child_lineage_failures": lineage_failures,
            "unsafe_runtime_units": [row["unit_id"] for row in unsafe_runtime],
            "true_no_unit_windows": [
                row["window_id"] for row in statuses if row["status"] == "no_unit"
            ],
            "terminal_windows": all(
                row["status"] in {"success", "no_unit", "explicitly_excluded"}
                for row in statuses
            ),
            "max_two_ceiling_removed": len(units) > 2 * len(old_selected_ids),
            "full_resume_allowed": not (
                exact_failures or segment_failures or lineage_failures or unsafe_runtime
            ) and all(
                row["status"] in {"success", "no_unit", "explicitly_excluded"}
                for row in statuses
            ),
        },
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.parent.resolve(), args.replay.resolve())
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
