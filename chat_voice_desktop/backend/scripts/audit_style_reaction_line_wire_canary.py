from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from hanser_agent.config import load_settings
from hanser_agent.model_gateway import ModelGateway
from hanser_agent.models import ChatMessage
from scripts.run_style_reaction_pipeline_v2 import all_windows, read_jsonl
from scripts.style_reaction_pipeline import (
    LINE_WIRE_SCHEMA_VERSION,
    ReactionExtractionResult,
    ReactionExtractionWireResult,
    extraction_line_wire_system_prompt,
    materialize_reaction_events,
    provider_segment_id_map,
    render_segmented_batch_line_wire,
    render_segmented_batch_legacy_xml,
    segment_transport_batch,
)


def percentile(values: list[int], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 2)


def semantic_metrics(
    result: ReactionExtractionResult,
    units: list[dict[str, Any]],
) -> dict[str, Any]:
    boundaries = [
        int(row["reaction_source_end_char"]) - int(row["reaction_source_start_char"])
        for row in units
    ]
    return {
        "reaction_count": len(units),
        "reaction_boundary_chars": {
            "p50": percentile(boundaries, 0.5),
            "p95": percentile(boundaries, 0.95),
            "max": max(boundaries, default=0),
        },
        "speaker_distribution": dict(Counter(
            str(row["speaker_candidate"]) for row in units
        )),
        "trigger_relation": dict(Counter(
            str(row["trigger_basis"]) for row in units
        )),
        "behavior_ids": dict(Counter(
            behavior for row in units for behavior in row.get("behavior_ids", [])
        )),
        "risk_flags": dict(Counter(
            risk for row in units for risk in row.get("risk_flags", [])
        )),
        "coverage_status": result.coverage_status,
        "continuation_from_segment_id": result.continuation_from_segment_id,
    }


async def run(args: argparse.Namespace) -> None:
    run_dir = args.run_dir.resolve()
    artifact_dir = run_dir / "wire_canary_v8"
    artifact_dir.mkdir(exist_ok=True)
    windows = all_windows(run_dir)
    old_raw = {
        str(row["window_id"]): row
        for row in read_jsonl(run_dir / "extract_raw.jsonl")
        if row.get("extraction_schema_version") == "reaction-event-extraction-v7-compact-wire"
    }
    old_calls = {
        str(row["window_id"]): row
        for row in read_jsonl(run_dir / "extract_call_records.jsonl")
        if row.get("extraction_schema_version") == "reaction-event-extraction-v7-compact-wire"
        and row.get("status") == "success"
    }
    old_prompt_path = run_dir / "extraction_prompt.reaction-event-extraction-v7-compact-wire.fixed.txt"
    old_system = old_prompt_path.read_text(encoding="utf-8")
    new_system = extraction_line_wire_system_prompt()
    settings = load_settings(PROJECT_ROOT / "backend" / "config.yml")
    gateway = ModelGateway({
        "planner": replace(settings.planner, max_tokens=args.max_tokens, temperature=0.0)
    })
    comparisons: list[dict[str, Any]] = []
    try:
        for window_id in args.window_ids:
            window = windows[window_id]
            segments = segment_transport_batch(str(window["source_span"]))
            mapping = provider_segment_id_map(segments)
            old_user = render_segmented_batch_legacy_xml(window, segments)
            new_user = render_segmented_batch_line_wire(window, segments)
            if args.fresh_old:
                old_capture = gateway.start_call_capture()
                old_wire = await gateway.generate_json(
                    "planner",
                    [
                        ChatMessage(role="system", content=old_system),
                        ChatMessage(role="user", content=old_user),
                    ],
                    ReactionExtractionWireResult,
                )
                old_call = gateway.finish_call_capture(old_capture)[-1]
                old_result = old_wire.to_result()
            else:
                old_call = old_calls[window_id]
                old_result = ReactionExtractionResult.model_validate(
                    old_raw[window_id]["result"]
                )
            old_units, old_failures = materialize_reaction_events(window, segments, old_result)
            capture = gateway.start_call_capture()
            wire = await gateway.generate_json(
                "planner",
                [
                    ChatMessage(role="system", content=new_system),
                    ChatMessage(role="user", content=new_user),
                ],
                ReactionExtractionWireResult,
            )
            calls = gateway.finish_call_capture(capture)
            result = wire.to_result(mapping)
            units, failures = materialize_reaction_events(window, segments, result)
            call = calls[-1]
            correctness = {
                "materialize_success": not failures and len(units) == len(result.events),
                "exact_source_slice": all(row.get("exact_source_slice") for row in units),
                "mechanical_failure_count": len(failures),
                "source_round_trip_failure_count": sum(
                    str(row["source_response_turn"]) != "".join(
                        str(item["text"]) for item in row["reaction_source_slices"]
                    )
                    for row in units
                ),
                "segment_mapping_failure_count": sum(
                    span.segment_id not in {str(row["segment_id"]) for row in segments}
                    for event in result.events
                    for span in [*event.reaction_spans, *event.trigger_spans]
                ),
                "parent_child_lineage_regression_count": sum(
                    row.get("parent_window_id") != window.get("parent_window_id")
                    for row in units
                ),
            }
            comparison = {
                "window_id": window_id,
                "source_chars": len(str(window["source_span"])),
                "canonical_segment_count": len(segments),
                "provider_segment_count": len(mapping),
                "old": {
                    "provider_transcript_chars": len(old_user),
                    "user_message_chars": len(old_user),
                    "system_message_chars": len(old_system),
                    "prompt_tokens": old_call.get("prompt_tokens"),
                    "cache_hit_tokens": old_call.get("input_cache_hit_tokens"),
                    "cache_miss_tokens": old_call.get("input_cache_miss_tokens"),
                    "completion_tokens": old_call.get("completion_tokens"),
                    "tokens_per_event": round(
                        int(old_call.get("total_tokens") or 0) /
                        max(1, len(old_units)), 2
                    ),
                    "correctness": {"mechanical_failure_count": len(old_failures)},
                    "semantics": semantic_metrics(old_result, old_units),
                },
                "new": {
                    "provider_transcript_chars": len(new_user),
                    "user_message_chars": len(new_user),
                    "system_message_chars": len(new_system),
                    "prompt_tokens": call.get("prompt_tokens"),
                    "cache_hit_tokens": call.get("input_cache_hit_tokens"),
                    "cache_miss_tokens": call.get("input_cache_miss_tokens"),
                    "completion_tokens": call.get("completion_tokens"),
                    "tokens_per_event": round(
                        int(call.get("total_tokens") or 0) / max(1, len(units)), 2
                    ),
                    "correctness": correctness,
                    "semantics": semantic_metrics(result, units),
                },
                "provider_raw_wire": wire.model_dump(mode="json"),
                "normalized_canonical": result.model_dump(mode="json"),
                "mechanical_failures": failures,
            }
            comparisons.append(comparison)
            (artifact_dir / f"{window_id}.json").write_text(
                json.dumps(comparison, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    finally:
        await gateway.close()

    old_prompt = sum(int(row["old"]["prompt_tokens"] or 0) for row in comparisons)
    new_prompt = sum(int(row["new"]["prompt_tokens"] or 0) for row in comparisons)
    report = {
        "extraction_schema_version": LINE_WIRE_SCHEMA_VERSION,
        "window_count": len(comparisons),
        "window_ids": args.window_ids,
        "correctness_gate_passed": all(
            row["new"]["correctness"]["materialize_success"]
            and row["new"]["correctness"]["exact_source_slice"]
            and row["new"]["correctness"]["mechanical_failure_count"] == 0
            and row["new"]["correctness"]["source_round_trip_failure_count"] == 0
            and row["new"]["correctness"]["segment_mapping_failure_count"] == 0
            and row["new"]["correctness"]["parent_child_lineage_regression_count"] == 0
            for row in comparisons
        ),
        "totals": {
            "old_prompt_tokens": old_prompt,
            "new_prompt_tokens": new_prompt,
            "prompt_token_reduction": old_prompt - new_prompt,
            "prompt_token_reduction_percent": round(
                (old_prompt - new_prompt) / old_prompt * 100, 2
            ) if old_prompt else None,
            "old_completion_tokens": sum(
                int(row["old"]["completion_tokens"] or 0) for row in comparisons
            ),
            "new_completion_tokens": sum(
                int(row["new"]["completion_tokens"] or 0) for row in comparisons
            ),
            "old_event_count": sum(
                int(row["old"]["semantics"]["reaction_count"]) for row in comparisons
            ),
            "new_event_count": sum(
                int(row["new"]["semantics"]["reaction_count"]) for row in comparisons
            ),
        },
        "windows": comparisons,
    }
    report_path = artifact_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "report_path": str(report_path),
        "correctness_gate_passed": report["correctness_gate_passed"],
        "totals": report["totals"],
    }, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--window-ids", nargs="+", required=True)
    parser.add_argument("--max-tokens", type=int, default=6000)
    parser.add_argument("--fresh-old", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
