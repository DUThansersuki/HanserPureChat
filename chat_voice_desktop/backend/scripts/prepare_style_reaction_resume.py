"""Prepare a full-scope resumable ledger from frozen input and compatible runs.

No provider is created. Older work remains a migration backlog, rather than
being silently counted as v9-complete or automatically paid for a second time.
"""
from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from scripts.run_style_reaction_pipeline_v2 import (
    all_windows, initialize_full, read_jsonl, unique_units, write_jsonl,
    write_progress, write_review_progress,
)
from scripts.style_reaction_pipeline import (
    ATTRIBUTION_POLICY_VERSION, EXTRACTION_SCHEMA_VERSION, PIPELINE_VERSION,
    REVIEW_SCHEMA_VERSION, SpanRef, resolve_span_exact, segment_transport_batch,
)


def root_window(window_id: str, windows: dict) -> str:
    while windows[window_id].get("parent_window_id"):
        window_id = str(windows[window_id]["parent_window_id"])
    return window_id


def prepare(*, frozen: Path, compatible: Path, reviewed: Path, legacy: Path, output: Path, run_id: str) -> dict:
    frozen = frozen.resolve()
    compatible = compatible.resolve()
    reviewed = reviewed.resolve()
    legacy = legacy.resolve()
    output = output.resolve()
    base = {row["window_id"]: row for row in read_jsonl(frozen / "windows.frozen.jsonl")}
    windows = all_windows(compatible)
    statuses = {row["window_id"]: row for row in read_jsonl(compatible / "window_status.jsonl")}
    for window_id, window in windows.items():
        if window.get("parent_window_id") is None and window != base.get(window_id):
            raise ValueError(f"reused base window differs from frozen input: {window_id}")
        if statuses[window_id]["status"] not in {"success", "no_unit", "explicitly_excluded"}:
            raise ValueError(f"reuse requires a terminal window: {window_id}")
        if root_window(window_id, windows) not in base:
            raise ValueError(f"derived window belongs to another corpus: {window_id}")
        source = base[root_window(window_id, windows)]
        local_start = int(window["source_start_char"]) - int(source["source_start_char"])
        local_end = int(window["source_end_char"]) - int(source["source_start_char"])
        if window["source_id"] != source["source_id"] or window["source_span"] != source["source_span"][local_start:local_end]:
            raise ValueError(f"derived window differs from frozen source span: {window_id}")
    candidates = read_jsonl(compatible / "source_unit_candidates.jsonl")
    for unit in candidates:
        if (unit.get("pipeline_version"), unit.get("extraction_schema_version"), unit.get("attribution_policy_version")) != (
            PIPELINE_VERSION, EXTRACTION_SCHEMA_VERSION, ATTRIBUTION_POLICY_VERSION,
        ):
            raise ValueError(f"incompatible unit contract: {unit['unit_id']}")
        window = windows[unit["window_id"]]
        segments = {row["segment_id"]: row for row in segment_transport_batch(window["source_span"])}
        exact_reaction = "".join(resolve_span_exact(SpanRef.model_validate(span), segments)["text"] for span in unit["reaction_spans"])
        if exact_reaction != unit["source_response_turn"] or unit["source_id"] != window["source_id"]:
            raise ValueError(f"unit reaction does not match its source: {unit['unit_id']}")
    unique_ids = {row["unit_id"] for row in unique_units(compatible)}
    reviews = read_jsonl(reviewed / "semantic_reviews.jsonl")
    for review in reviews:
        if review["unit_id"] not in unique_ids or review.get("review_schema_version") != REVIEW_SCHEMA_VERSION:
            raise ValueError(f"review cannot be reused: {review['unit_id']}")

    old_windows = all_windows(legacy)
    old_statuses = read_jsonl(legacy / "window_status.jsonl")
    legacy_candidates = read_jsonl(legacy / "source_unit_candidates.jsonl")
    touched = {
        root_window(row["window_id"], old_windows)
        for row in old_statuses if row["status"] != "pending"
    }
    touched.update(root_window(row["window_id"], old_windows) for row in legacy_candidates)
    reused_base = {window_id for window_id in windows if window_id in base}
    migration_ids = touched - reused_base
    backlog = [{
        "window_id": window_id,
        "status": "awaiting_contract_migration",
        "legacy_run": str(legacy),
        "action": "inspect exact spans/trigger contract and child coverage before migrating or re-extracting",
    } for window_id in sorted(migration_ids)]

    initialize_full(SimpleNamespace(parent=frozen, output=output, run_id=run_id))
    shutil.copyfile(compatible / "windows.derived.jsonl", output / "windows.derived.jsonl")
    updated = []
    for row in read_jsonl(output / "window_status.jsonl"):
        window_id = row["window_id"]
        if window_id in reused_base:
            updated.append({**statuses[window_id], "reused_from": str(compatible)})
        elif window_id in migration_ids:
            updated.append({**row, "status": "awaiting_contract_migration", "coverage_status": "legacy_work_preserved"})
        else:
            updated.append(row)
    updated.extend({**statuses[window_id], "reused_from": str(compatible)} for window_id in windows if window_id not in base)
    write_jsonl(output / "window_status.jsonl", updated)
    write_jsonl(output / "source_unit_candidates.jsonl", candidates)
    write_jsonl(output / "legacy_contract_migration.jsonl", backlog)
    # Preserve originals and call evidence; copied costs are inherited, not new calls.
    for origin, filenames in [
        (compatible, ["extract_call_records.jsonl", "extract_raw.jsonl"]),
        (reviewed, ["semantic_reviews.jsonl", "review_call_records.jsonl", "review_evidence_packets.jsonl", "review_unit_status.jsonl"]),
        (frozen, ["split_manifest.json", "window_manifest.json"]),
    ]:
        for filename in filenames:
            shutil.copyfile(origin / filename, output / filename)
    legacy_inflight = [row["window_id"] for row in old_statuses if row["status"] == "inflight"]
    manifest = {
        "canonical_backend": "chat_voice_desktop/backend",
        "run_id": run_id,
        "frozen_run": str(frozen), "compatible_run": str(compatible),
        "reviewed_run": str(reviewed), "legacy_run": str(legacy),
        "pipeline_version": PIPELINE_VERSION,
        "extraction_schema_version": EXTRACTION_SCHEMA_VERSION,
        "review_schema_version": REVIEW_SCHEMA_VERSION,
        "attribution_policy_version": ATTRIBUTION_POLICY_VERSION,
        "basic_windows": len(base), "reused_basic_windows": len(reused_base),
        "reused_derived_windows": len(windows) - len(reused_base),
        "legacy_basic_windows_awaiting_migration": len(migration_ids),
        "untouched_basic_windows_pending": sum(row["status"] == "pending" for row in updated),
        "raw_units_reused": len(candidates), "unique_units_reused": len(unique_ids),
        "reviewed_units_reused": len({row["unit_id"] for row in reviews}),
        "legacy_extraction_contracts": dict(Counter(row["extraction_schema_version"] for row in legacy_candidates)),
        "legacy_inflight_not_automatically_retried": legacy_inflight,
        "legacy_awaiting_children": [row["window_id"] for row in old_statuses if row["status"] == "awaiting_children"],
        "provider_calls_this_preparation": 0, "production_pointer_modified": False,
        "source_split_modified": False,
    }
    (output / "resume_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_progress(output, calls_used=0, tokens_used=0)
    write_review_progress(output, unique_units(output))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["frozen", "compatible", "reviewed", "legacy", "output"]:
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(**vars(args)), ensure_ascii=False, indent=2))
