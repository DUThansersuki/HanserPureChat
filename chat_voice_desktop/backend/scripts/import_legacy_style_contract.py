"""Stage and import exact-source migration drafts for terminal legacy families.

Old semantic approvals are never imported. Existing current-contract verdicts
must still address the same canonical ID and reaction text after deduplication.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.prepare_style_reaction_resume import root_window
from scripts.run_style_reaction_pipeline_v2 import (
    TERMINAL_STATUSES, all_windows, read_jsonl, unique_units, write_jsonl,
    write_progress, write_review_progress,
)
from scripts.style_reaction_pipeline import (
    EXTRACTION_SCHEMA_VERSION, PIPELINE_VERSION, SpanRef, child_windows_for_partial_coverage, resolve_span_exact,
    segment_transport_batch,
)


def verify_review_identity(before: list[dict], after: list[dict], reviews: list[dict]) -> None:
    prior = {row["unit_id"]: row for row in before}
    current = {row["unit_id"]: row for row in after}
    for identity in {row["unit_id"] for row in reviews} & prior.keys():
        if identity not in current:
            raise ValueError(f"migration_orphans_current_review:{identity}")
        for field in ("source_id", "source_response_turn", "reaction_source_start_char",
                      "reaction_source_end_char"):
            if prior[identity][field] != current[identity][field]:
                raise ValueError(f"migration_changes_reviewed_evidence:{identity}:{field}")


def recovery_children(window: dict) -> list[dict]:
    segments = segment_transport_batch(window["source_span"])
    children = child_windows_for_partial_coverage(
        window, segments, segments[0]["segment_id"], max_chars=1000)
    for child in children:
        child["derivation_reason"] = "legacy_inflight_exact_source_recovery"
    return children


def stage_import(resume: Path, audit: Path, output: Path, include_incomplete: bool = False) -> dict:
    manifest = json.loads((resume / "resume_manifest.json").read_text(encoding="utf-8"))
    legacy = Path(manifest["legacy_run"])
    old_windows = all_windows(legacy)
    current_windows = all_windows(resume)
    current_statuses = {row["window_id"]: row for row in read_jsonl(resume / "window_status.jsonl")}
    selected = {row["window_id"] for row in read_jsonl(audit / "window_compatibility.jsonl")
                 if (row["coverage_complete_in_legacy"] or include_incomplete)
                 and current_statuses[row["window_id"]]["status"] == "awaiting_contract_migration"}
    old_statuses = {row["window_id"]: row for row in read_jsonl(legacy / "window_status.jsonl")}
    imported_windows = {identity: row for identity, row in old_windows.items()
                        if root_window(identity, old_windows) in selected}
    recovered = {}
    for identity, window in imported_windows.items():
        if old_statuses[identity]["status"] not in TERMINAL_STATUSES and not include_incomplete:
            raise ValueError(f"legacy_coverage_not_terminal:{identity}")
        if identity in current_windows and window != current_windows[identity]:
            raise ValueError(f"migration_window_collision:{identity}")
        base = current_windows[root_window(identity, old_windows)]
        start = int(window["source_start_char"]) - int(base["source_start_char"])
        end = int(window["source_end_char"]) - int(base["source_start_char"])
        if window["source_id"] != base["source_id"] or window["source_span"] != base["source_span"][start:end]:
            raise ValueError(f"migration_source_differs_from_frozen:{identity}")
        if old_statuses[identity]["status"] == "inflight":
            if any(row.get("window_id") == identity for filename in (
                "extract_raw.jsonl", "extract_call_records.jsonl") for row in read_jsonl(legacy / filename)):
                raise ValueError(f"legacy_inflight_has_result_or_call_evidence:{identity}")
            recovered[identity] = recovery_children(window)
    drafts = [row for row in read_jsonl(audit / "source_unit_migration_drafts.jsonl")
              if root_window(row["window_id"], old_windows) in selected]
    for row in drafts:
        window = imported_windows[row["window_id"]]
        segments = {item["segment_id"]: item for item in segment_transport_batch(window["source_span"])}
        reaction = "".join(resolve_span_exact(SpanRef.model_validate(span), segments)["text"]
                           for span in row["reaction_spans"])
        if reaction != row["source_response_turn"] or row["source_id"] != window["source_id"]:
            raise ValueError(f"migration_unit_source_mismatch:{row['unit_id']}")
        if row["pipeline_version"] != PIPELINE_VERSION or row["extraction_schema_version"] != EXTRACTION_SCHEMA_VERSION:
            raise ValueError(f"migration_draft_contract_mismatch:{row['unit_id']}")
        if row["runtime_eligible"] or row["contract_migration"]["semantic_review_reused"]:
            raise ValueError(f"migration_draft_reuses_approval:{row['unit_id']}")
    output.mkdir(parents=True, exist_ok=False)
    originals = read_jsonl(resume / "source_unit_candidates.jsonl")
    write_jsonl(output / "source_unit_candidates.jsonl", [*originals, *drafts])
    before = unique_units(resume)
    after = unique_units(output)
    verify_review_identity(before, after, read_jsonl(resume / "semantic_reviews.jsonl"))
    derived = read_jsonl(resume / "windows.derived.jsonl")
    derived.extend(row for identity, row in imported_windows.items() if identity not in current_windows)
    derived.extend(child for children in recovered.values() for child in children)
    write_jsonl(output / "windows.derived.jsonl", derived)
    for identity in imported_windows:
        current_statuses[identity] = {**old_statuses[identity], "reused_from": str(legacy),
            "contract_migration_audit": str(audit), "migration_stage": str(output),
            "legacy_status": old_statuses[identity]["status"],
            "coverage_status": ("legacy_complete_exact_source_migrated"
                if old_statuses[identity]["status"] in TERMINAL_STATUSES else "legacy_unfinished_exact_source_migrated")}
    for identity, children in recovered.items():
        current_statuses[identity].update(status="awaiting_children",
            coverage_status="legacy_inflight_recovery_children_pending",
            child_window_ids=[row["window_id"] for row in children])
        for child in children:
            current_statuses[child["window_id"]] = {"window_id": child["window_id"],
                "status": "pending", "attempts": 0, "coverage_status": "not_started",
                "last_error": None, "migration_stage": str(output)}
    write_jsonl(output / "window_status.jsonl", list(current_statuses.values()))
    backlog = read_jsonl(resume / "legacy_contract_migration.jsonl")
    for row in backlog:
        if row["window_id"] in selected:
            row.update(status="imported_current_contract", migration_stage=str(output),
                       action="review_current_contract_units_before_runtime_projection")
    write_jsonl(output / "legacy_contract_migration.jsonl", backlog)
    aliases = {identity: row["unit_id"] for row in after
               for identity in [row["unit_id"], *row.get("dedupe_merged_unit_ids", [])]}
    write_jsonl(output / "imported_unit_lineage.jsonl", [
        {"input_unit_id": row["unit_id"], "canonical_unit_id": aliases[row["unit_id"]],
         "window_id": row["window_id"], "contract_migration": row["contract_migration"]}
        for row in drafts])
    report = {"resume": str(resume), "audit": str(audit), "legacy": str(legacy),
        "imported_base_windows": sorted(selected),
        "imported_derived_windows": len(imported_windows) - len(selected),
        "inflight_recovered_from_frozen_source": sorted(recovered),
        "new_recovery_children": sum(len(rows) for rows in recovered.values()),
        "imported_raw_units": len(drafts), "unique_before": len(before), "unique_after": len(after),
        "reviewed_identity_preserved": True, "old_semantic_approvals_imported": 0,
        "new_provider_calls": 0, "source_split_modified": False,
        "production_modified": False, "applied": False}
    (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def apply_import(output: Path) -> dict:
    report = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    resume = Path(report["resume"])
    for filename in ("source_unit_candidates.jsonl", "windows.derived.jsonl",
                     "window_status.jsonl", "legacy_contract_migration.jsonl"):
        pending = resume / (filename + ".migration.tmp")
        pending.write_bytes((output / filename).read_bytes())
        pending.replace(resume / filename)
    report["applied"] = True
    (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_progress(resume, calls_used=0, tokens_used=0)
    write_review_progress(resume, unique_units(resume))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("resume", "audit", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--include-incomplete", action="store_true")
    args = parser.parse_args()
    report = stage_import(args.resume, args.audit, args.output, args.include_incomplete)
    if args.apply:
        report = apply_import(args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
