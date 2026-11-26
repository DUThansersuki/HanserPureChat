"""Restore interrupted ledger commits from saved normalized extraction results.

This performs no provider calls and never treats an inflight request without
a saved result as successful.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_style_reaction_pipeline_v2 import (
    all_windows, append_jsonl, read_jsonl, reconcile_parent_statuses,
    replace_statuses, write_progress,
)
from scripts.style_reaction_pipeline import (
    EXTRACTION_SCHEMA_VERSION, ReactionExtractionResult,
    child_windows_for_partial_coverage, materialize_reaction_events,
    route_semantic_review, segment_transport_batch,
)


def restore(output: Path) -> dict:
    windows = all_windows(output)
    statuses = {row["window_id"]: row for row in read_jsonl(output / "window_status.jsonl")}
    raw = {row["window_id"]: row for row in read_jsonl(output / "extract_raw.jsonl")}
    existing_units = {row["unit_id"] for row in read_jsonl(output / "source_unit_candidates.jsonl")}
    updates, new_units, new_children, child_statuses = {}, [], [], []
    for identity, status in statuses.items():
        if status["status"] != "inflight" or identity not in raw:
            continue
        saved = raw[identity]
        if saved["extraction_schema_version"] != EXTRACTION_SCHEMA_VERSION:
            raise ValueError(f"saved_extraction_contract_mismatch:{identity}")
        window = windows[identity]
        segments = segment_transport_batch(window["source_span"])
        result = ReactionExtractionResult.model_validate(saved["result"])
        units, failures = materialize_reaction_events(window, segments, result)
        if result.events and not units:
            raise ValueError(f"saved_extraction_has_no_valid_units:{identity}:{failures}")
        for unit in units:
            if unit["unit_id"] not in existing_units:
                unit.update(route_semantic_review(unit))
                new_units.append(unit)
                existing_units.add(unit["unit_id"])
        update = {"last_error": None, "raw_event_count": len(result.events),
                  "valid_unit_count": len(units), "ledger_restored_from_saved_result": True}
        if result.continuation_from_segment_id or result.coverage_status == "partial":
            children = child_windows_for_partial_coverage(window, segments,
                result.continuation_from_segment_id or "")
            update.update(status="awaiting_children", coverage_status="partial",
                          child_window_ids=[row["window_id"] for row in children])
            for child in children:
                if child["window_id"] not in windows:
                    new_children.append(child)
                    windows[child["window_id"]] = child
                if child["window_id"] not in statuses:
                    child_statuses.append({"window_id": child["window_id"], "status": "pending",
                        "attempts": 0, "last_error": None, "coverage_status": "not_started"})
        else:
            update.update(status="success" if result.events else "no_unit", coverage_status="complete")
            if not result.events:
                update["true_no_unit_reason"] = result.no_unit_reason
        updates[identity] = update
    append_jsonl(output / "source_unit_candidates.jsonl", new_units)
    append_jsonl(output / "windows.derived.jsonl", new_children)
    append_jsonl(output / "window_status.jsonl", child_statuses)
    replace_statuses(output, updates)
    reconcile_parent_statuses(output)
    write_progress(output, calls_used=0, tokens_used=0)
    report = {"restored_window_ids": sorted(updates), "new_children": len(new_children),
              "reconstructed_missing_units": len(new_units), "provider_calls": 0}
    append_jsonl(output / "ledger_restore_history.jsonl", [report])
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(restore(**vars(parser.parse_args())), ensure_ascii=False, indent=2))
