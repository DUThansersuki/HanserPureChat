"""Read-only source, coverage, review identity and payload audit of a fixed cohort."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.prepare_style_reaction_resume import root_window
from scripts.run_style_reaction_pipeline_v2 import (
    TERMINAL_STATUSES, all_windows, fragment_within_reaction, read_jsonl, unique_units, write_jsonl,
)
from scripts.style_reaction_pipeline import SpanRef, resolve_span_exact, segment_transport_batch


def audit(resume: Path, cohort: Path, output: Path) -> dict:
    selection = json.loads((cohort / "selection.json").read_text(encoding="utf-8"))
    roots = set(selection["window_ids"])
    manifest = json.loads((resume / "resume_manifest.json").read_text(encoding="utf-8"))
    frozen = Path(manifest["frozen_run"])
    sources = {row["source_id"]: row for row in read_jsonl(frozen / "sources.frozen.jsonl")}
    base = {row["window_id"]: row for row in read_jsonl(frozen / "windows.frozen.jsonl")}
    windows = all_windows(resume)
    root_ids = {identity: root_window(identity, windows) for identity in windows}
    statuses = {row["window_id"]: row for row in read_jsonl(resume / "window_status.jsonl")}
    units = unique_units(resume)
    by_id = {row["unit_id"]: row for row in units}
    reviews = {row["unit_id"]: row for row in read_jsonl(resume / "semantic_reviews.jsonl")}
    projections = {row["unit_id"]: row for row in read_jsonl(resume / "source_reaction_units.reviewed.jsonl")}
    errors = []
    for identity, window in windows.items():
        source = base[root_ids[identity]]
        start = window["source_start_char"] - source["source_start_char"]
        end = window["source_end_char"] - source["source_start_char"]
        if (not 0 <= start < end <= len(source["source_span"])
                or window["source_span"] != source["source_span"][start:end]
                or window["source_id"] != source["source_id"]):
            errors.append({"kind": "window_source", "window_id": identity})
        if not window.get("parent_window_id") and window != source:
            errors.append({"kind": "frozen_base_changed", "window_id": identity})
    segments = {}
    checked_raw = read_jsonl(resume / "source_unit_candidates.jsonl")
    for unit in checked_raw:
        identity = unit["window_id"]
        if identity not in segments:
            segments[identity] = {row["segment_id"]: row for row in segment_transport_batch(windows[identity]["source_span"])}
        reaction = [resolve_span_exact(SpanRef.model_validate(span), segments[identity]) for span in unit["reaction_spans"]]
        trigger = [resolve_span_exact(SpanRef.model_validate(span), segments[identity]) for span in unit["trigger_spans"]]
        if ("".join(row["text"] for row in reaction) != unit["source_response_turn"]
                or unit["reaction_source_start_char"] != windows[identity]["source_start_char"] + min(row["window_start"] for row in reaction)
                or unit["reaction_source_end_char"] != windows[identity]["source_start_char"] + max(row["window_end"] for row in reaction)):
            errors.append({"kind": "reaction_source", "unit_id": unit["unit_id"]})
        if sources[unit["source_id"]]["split"] != "corpus_build":
            errors.append({"kind": "holdout_or_excluded_source", "unit_id": unit["unit_id"]})
        if unit.get("source_user_turn") and unit["source_user_turn"] != "".join(row["text"] for row in trigger):
            errors.append({"kind": "invented_q", "unit_id": unit["unit_id"]})
    for prior in read_jsonl(cohort / "baseline_reviewed_units.jsonl"):
        current = by_id.get(prior["unit_id"])
        if current is None or any(current.get(field) != prior.get(field) for field in (
            "source_id", "source_response_turn", "reaction_source_start_char", "reaction_source_end_char", "reaction_spans")):
            errors.append({"kind": "changed_review_identity", "unit_id": prior["unit_id"]})
    for identity in reviews.keys() - by_id.keys():
        errors.append({"kind": "orphan_review", "unit_id": identity})
    eligible = [row for row in projections.values() if row.get("runtime_eligible")]
    for unit in eligible:
        review = unit.get("semantic_review") or {}
        if (review.get("decision") != "approved" or review.get("attribution_status") != "verified"
                or review.get("transfer_scope") != "general" or not unit.get("a_projection")):
            errors.append({"kind": "runtime_eligibility", "unit_id": unit["unit_id"]})
        if review.get("payload_class") not in {"reaction_only", "turn_local_stance"}:
            errors.append({"kind": "payload_facts_gate", "unit_id": unit["unit_id"]})
        if unit["runtime_payload_kind"] == "reviewed_fragment" and (
                review.get("source_specific_dependency") or not fragment_within_reaction(unit, review)):
            errors.append({"kind": "fragment_boundary", "unit_id": unit["unit_id"]})
    cohort_units = [row for row in units if root_ids[row["window_id"]] in roots]
    by_root = defaultdict(list)
    for unit in cohort_units:
        by_root[root_ids[unit["window_id"]]].append(unit)
    raw_cohort = [row for row in checked_raw if root_ids[row["window_id"]] in roots]
    failures = [row for row in read_jsonl(resume / "pipeline_failures.jsonl") if root_ids.get(row.get("window_id")) in roots]
    coverage = [{"window_id": identity, "source_id": windows[identity]["source_id"],
        "base_status": statuses[identity]["status"], "base_chars": len(windows[identity]["source_span"]),
        "canonical_units": len(by_root[identity]),
        "unfinished_descendants": {key: statuses.get(key, {}).get("status") for key in windows
            if root_ids[key] == identity and statuses.get(key, {}).get("status") not in TERMINAL_STATUSES}}
        for identity in selection["window_ids"]]
    sample_decisions = Counter(reviews.get(row["unit_id"], {}).get("decision", "unreviewed") for row in cohort_units)
    summary = {"cohort_base_windows": len(roots), "cohort_base_statuses": dict(Counter(row["base_status"] for row in coverage)),
        "cohort_complete_families": sum(not row["unfinished_descendants"] for row in coverage),
        "cohort_distinct_sources": len({row["source_id"] for row in cohort_units}),
        "cohort_raw_units": len(raw_cohort), "cohort_canonical_units": len(cohort_units),
        "cohort_review_decisions": dict(sample_decisions),
        "cohort_risk_flags": dict(Counter(flag for row in cohort_units for flag in row.get("risk_flags", []))),
        "cohort_behaviors": dict(Counter(value for row in cohort_units for value in row.get("behavior_ids", []))),
        "cohort_attribution_basis": dict(Counter(row.get("attribution_basis", "unknown") for row in cohort_units)),
        "cohort_explicit_q": sum(bool(row.get("source_user_turn")) for row in cohort_units),
        "cohort_pipeline_failure_attempts": len(failures), "cohort_pipeline_failure_kinds": dict(Counter(row.get("error_type", row.get("stage", "unknown")) for row in failures)),
        "cumulative_units": len(units), "cumulative_reviewed": len(reviews), "cumulative_eligible": len(eligible),
        "hard_gate_errors": errors, "raw_units_checked": len(checked_raw), "windows_checked": len(windows),
        "source_split_changed": False, "production_modified": False, "hash_recomputed": False,
        "scope": "engineering and source contracts; semantic accuracy and chat benefit require separate checks"}
    output.mkdir(parents=True, exist_ok=True)
    write_jsonl(output / "window_coverage.jsonl", coverage)
    write_jsonl(output / "cohort_units.jsonl", cohort_units)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("resume", "cohort", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    print(json.dumps(audit(**vars(parser.parse_args())), ensure_ascii=False, indent=2))
