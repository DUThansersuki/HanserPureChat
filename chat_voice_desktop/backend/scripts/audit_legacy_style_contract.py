"""Reconstruct old exact spans into a migration draft, without new extraction.

The draft retains old extraction provenance and has no semantic approvals.
Window coverage and unit compatibility are reported separately; the active
resume ledger and all production databases remain untouched.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from scripts.prepare_style_reaction_resume import root_window
from scripts.run_style_reaction_pipeline_v2 import TERMINAL_STATUSES, all_windows, read_jsonl, write_jsonl
from scripts.style_reaction_pipeline import (
    ReactionEvent, ReactionExtractionResult, materialize_reaction_events,
    route_semantic_review, segment_transport_batch,
)


def remap_slices(slices: list[dict], window: dict, segments: list[dict]) -> list[dict]:
    refs = []
    for source in slices:
        left, right = int(source["window_start"]), int(source["window_end"])
        if not 0 <= left < right <= len(window["source_span"]):
            raise ValueError("legacy_slice_outside_window")
        if window["source_span"][left:right] != source["text"]:
            raise ValueError("legacy_slice_source_text_mismatch")
        for segment in segments:
            start, end = max(left, int(segment["start"])), min(right, int(segment["end"]))
            if start < end:
                refs.append({"segment_id": segment["segment_id"],
                             "start": start - int(segment["start"]), "end": end - int(segment["start"])})
    return refs


def migrate_unit(unit: dict, window: dict) -> dict:
    segments = segment_transport_batch(window["source_span"])
    event = ReactionEvent.model_validate({
        "reaction_spans": remap_slices(unit["reaction_source_slices"], window, segments),
        "trigger_spans": remap_slices(unit["trigger_source_slices"], window, segments),
        **{field: unit[field] for field in (
            "trigger_basis", "speaker_candidate", "attribution_status_initial", "interaction_frame",
            "interaction_target", "behavior_ids", "reaction_mechanism", "event_relation",
            "context_needed", "risk_flags", "confidence", "uncertainty",
        )},
    })
    rows, failures = materialize_reaction_events(window, segments,
        ReactionExtractionResult(events=[event], coverage_status="complete"))
    if failures:
        raise ValueError(failures[0]["error"])
    candidate = rows[0]
    if candidate["source_response_turn"] != unit["source_response_turn"]:
        raise ValueError("legacy_reaction_text_mismatch")
    if (candidate["reaction_source_start_char"], candidate["reaction_source_end_char"]) != (
        unit["reaction_source_start_char"], unit["reaction_source_end_char"],
    ):
        raise ValueError("legacy_reaction_absolute_span_mismatch")
    candidate.update(route_semantic_review(candidate))
    candidate["contract_migration"] = {
        "method": "exact_source_slices_to_current_mechanical_segments",
        "original_unit_id": unit["unit_id"],
        "original_extraction_schema_version": unit["extraction_schema_version"],
        "original_pipeline_version": unit["pipeline_version"],
        "new_provider_calls": 0, "semantic_review_reused": False,
    }
    return candidate


def audit(*, resume: Path, output: Path) -> dict:
    manifest = json.loads((resume / "resume_manifest.json").read_text(encoding="utf-8"))
    legacy = Path(manifest["legacy_run"])
    frozen = {row["window_id"]: row for row in read_jsonl(Path(manifest["frozen_run"]) / "windows.frozen.jsonl")}
    windows = all_windows(legacy)
    statuses = {row["window_id"]: row for row in read_jsonl(legacy / "window_status.jsonl")}
    migration_ids = {row["window_id"] for row in read_jsonl(resume / "legacy_contract_migration.jsonl")}
    candidates, unit_audits = [], []
    by_base = defaultdict(list)
    for unit in read_jsonl(legacy / "source_unit_candidates.jsonl"):
        base_id = root_window(unit["window_id"], windows)
        if base_id not in migration_ids:
            continue
        row = {"original_unit_id": unit["unit_id"], "window_id": unit["window_id"],
               "base_window_id": base_id, "original_schema": unit["extraction_schema_version"]}
        window, base = windows[unit["window_id"]], frozen[base_id]
        left = int(window["source_start_char"]) - int(base["source_start_char"])
        right = int(window["source_end_char"]) - int(base["source_start_char"])
        try:
            if window["source_id"] != base["source_id"] or not 0 <= left < right <= len(base["source_span"]):
                raise ValueError("legacy_window_outside_frozen_base")
            if window["source_span"] != base["source_span"][left:right]:
                raise ValueError("legacy_window_differs_from_frozen_source")
            candidate = migrate_unit(unit, window)
        except (ValueError, KeyError) as exc:
            row.update({"status": "requires_targeted_reextraction", "reason": str(exc)[:400]})
        else:
            candidates.append(candidate)
            row.update({"status": "compatible_migration_draft", "normalized_unit_id": candidate["unit_id"],
                        "id_preserved": candidate["unit_id"] == unit["unit_id"]})
        by_base[base_id].append(row)
        unit_audits.append(row)
    window_audits = []
    for base_id in sorted(migration_ids):
        descendants = [key for key in windows if root_window(key, windows) == base_id]
        unfinished = {key: statuses[key]["status"] for key in descendants
                      if statuses.get(key, {}).get("status") not in TERMINAL_STATUSES}
        rows = by_base[base_id]
        window_audits.append({"window_id": base_id, "legacy_status": statuses[base_id]["status"],
            "units": len(rows), "unit_compatibility": dict(Counter(row["status"] for row in rows)),
            "coverage_complete_in_legacy": not unfinished,
            "unfinished_window_statuses": unfinished,
            "next_action": "review_migration_draft_then_import_with_lineage" if not unfinished
                and all(row["status"] == "compatible_migration_draft" for row in rows)
                else "resolve_unit_or_child_coverage_before_import"})
    output.mkdir(parents=True, exist_ok=False)
    write_jsonl(output / "unit_compatibility.jsonl", unit_audits)
    write_jsonl(output / "window_compatibility.jsonl", window_audits)
    write_jsonl(output / "source_unit_migration_drafts.jsonl", candidates)
    report = {"legacy_run": str(legacy), "resume_run": str(resume), "base_windows_audited": len(window_audits),
        "raw_units_audited": len(unit_audits), "unit_compatibility": dict(Counter(row["status"] for row in unit_audits)),
        "legacy_coverage_complete_windows": sum(row["coverage_complete_in_legacy"] for row in window_audits),
        "coverage_unfinished_windows": sum(not row["coverage_complete_in_legacy"] for row in window_audits),
        "migration_drafts": len(candidates), "new_provider_calls": 0,
        "active_resume_imported": False, "semantic_review_reused": False, "production_modified": False}
    (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(audit(**vars(parser.parse_args())), ensure_ascii=False, indent=2))
