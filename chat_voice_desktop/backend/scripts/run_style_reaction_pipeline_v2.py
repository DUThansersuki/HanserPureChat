from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import shutil
import sys
import time
from collections import Counter
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from hanser_agent.config import load_settings
from hanser_agent.model_gateway import ModelGateway
from hanser_agent.models import ChatMessage
from scripts.style_reaction_pipeline import (
    ATTRIBUTION_POLICY_VERSION,
    EXTRACTION_SCHEMA_VERSION,
    PIPELINE_VERSION,
    REACTION_EVENT_LIMIT,
    REVIEW_SCHEMA_VERSION,
    ReactionExtractionResult,
    ReactionExtractionWireResult,
    SpanRef,
    TargetedReviewResult,
    apply_source_prior_attribution_policy,
    child_windows_for_partial_coverage,
    extraction_system_prompt,
    materialize_reaction_events,
    minimal_evidence_packet,
    provider_segment_id_map,
    render_segmented_batch,
    resolve_span_exact,
    route_semantic_review,
    segment_transport_batch,
    targeted_review_system_prompt,
)


TERMINAL_STATUSES = {"success", "no_unit", "explicitly_excluded"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    content = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    pending = path.with_name(path.name + ".tmp")
    pending.write_text(content, encoding="utf-8", newline="\n")
    pending.replace(path)


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def gateway_for(max_tokens: int) -> ModelGateway:
    settings = load_settings(PROJECT_ROOT / "backend" / "config.yml")
    return ModelGateway({
        "planner": replace(settings.planner, max_tokens=max_tokens, temperature=0.0)
    })


def _window_categories(window: dict[str, Any], saturated_ids: set[str]) -> set[str]:
    text = str(window["source_span"])
    lines = [line for line in text.splitlines() if line.strip()]
    categories: set[str] = set()
    if window["window_id"] in saturated_ids or len(lines) >= 35:
        categories.add("reaction_high_density")
    if len(re.findall(r"(?:弹幕|SC|SuperChat)\s*[:：]", text, re.I)) >= 2:
        categories.add("multi_bullet_interaction")
    if not re.search(r"(?:弹幕|SC|SuperChat)\s*[:：]", text, re.I):
        categories.add("no_recorded_q")
    if re.search(r"(?:投稿|From|邮件|来信)\s*[:：]", text, re.I):
        categories.add("submission_or_mail")
    if len(set(re.findall(
        r"(?m)^\s*(憨憨|憨色|Hanser|憨|茶|海|七海|阿萨|嘉宾|主持|旁白)\s*[:：]",
        text, re.I,
    ))) >= 2:
        categories.add("speaker_switch")
    if re.search(r"(?:引用|转述|[“”\"'])", text):
        categories.add("quote")
    if re.search(r"(?:歌词\s*[:：]|[♪♫])", text):
        categories.add("lyrics")
    if "<!--" in text:
        categories.add("editor_meta_comment")
    if sum(1 for line in lines if len(line) <= 20) >= 8:
        categories.add("many_short_reactions")
    if len(lines) <= 3 and not re.search(r"[:：？！!?]", text):
        categories.add("likely_true_no_unit")
    return categories


def select_replay_windows(parent: Path, limit: int) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    windows = read_jsonl(parent / "windows.frozen.jsonl")
    raw = read_jsonl(parent / "extract_raw.jsonl")
    saturated_ids = {
        str(row["window_id"]) for row in raw
        if len((row.get("result") or {}).get("units", [])) >= 2
    }
    legacy_empty_ids = {
        str(row["window_id"]) for row in raw
        if not (row.get("result") or {}).get("units")
    }
    by_category: dict[str, list[dict[str, Any]]] = {}
    categories_by_id: dict[str, list[str]] = {}
    for window in windows:
        categories = sorted(_window_categories(window, saturated_ids))
        if str(window["window_id"]) in legacy_empty_ids:
            categories.append("legacy_no_unit_recheck")
        categories_by_id[str(window["window_id"])] = categories
        for category in categories:
            by_category.setdefault(category, []).append(window)
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    preferred = [
        "reaction_high_density", "multi_bullet_interaction", "no_recorded_q",
        "submission_or_mail", "speaker_switch", "quote", "lyrics",
        "editor_meta_comment", "many_short_reactions", "likely_true_no_unit",
        "legacy_no_unit_recheck",
    ]
    for category in preferred:
        candidates = sorted(
            by_category.get(category, []),
            key=lambda row: hashlib.sha256(str(row["window_id"]).encode()).hexdigest(),
        )
        candidate = next(
            (row for row in candidates if str(row["window_id"]) not in selected_ids),
            None,
        )
        if candidate is not None:
            selected.append(candidate)
            selected_ids.add(str(candidate["window_id"]))
    remaining = sorted(
        windows,
        key=lambda row: (
            str(row["window_id"]) not in saturated_ids,
            hashlib.sha256(str(row["window_id"]).encode()).hexdigest(),
        ),
    )
    for window in remaining:
        if len(selected) >= limit:
            break
        if str(window["window_id"]) not in selected_ids:
            selected.append(window)
            selected_ids.add(str(window["window_id"]))
    return selected[:limit], {
        window_id: categories_by_id.get(window_id, [])
        for window_id in selected_ids
    }


def initialize(args: argparse.Namespace) -> None:
    parent = args.parent.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    selected, categories = select_replay_windows(parent, args.replay_windows)
    write_jsonl(output / "windows.replay.jsonl", selected)
    write_jsonl(output / "windows.derived.jsonl", [])
    write_jsonl(output / "window_status.jsonl", [{
        "window_id": row["window_id"], "status": "pending", "attempts": 0,
        "last_error": None, "coverage_status": "not_started",
    } for row in selected])
    manifest = {
        "run_id": args.run_id,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "parent_run": str(parent),
        "pipeline_version": PIPELINE_VERSION,
        "extraction_schema_version": EXTRACTION_SCHEMA_VERSION,
        "review_schema_version": REVIEW_SCHEMA_VERSION,
        "attribution_policy_version": ATTRIBUTION_POLICY_VERSION,
        "mode": "pipeline_replay",
        "frozen_basic_windows_modified": False,
        "selected_windows": len(selected),
        "categories": categories,
        "production_pointer_modified": False,
    }
    (output / "replay_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "extraction_prompt.fixed.txt").write_text(
        extraction_system_prompt(), encoding="utf-8"
    )
    (output / "targeted_review_prompt.fixed.txt").write_text(
        targeted_review_system_prompt(), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


def initialize_full(args: argparse.Namespace) -> None:
    parent = args.parent.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    windows = read_jsonl(parent / "windows.frozen.jsonl")
    write_jsonl(output / "windows.full.jsonl", windows)
    write_jsonl(output / "windows.derived.jsonl", [])
    write_jsonl(output / "window_status.jsonl", [{
        "window_id": row["window_id"], "status": "pending", "attempts": 0,
        "last_error": None, "coverage_status": "not_started",
    } for row in windows])
    manifest = {
        "run_id": args.run_id,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "parent_run": str(parent),
        "pipeline_version": PIPELINE_VERSION,
        "extraction_schema_version": EXTRACTION_SCHEMA_VERSION,
        "review_schema_version": REVIEW_SCHEMA_VERSION,
        "attribution_policy_version": ATTRIBUTION_POLICY_VERSION,
        "mode": "full_reaction_source_rebuild",
        "frozen_basic_windows_modified": False,
        "basic_windows": len(windows),
        "source_split_modified": False,
        "production_pointer_modified": False,
        "automatic_retry_of_inflight_calls": False,
    }
    (output / "full_run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "extraction_prompt.fixed.txt").write_text(
        extraction_system_prompt(), encoding="utf-8"
    )
    (output / "targeted_review_prompt.fixed.txt").write_text(
        targeted_review_system_prompt(), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


def initialize_selected(args: argparse.Namespace) -> None:
    parent = args.parent.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = all_windows(parent)
    selected = []
    for window_id in args.window_ids:
        window = source.get(str(window_id))
        if window is None:
            raise ValueError(f"window_id_not_found_in_parent:{window_id}")
        selected.append(window)
    write_jsonl(output / "windows.replay.jsonl", selected)
    write_jsonl(output / "windows.derived.jsonl", [])
    write_jsonl(output / "window_status.jsonl", [{
        "window_id": row["window_id"], "status": "pending", "attempts": 0,
        "last_error": None, "coverage_status": "not_started",
    } for row in selected])
    manifest = {
        "run_id": args.run_id,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "parent_run": str(parent),
        "pipeline_version": PIPELINE_VERSION,
        "extraction_schema_version": EXTRACTION_SCHEMA_VERSION,
        "review_schema_version": REVIEW_SCHEMA_VERSION,
        "mode": "selected_full_pipeline_canary",
        "selected_window_ids": [row["window_id"] for row in selected],
        "source_split_modified": False,
        "production_pointer_modified": False,
    }
    (output / "selected_run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / f"extraction_prompt.{EXTRACTION_SCHEMA_VERSION}.fixed.txt").write_text(
        extraction_system_prompt(), encoding="utf-8"
    )
    (output / "targeted_review_prompt.fixed.txt").write_text(
        targeted_review_system_prompt(), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


def extend_replay(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    parent = args.parent.resolve()
    existing = all_windows(output)
    source = {
        str(row["window_id"]): row
        for row in read_jsonl(parent / "windows.frozen.jsonl")
    }
    window = source.get(str(args.window_id))
    if window is None:
        raise ValueError("window_id_not_found_in_parent_frozen_windows")
    if str(args.window_id) in existing:
        raise ValueError("window_already_in_replay")
    append_jsonl(output / "windows.replay.jsonl", [window])
    append_jsonl(output / "window_status.jsonl", [{
        "window_id": window["window_id"], "status": "pending", "attempts": 0,
        "last_error": None, "coverage_status": "not_started",
    }])
    manifest_path = output / "replay_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["selected_windows"] = int(manifest["selected_windows"]) + 1
    manifest.setdefault("categories", {})[str(window["window_id"])] = sorted(
        _window_categories(window, set()) | {"true_no_unit_probe"}
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "added_window_id": window["window_id"],
        "selected_windows": manifest["selected_windows"],
    }, ensure_ascii=False, indent=2))


def recover_failed_windows(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    windows = all_windows(output)
    statuses = read_jsonl(output / "window_status.jsonl")
    failed = [row for row in statuses if row["status"] == "failed"]
    child_rows: list[dict[str, Any]] = []
    updates: dict[str, dict[str, Any]] = {}
    existing = set(windows)
    for status_row in failed:
        window = windows[str(status_row["window_id"])]
        segments = segment_transport_batch(str(window["source_span"]))
        children = child_windows_for_partial_coverage(
            window, segments, str(segments[0]["segment_id"]), max_chars=args.recovery_child_chars,
        )
        children = [row for row in children if str(row["window_id"]) not in existing]
        for child in children:
            child["derivation_reason"] = "failed_root_mechanical_recovery"
            existing.add(str(child["window_id"]))
        child_rows.extend(children)
        updates[str(window["window_id"])] = {
            "status": "awaiting_children" if children else "failed",
            "coverage_status": "recovery_children_pending" if children else "recovery_failed",
            "child_window_ids": [row["window_id"] for row in children],
        }
    if child_rows:
        append_jsonl(output / "windows.derived.jsonl", child_rows)
        append_jsonl(output / "window_status.jsonl", [{
            "window_id": row["window_id"], "status": "pending", "attempts": 0,
            "last_error": None, "coverage_status": "not_started",
        } for row in child_rows])
    replace_statuses(output, updates)
    append_jsonl(output / "pipeline_revision_history.jsonl", [{
        "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "pipeline_version": PIPELINE_VERSION,
        "extraction_schema_version": EXTRACTION_SCHEMA_VERSION,
        "reason": "provider_schema_tolerance_and_failed_root_child_recovery",
        "failed_roots_recovered": len(failed),
        "recovery_children": len(child_rows),
        "successful_prior_calls_repeated": 0,
    }])
    (output / f"extraction_prompt.{EXTRACTION_SCHEMA_VERSION}.fixed.txt").write_text(
        extraction_system_prompt(), encoding="utf-8"
    )
    print(json.dumps({
        "failed_roots_recovered": len(failed),
        "recovery_children": len(child_rows),
        "pipeline_version": PIPELINE_VERSION,
        "extraction_schema_version": EXTRACTION_SCHEMA_VERSION,
    }, ensure_ascii=False, indent=2))


def record_pipeline_revision(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    append_jsonl(output / "pipeline_revision_history.jsonl", [{
        "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "pipeline_version": PIPELINE_VERSION,
        "extraction_schema_version": EXTRACTION_SCHEMA_VERSION,
        "review_schema_version": REVIEW_SCHEMA_VERSION,
        "attribution_policy_version": ATTRIBUTION_POLICY_VERSION,
        "reason": args.revision_reason,
        "successful_prior_calls_repeated": 0,
    }])
    prompt_path = output / f"extraction_prompt.{EXTRACTION_SCHEMA_VERSION}.fixed.txt"
    prompt_path.write_text(extraction_system_prompt(), encoding="utf-8")
    review_prompt_path = output / f"review_prompt.{REVIEW_SCHEMA_VERSION}.fixed.txt"
    review_prompt_path.write_text(targeted_review_system_prompt(), encoding="utf-8")
    print(json.dumps({
        "pipeline_version": PIPELINE_VERSION,
        "extraction_schema_version": EXTRACTION_SCHEMA_VERSION,
        "review_schema_version": REVIEW_SCHEMA_VERSION,
        "attribution_policy_version": ATTRIBUTION_POLICY_VERSION,
        "reason": args.revision_reason,
        "prompt_path": str(prompt_path),
        "review_prompt_path": str(review_prompt_path),
    }, ensure_ascii=False, indent=2))


def render_model_input(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    windows = all_windows(output)
    window = windows.get(str(args.window_id))
    if window is None:
        raise ValueError("window_id_not_found_in_run")
    segments = segment_transport_batch(str(window["source_span"]))
    messages = [
        ChatMessage(role="system", content=extraction_system_prompt()),
        ChatMessage(role="user", content=render_segmented_batch(window, segments)),
    ]
    settings = load_settings(PROJECT_ROOT / "backend" / "config.yml")
    profile = replace(settings.planner, max_tokens=args.model_max_tokens, temperature=0.0)
    payload: dict[str, Any] = {
        "model": profile.model,
        "messages": [message.model_dump() for message in messages],
        "temperature": profile.temperature,
        "top_p": profile.top_p,
        "max_tokens": profile.max_tokens,
        "response_format": {"type": "json_object"},
    }
    ModelGateway._apply_provider_capabilities(profile, payload)
    matching_calls = [
        row for row in read_jsonl(output / "extract_call_records.jsonl")
        if str(row.get("window_id")) == str(args.window_id)
    ]
    local_artifact = {
        "artifact_kind": "simulated_exact_provider_input_without_credentials",
        "pipeline_version": PIPELINE_VERSION,
        "extraction_schema_version": EXTRACTION_SCHEMA_VERSION,
        "window_id": window["window_id"],
        "source_id": window["source_id"],
        "source_chars": len(str(window["source_span"])),
        "segment_count": len(segments),
        "provider_segment_count": len(provider_segment_id_map(segments)),
        "wire_to_canonical_segment_id": provider_segment_id_map(segments),
        "message_chars": {
            "system": len(messages[0].content),
            "user": len(messages[1].content),
            "total": sum(len(message.content) for message in messages),
        },
        "actual_call_records_for_window": matching_calls,
        "omitted_sensitive_transport_fields": ["endpoint", "Authorization/api_key"],
        "provider_request_path": None,
    }
    sample_dir = output / "input_samples"
    sample_dir.mkdir(exist_ok=True)
    sample_path = sample_dir / (
        f"{args.window_id}.{EXTRACTION_SCHEMA_VERSION}.request.json"
    )
    local_path = sample_dir / (
        f"{args.window_id}.{EXTRACTION_SCHEMA_VERSION}.local-normalization.json"
    )
    local_artifact["provider_request_path"] = str(sample_path)
    sample_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    local_path.write_text(
        json.dumps(local_artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "sample_path": str(sample_path),
        "local_normalization_path": str(local_path),
        "window_id": window["window_id"],
        "source_chars": local_artifact["source_chars"],
        "segment_count": local_artifact["segment_count"],
        "message_chars": local_artifact["message_chars"],
        "actual_call_records": len(matching_calls),
    }, ensure_ascii=False, indent=2))


def all_windows(output: Path) -> dict[str, dict[str, Any]]:
    rows = [
        *read_jsonl(output / "windows.replay.jsonl"),
        *read_jsonl(output / "windows.full.jsonl"),
        *read_jsonl(output / "windows.derived.jsonl"),
    ]
    return {str(row["window_id"]): row for row in rows}


def replace_statuses(output: Path, updates: dict[str, dict[str, Any]]) -> None:
    rows = read_jsonl(output / "window_status.jsonl")
    for row in rows:
        if str(row["window_id"]) in updates:
            row.update(updates[str(row["window_id"])])
    write_jsonl(output / "window_status.jsonl", rows)


def upsert_jsonl_status(
    path: Path,
    key: str,
    updates: list[dict[str, Any]],
) -> None:
    rows = read_jsonl(path)
    by_key = {str(row[key]): row for row in rows}
    for update in updates:
        value = str(update[key])
        by_key.setdefault(value, {key: update[key]}).update(update)
    write_jsonl(path, [by_key[value] for value in sorted(by_key)])


def reconcile_parent_statuses(output: Path) -> None:
    windows = all_windows(output)
    statuses = read_jsonl(output / "window_status.jsonl")
    by_id = {str(row["window_id"]): row for row in statuses}
    children: dict[str, list[str]] = {}
    for window in windows.values():
        parent = window.get("parent_window_id")
        if parent:
            children.setdefault(str(parent), []).append(str(window["window_id"]))
    changed = True
    while changed:
        changed = False
        for parent_id, child_ids in children.items():
            parent = by_id.get(parent_id)
            if parent is None or parent.get("status") != "awaiting_children":
                continue
            if child_ids and all(by_id.get(child_id, {}).get("status") in TERMINAL_STATUSES for child_id in child_ids):
                parent["status"] = "success"
                parent["coverage_status"] = "complete_via_children"
                changed = True
    write_jsonl(output / "window_status.jsonl", statuses)


async def extract_one(
    gateway: ModelGateway,
    window: dict[str, Any],
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    segments = segment_transport_batch(str(window["source_span"]))
    messages = [
        ChatMessage(role="system", content=extraction_system_prompt()),
        ChatMessage(role="user", content=render_segmented_batch(window, segments)),
    ]
    capture = gateway.start_call_capture()
    started = time.perf_counter()
    provider_raw_wire = None
    try:
        async with semaphore:
            wire_result = await gateway.generate_json(
                "planner", messages, ReactionExtractionWireResult
            )
            provider_raw_wire = wire_result.model_dump(mode="json")
            result = wire_result.to_result(provider_segment_id_map(segments))
            coverage_normalization_operation = None
            if (
                len(result.events) >= REACTION_EVENT_LIMIT
                and result.coverage_status == "complete"
                and not result.continuation_from_segment_id
            ):
                segment_indexes = {
                    str(row["segment_id"]): index for index, row in enumerate(segments)
                }
                referenced = [
                    span.segment_id
                    for event in result.events
                    for span in [*event.reaction_spans, *event.trigger_spans]
                ]
                last_index = max(segment_indexes[value] for value in referenced)
                if last_index + 1 < len(segments):
                    result = result.model_copy(update={
                        "coverage_status": "partial",
                        "continuation_from_segment_id": str(
                            segments[last_index + 1]["segment_id"]
                        ),
                    })
                    coverage_normalization_operation = (
                        "event_cap_complete_forced_to_partial"
                    )
        status = "success"
        error = None
    except Exception as exc:
        result = None
        coverage_normalization_operation = None
        status = "failed"
        error = {"error_type": type(exc).__name__, "message": str(exc)[:500]}
    return {
        "window": window,
        "segments": segments,
        "result": result,
        "provider_raw_wire": provider_raw_wire,
        "coverage_normalization_operation": coverage_normalization_operation,
        "status": status,
        "error": error,
        "call_records": gateway.finish_call_capture(capture),
        "elapsed_seconds": time.perf_counter() - started,
        "estimated_prompt_chars": sum(len(message.content) for message in messages),
    }


def select_extraction_windows(
    windows: dict[str, dict[str, Any]], statuses: list[dict[str, Any]],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    requested = set(getattr(args, "window_ids", None) or [])
    if getattr(args, "window_id", None):
        requested.add(args.window_id)
    unknown = requested - windows.keys()
    if unknown:
        raise ValueError(f"unknown_extraction_window_ids:{sorted(unknown)}")
    eligible = [
        row for row in statuses
        if (not requested or str(row["window_id"]) in requested)
        and (row["status"] == "pending" or (
            args.retry_failures and row["status"] == "failed"
            and int(row.get("attempts", 0)) < args.max_attempts
        ))
    ]
    if args.derived_first:
        eligible.sort(key=lambda row: (
            windows[str(row["window_id"])].get("parent_window_id") is None,
            str(row["window_id"]),
        ))
    return eligible[:args.max_calls]


async def extract(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    windows = all_windows(output)
    statuses = read_jsonl(output / "window_status.jsonl")
    eligible = select_extraction_windows(windows, statuses, args)
    gateway = gateway_for(args.model_max_tokens)
    semaphore = asyncio.Semaphore(args.concurrency)
    calls_used = 0
    tokens_used = 0
    try:
        for batch_start in range(0, len(eligible), args.batch_size):
            if args.max_total_tokens and tokens_used >= args.max_total_tokens:
                break
            batch_status = eligible[batch_start:batch_start + args.batch_size]
            replace_statuses(output, {
                str(row["window_id"]): {
                    "status": "inflight",
                    "attempts": int(row.get("attempts", 0)) + 1,
                    "inflight_started_at": datetime.now().astimezone().isoformat(
                        timespec="seconds"
                    ),
                }
                for row in batch_status
            })
            results = await asyncio.gather(*(
                extract_one(gateway, windows[str(row["window_id"])], semaphore)
                for row in batch_status
            ))
            raw_rows: list[dict[str, Any]] = []
            unit_rows: list[dict[str, Any]] = []
            failure_rows: list[dict[str, Any]] = []
            call_rows: list[dict[str, Any]] = []
            child_rows: list[dict[str, Any]] = []
            updates: dict[str, dict[str, Any]] = {}
            resource_exhausted = False
            for item, prior in zip(results, batch_status, strict=True):
                window = item["window"]
                window_id = str(window["window_id"])
                calls_used += len(item["call_records"])
                item_tokens = sum(int(row.get("total_tokens") or 0) for row in item["call_records"])
                tokens_used += item_tokens
                call_rows.extend({
                    **row, "stage": "reaction_event_extraction",
                    "window_id": window_id, "source_id": window["source_id"],
                    "pipeline_version": PIPELINE_VERSION,
                    "extraction_schema_version": EXTRACTION_SCHEMA_VERSION,
                } for row in item["call_records"])
                if item["status"] == "failed":
                    failure = {
                        "window_id": window_id, "source_id": window["source_id"],
                        "stage": "reaction_event_extraction", **item["error"],
                    }
                    if item["provider_raw_wire"] is not None:
                        failure["provider_raw_wire"] = item["provider_raw_wire"]
                    failure_rows.append(failure)
                    updates[window_id] = {
                        "status": "failed",
                        "attempts": int(prior.get("attempts", 0)) + 1,
                        "last_error": failure,
                        "coverage_status": "provider_or_schema_failure",
                    }
                    resource_exhausted |= "Insufficient Balance" in str(item["error"])
                    continue
                result: ReactionExtractionResult = item["result"]
                raw_rows.append({
                    "pipeline_version": PIPELINE_VERSION,
                    "extraction_schema_version": EXTRACTION_SCHEMA_VERSION,
                    "window_id": window_id, "source_id": window["source_id"],
                    "elapsed_seconds": item["elapsed_seconds"],
                    "provider_tokens": item_tokens,
                    "estimated_prompt_chars": item["estimated_prompt_chars"],
                    "segment_count": len(item["segments"]),
                    "provider_segment_count": len(provider_segment_id_map(item["segments"])),
                    "provider_raw_wire": item["provider_raw_wire"],
                    "effective_coverage_status": (
                        "partial" if result.continuation_from_segment_id
                        else result.coverage_status
                    ),
                    "coverage_normalization_operation": (
                        item["coverage_normalization_operation"]
                        or (
                            "continuation_cursor_overrides_complete_status"
                            if result.coverage_status == "complete"
                            and result.continuation_from_segment_id
                            else None
                        )
                    ),
                    "result": result.model_dump(mode="json"),
                })
                materialized, mechanical_failures = materialize_reaction_events(
                    window, item["segments"], result
                )
                for unit in materialized:
                    unit.update(route_semantic_review(unit))
                unit_rows.extend(materialized)
                failure_rows.extend(mechanical_failures)
                if result.events and not materialized:
                    updates[window_id] = {
                        "status": "failed",
                        "attempts": int(prior.get("attempts", 0)) + 1,
                        "last_error": {"stage": "mechanical_validation", "error": "all_events_invalid"},
                        "coverage_status": "mechanical_failure",
                    }
                    continue
                effective_partial = (
                    result.coverage_status == "partial"
                    or bool(result.continuation_from_segment_id)
                )
                if effective_partial:
                    try:
                        children = child_windows_for_partial_coverage(
                            window, item["segments"],
                            result.continuation_from_segment_id or "",
                        )
                    except ValueError as exc:
                        failure_rows.append({
                            "window_id": window_id, "source_id": window["source_id"],
                            "stage": "coverage", "error": str(exc),
                        })
                        updates[window_id] = {
                            "status": "failed",
                            "attempts": int(prior.get("attempts", 0)) + 1,
                            "last_error": {"stage": "coverage", "error": str(exc)},
                            "coverage_status": "invalid_continuation_cursor",
                        }
                        continue
                    existing = set(windows) | {str(row["window_id"]) for row in child_rows}
                    children = [row for row in children if str(row["window_id"]) not in existing]
                    child_rows.extend(children)
                    updates[window_id] = {
                        "status": "awaiting_children" if children else "failed",
                        "attempts": int(prior.get("attempts", 0)) + 1,
                        "last_error": None if children else {
                            "stage": "coverage", "error": "partial_without_new_children"
                        },
                        "coverage_status": "partial",
                        "raw_event_count": len(result.events),
                        "valid_unit_count": len(materialized),
                        "child_window_ids": [row["window_id"] for row in children],
                    }
                elif not result.events:
                    updates[window_id] = {
                        "status": "no_unit",
                        "attempts": int(prior.get("attempts", 0)) + 1,
                        "last_error": None,
                        "coverage_status": "complete",
                        "true_no_unit_reason": result.no_unit_reason,
                        "raw_event_count": 0, "valid_unit_count": 0,
                    }
                else:
                    updates[window_id] = {
                        "status": "success",
                        "attempts": int(prior.get("attempts", 0)) + 1,
                        "last_error": None, "coverage_status": "complete",
                        "raw_event_count": len(result.events),
                        "valid_unit_count": len(materialized),
                    }
            append_jsonl(output / "extract_raw.jsonl", raw_rows)
            append_jsonl(output / "source_unit_candidates.jsonl", unit_rows)
            append_jsonl(output / "pipeline_failures.jsonl", failure_rows)
            append_jsonl(output / "extract_call_records.jsonl", call_rows)
            replace_statuses(output, updates)
            if child_rows:
                append_jsonl(output / "windows.derived.jsonl", child_rows)
                append_jsonl(output / "window_status.jsonl", [{
                    "window_id": row["window_id"], "status": "pending", "attempts": 0,
                    "last_error": None, "coverage_status": "not_started",
                } for row in child_rows])
                windows.update({str(row["window_id"]): row for row in child_rows})
            reconcile_parent_statuses(output)
            print(
                f"extract calls={calls_used} tokens={tokens_used} "
                f"windows={min(batch_start + len(batch_status), len(eligible))}/{len(eligible)}",
                flush=True,
            )
            if resource_exhausted:
                (output / "execution_halt.json").write_text(json.dumps({
                    "stage": "reaction_event_extraction",
                    "reason": "provider_resource_budget_exhausted",
                    "calls_used": calls_used, "tokens_used": tokens_used,
                }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                break
    finally:
        await gateway.close()
    write_progress(output, calls_used=calls_used, tokens_used=tokens_used)


def unique_units(output: Path) -> list[dict[str, Any]]:
    reviewed_ids = {str(row["unit_id"]) for row in read_jsonl(output / "semantic_reviews.jsonl")}
    exact: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(output / "source_unit_candidates.jsonl"):
        unit_id = str(row["unit_id"])
        if unit_id not in exact:
            exact[unit_id] = row
        else:
            lineage = [*exact[unit_id].get("source_lineage", []), *row.get("source_lineage", [])]
            exact[unit_id]["source_lineage"] = list({
                json.dumps(item, ensure_ascii=False, sort_keys=True): item for item in lineage
            }.values())
    merged: list[dict[str, Any]] = []
    ordered = sorted(exact.values(), key=lambda row: (
        str(row["source_id"]), int(row["reaction_source_start_char"]),
        int(row["reaction_source_end_char"]),
    ))
    for row in ordered:
        start = int(row["reaction_source_start_char"])
        end = int(row["reaction_source_end_char"])
        match_index = None
        for index in range(len(merged) - 1, -1, -1):
            prior = merged[index]
            if str(prior["source_id"]) != str(row["source_id"]):
                break
            prior_start = int(prior["reaction_source_start_char"])
            prior_end = int(prior["reaction_source_end_char"])
            if prior_end <= start:
                break
            overlap = max(0, min(end, prior_end) - max(start, prior_start))
            shorter = min(end - start, prior_end - prior_start)
            if shorter > 0 and overlap / shorter >= 0.9:
                match_index = index
                break
        if match_index is None:
            merged.append(row)
            continue
        prior = merged[match_index]
        preferred, duplicate = sorted(
            [prior, row],
            key=lambda value: (
                str(value["unit_id"]) not in reviewed_ids,
                int(value["reaction_source_end_char"])
                - int(value["reaction_source_start_char"]),
                str(value["unit_id"]),
            ),
        )
        lineage = [
            *preferred.get("source_lineage", []), *duplicate.get("source_lineage", [])
        ]
        preferred["source_lineage"] = list({
            json.dumps(item, ensure_ascii=False, sort_keys=True): item for item in lineage
        }.values())
        preferred["dedupe_merged_unit_ids"] = sorted(set([
            *preferred.get("dedupe_merged_unit_ids", []),
            *duplicate.get("dedupe_merged_unit_ids", []),
            str(duplicate["unit_id"]),
        ]))
        merged[match_index] = preferred
    return sorted(merged, key=lambda row: str(row["unit_id"]))


def _program_review(unit: dict[str, Any]) -> dict[str, Any]:
    route = unit["review_route"]
    if route == "program_external_reject":
        return {
            "unit_id": unit["unit_id"], "decision": "rejected",
            "attribution_status": "external", "event_relation_status": "uncertain",
            "source_specific_dependency": False, "transfer_scope": "reject",
            "behavior_ids": unit.get("behavior_ids", []),
            "applicability_requires_all": [], "applicability_forbids_any": [],
            "payload_kind": "none", "reviewed_fragment_spans": [],
            "insufficient_context": False, "needed_context": "none",
            "review_basis": "mechanical external marker boundary",
            "review_route": route, "review_schema_version": REVIEW_SCHEMA_VERSION,
        }
    fragment = len(str(unit.get("source_response_turn", ""))) <= 80
    return {
        "unit_id": unit["unit_id"], "decision": "approved",
        "attribution_status": "verified", "event_relation_status": "correct",
        "source_specific_dependency": False, "transfer_scope": "general",
        "payload_class": "unreviewed",
        "behavior_ids": unit.get("behavior_ids", []),
        "applicability_requires_all": [], "applicability_forbids_any": [],
        "payload_kind": "reviewed_fragment" if fragment else "reaction_pattern",
        "reviewed_fragment_spans": unit.get("reaction_spans", []) if fragment else [],
        "insufficient_context": False, "needed_context": "none",
        "review_basis": "verified marker, high-confidence bounded reaction, no routed risk",
        "review_route": route, "review_schema_version": REVIEW_SCHEMA_VERSION,
    }


def fragment_within_reaction(unit: dict[str, Any], review_row: dict[str, Any]) -> bool:
    spans = review_row.get("reviewed_fragment_spans", [])
    if not spans:
        return False
    for span in spans:
        covered_to = int(span["start"])
        for reaction in sorted(unit["reaction_spans"], key=lambda item: int(item["start"])):
            if reaction["segment_id"] != span["segment_id"]:
                continue
            if int(reaction["start"]) <= covered_to < int(reaction["end"]):
                covered_to = int(reaction["end"])
        if covered_to < int(span["end"]):
            return False
    return True


def review_gap_reasons(row: dict[str, Any], unit: dict[str, Any] | None = None) -> list[str]:
    reasons = []
    if row.get("decision") == "pending":
        reasons.append("pending_verdict")
    if row.get("decision") == "approved" and not row.get("behavior_ids"):
        reasons.append("approved_missing_a_projection")
    if (unit is not None and row.get("decision") == "approved"
            and row.get("payload_kind") == "reviewed_fragment"
            and not fragment_within_reaction(unit, row)):
        reasons.append("approved_fragment_outside_reaction")
    if (row.get("decision") == "approved" and row.get("transfer_scope") == "general"
            and row.get("payload_kind") in {"reviewed_fragment", "reaction_pattern"}
            and row.get("payload_class") not in {"reaction_only", "turn_local_stance"}):
        reasons.append("approved_missing_safe_payload_class")
    return reasons


def select_review_units(units, prior_by_unit, statuses, *, revisit_gaps=False, unit_ids=None):
    selected = []
    for unit in units:
        unit_id = str(unit["unit_id"])
        if unit_ids is not None and unit_id not in unit_ids:
            continue
        if statuses.get(unit_id, {}).get("status") == "inflight":
            continue
        attempts = prior_by_unit.get(unit_id, [])
        latest = attempts[-1] if attempts else {}
        gap = bool(attempts and review_gap_reasons(latest, unit))
        completed = bool(attempts and (not latest.get("insufficient_context")
                         or len(attempts) >= 2 or latest.get("needed_context") == "none"))
        if completed and not (revisit_gaps and gap):
            continue
        if unit["review_required"] or (revisit_gaps and gap):
            selected.append(unit)
    if revisit_gaps:
        selected.sort(key=lambda unit: (
            0 if review_gap_reasons((prior_by_unit.get(str(unit["unit_id"])) or [{}])[-1], unit) else 1,
            str(unit["source_id"]), int(unit["reaction_source_start_char"]),
        ))
    return selected


async def review(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    units = unique_units(output)
    prior = read_jsonl(output / "semantic_reviews.jsonl")
    prior_by_unit: dict[str, list[dict[str, Any]]] = {}
    for row in prior:
        prior_by_unit.setdefault(str(row["unit_id"]), []).append(row)
    reviewed = {
        unit_id for unit_id, rows in prior_by_unit.items()
        if (
            not rows[-1].get("insufficient_context")
            or len(rows) >= 2
            or rows[-1].get("needed_context") == "none"
        )
    }
    review_status_path = output / "review_unit_status.jsonl"
    review_unit_status = {
        str(row["unit_id"]): row for row in read_jsonl(review_status_path)
    }
    requested_ids = set(args.unit_ids) if args.unit_ids else None
    known_ids = {str(row["unit_id"]) for row in units}
    if requested_ids is not None and requested_ids - known_ids:
        raise ValueError("unknown_review_unit_ids:" + ",".join(sorted(requested_ids - known_ids)))
    local = [
        _program_review(row) for row in units
        if row["unit_id"] not in reviewed and not row["review_required"]
        and (requested_ids is None or str(row["unit_id"]) in requested_ids)
    ]
    append_jsonl(output / "semantic_reviews.jsonl", local)
    reviewed.update(str(row["unit_id"]) for row in local)
    targeted = select_review_units(units, prior_by_unit, review_unit_status,
                                  revisit_gaps=args.revisit_review_gaps, unit_ids=requested_ids)
    windows = all_windows(output)
    gateway = gateway_for(args.review_model_max_tokens)
    calls_used = 0
    tokens_used = 0
    try:
        for batch_start in range(0, min(len(targeted), args.max_review_units), args.review_batch_size):
            if calls_used >= args.max_calls or (args.max_total_tokens and tokens_used >= args.max_total_tokens):
                break
            batch = targeted[batch_start:min(batch_start + args.review_batch_size, args.max_review_units)]
            packets = []
            for unit in batch:
                window = windows[str(unit["window_id"])]
                prior_attempts = prior_by_unit.get(str(unit["unit_id"]), [])
                gaps = review_gap_reasons(prior_attempts[-1], unit) if prior_attempts else []
                expanded = bool(prior_attempts and (prior_attempts[-1].get("insufficient_context")
                                                    or (args.revisit_review_gaps and gaps)))
                needed = str(prior_attempts[-1].get("needed_context", "none")) if expanded else "none"
                if expanded and needed == "none":
                    needed = "both"
                packet = minimal_evidence_packet(
                    unit, window, segment_transport_batch(str(window["source_span"])),
                    expanded=expanded,
                    needed_context=needed,
                )
                if args.revisit_review_gaps and gaps:
                    packet["review_revision_request"] = {
                        "reasons": gaps, "previous_verdict": prior_attempts[-1],
                        "requirement": "依据原文复核行为与适用条件；缺A时仅从固定词表选择有证据的行为。fragment只能取当前reaction_spans内的子范围，不能改用上下文/trigger/相邻回答。证据不足或不能转移仍保留pending或reject。",
                    }
                packets.append(packet)
            append_jsonl(output / "review_evidence_packets.jsonl", packets)
            upsert_jsonl_status(review_status_path, "unit_id", [{
                "unit_id": unit["unit_id"],
                "status": "inflight",
                "attempt": len(prior_by_unit.get(str(unit["unit_id"]), [])) + 1,
                "inflight_started_at": datetime.now().astimezone().isoformat(
                    timespec="seconds"
                ),
            } for unit in batch])
            capture = gateway.start_call_capture()
            try:
                result = await gateway.generate_json(
                    "planner",
                    [
                        ChatMessage(role="system", content=targeted_review_system_prompt()),
                        ChatMessage(role="user", content=json.dumps(
                            {"evidence_packets": packets}, ensure_ascii=False,
                            sort_keys=True, separators=(",", ":"),
                        )),
                    ],
                    TargetedReviewResult,
                )
                error = None
            except Exception as exc:
                result = None
                error = exc
            records = gateway.finish_call_capture(capture)
            calls_used += len(records)
            tokens_used += sum(int(row.get("total_tokens") or 0) for row in records)
            append_jsonl(output / "review_call_records.jsonl", [{
                **row, "stage": "targeted_semantic_review",
                "unit_ids": [unit["unit_id"] for unit in batch],
                "evidence_chars": sum(packet["evidence_chars"] for packet in packets),
                "full_window_chars_avoided": sum(packet["full_window_chars"] for packet in packets),
            } for row in records])
            if error is not None:
                append_jsonl(output / "pipeline_failures.jsonl", [{
                    "stage": "targeted_semantic_review",
                    "unit_ids": [unit["unit_id"] for unit in batch],
                    "error_type": type(error).__name__, "message": str(error)[:500],
                }])
                if "Insufficient Balance" in str(error):
                    break
                upsert_jsonl_status(review_status_path, "unit_id", [{
                    "unit_id": unit["unit_id"], "status": "failed",
                    "last_error": {"type": type(error).__name__, "message": str(error)[:500]},
                } for unit in batch])
                continue
            by_id = {str(unit["unit_id"]): unit for unit in batch}
            rows = []
            for item in result.reviews:
                row = item.model_dump(mode="json")
                if item.unit_id not in by_id:
                    row["orphan_review"] = True
                elif (row.get("payload_kind") == "reviewed_fragment"
                      and not fragment_within_reaction(by_id[item.unit_id], row)):
                    row.update(decision="pending", payload_kind="none", reviewed_fragment_spans=[],
                               payload_normalization_operation="blocked_fragment_outside_reaction")
                prior_attempts = prior_by_unit.get(item.unit_id, [])
                row["review_attempt"] = len(prior_attempts) + 1
                row["context_expanded"] = bool(prior_attempts)
                if (
                    row.get("payload_kind") == "reviewed_fragment"
                    and row.get("source_specific_dependency")
                ):
                    row.update({
                        "decision": "pending",
                        "payload_kind": "none",
                        "reviewed_fragment_spans": [],
                        "payload_normalization_operation":
                            "blocked_source_specific_reviewed_fragment",
                    })
                row.update({
                    "review_route": "targeted_semantic_review",
                    "review_schema_version": REVIEW_SCHEMA_VERSION,
                })
                rows.append(row)
                prior_by_unit.setdefault(item.unit_id, []).append(row)
            append_jsonl(output / "semantic_reviews.jsonl", rows)
            returned = {str(row["unit_id"]) for row in rows if not row.get("orphan_review")}
            upsert_jsonl_status(review_status_path, "unit_id", [{
                "unit_id": unit["unit_id"],
                "status": "success" if str(unit["unit_id"]) in returned else "failed",
                "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            } for unit in batch])
            print(
                f"review calls={calls_used} tokens={tokens_used} "
                f"units={min(batch_start + len(batch), len(targeted))}/{len(targeted)}",
                flush=True,
            )
    finally:
        await gateway.close()
    write_review_progress(output, units)
    append_jsonl(output / "review_batch_executions.jsonl", [{
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "review_schema_version": REVIEW_SCHEMA_VERSION, "revisit_review_gaps": args.revisit_review_gaps,
        "requested_unit_ids": args.unit_ids, "selected_units": min(len(targeted), args.max_review_units),
        "max_calls": args.max_calls, "max_total_tokens": args.max_total_tokens,
        "calls": calls_used, "tokens": tokens_used,
        "token_budget_semantics": "stop before next request once actual cumulative tokens reach threshold; final request may cross it",
    }])


def project(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    units = {str(row["unit_id"]): row for row in unique_units(output)}
    reviews = {
        str(row["unit_id"]): row for row in read_jsonl(output / "semantic_reviews.jsonl")
        if not row.get("orphan_review")
    }
    windows = all_windows(output)
    projected: list[dict[str, Any]] = []
    for unit_id, unit in units.items():
        review_row = reviews.get(unit_id)
        decision = str(review_row.get("decision")) if review_row else "pending"
        effective_attribution_status = (
            str(review_row.get("attribution_status"))
            if review_row else str(unit.get("attribution_status", "unresolved"))
        )
        effective_attribution_basis = str(
            unit.get("attribution_basis", "conflicted_evidence")
        )
        if (
            review_row
            and effective_attribution_status == "verified"
            and unit.get("attribution_status") != "verified"
        ):
            effective_attribution_basis = "reviewer_verified"
        q_projection = None
        a_projection = None
        if review_row and decision != "rejected":
            projection_review_status = decision
            if unit.get("source_user_turn"):
                q_projection = {
                    "review_status": projection_review_status,
                    "trigger_text_ref": unit.get("trigger_spans"),
                    "scene_description": unit.get("event_relation", ""),
                    "input_features": {
                        "interaction_frame": unit.get("interaction_frame", "unknown"),
                        "interaction_target": unit.get("interaction_target", "unknown"),
                    },
                    "evidence_refs": unit.get("trigger_spans", []),
                }
            elif unit.get("trigger_basis") in {"inferred", "restated", "self_initiated"} and str(unit.get("event_relation", "")).strip():
                q_projection = {
                    "review_status": projection_review_status, "trigger_text_ref": None,
                    "scene_description": unit["event_relation"],
                    "input_features": {
                        "interaction_frame": unit.get("interaction_frame", "unknown"),
                        "interaction_target": unit.get("interaction_target", "unknown"),
                    },
                    "evidence_refs": unit.get("attribution_evidence_refs", []),
                }
            if review_row.get("behavior_ids"):
                a_projection = {
                    "review_status": projection_review_status,
                    "behavior_ids": review_row["behavior_ids"],
                    "mechanism_description": unit.get("reaction_mechanism", ""),
                    "compatibility_features": {
                        "interaction_frame": unit.get("interaction_frame", "unknown"),
                        "interaction_target": unit.get("interaction_target", "unknown"),
                    },
                    "evidence_refs": unit.get("attribution_evidence_refs", []),
                }
        fragment = None
        reaction_pattern = None
        payload_kind = "none"
        runtime_eligible = False
        projection_block_reason = None
        if (
            review_row
            and decision == "approved"
            and review_row.get("attribution_status") == "verified"
            and review_row.get("transfer_scope") == "general"
        ):
            if review_row.get("payload_kind") == "reviewed_fragment":
                if not fragment_within_reaction(unit, review_row):
                    projection_block_reason = "fragment_outside_reaction"
                elif review_row.get("payload_class") not in {"reaction_only", "turn_local_stance"}:
                    projection_block_reason = "payload_facts_not_cleared"
                elif not review_row.get("source_specific_dependency"):
                    window = windows[str(unit["window_id"])]
                    segment_map = {
                        str(row["segment_id"]): row
                        for row in segment_transport_batch(str(window["source_span"]))
                    }
                    fragment = "".join(
                        resolve_span_exact(span, segment_map)["text"]
                        for span in [SpanRef.model_validate(value) for value in review_row.get("reviewed_fragment_spans", [])]
                    )
                    payload_kind = "reviewed_fragment"
            elif review_row.get("payload_kind") == "reaction_pattern":
                if review_row.get("payload_class") not in {"reaction_only", "turn_local_stance"}:
                    projection_block_reason = "payload_facts_not_cleared"
                else:
                    payload_kind = "reaction_pattern"
                    reaction_pattern = {
                        "behavior_ids": review_row.get("behavior_ids", []),
                        "reaction_mechanism": unit.get("reaction_mechanism", ""),
                        "applicability_requires_all": review_row.get("applicability_requires_all", []),
                        "applicability_forbids_any": review_row.get("applicability_forbids_any", []),
                    }
            runtime_eligible = payload_kind != "none" and bool(a_projection)
        projected.append({
            **unit,
            "semantic_review": review_row,
            "semantic_review_decision": decision,
            "effective_attribution_status": effective_attribution_status,
            "effective_attribution_basis": effective_attribution_basis,
            "q_projection": q_projection,
            "a_projection": a_projection,
            "projection_schema_version": "source-reaction-unit-projection-v1",
            "projection_version": "reaction-pipeline-v2-projection-v1",
            "runtime_payload_kind": payload_kind,
            "runtime_reaction_fragment": fragment,
            "runtime_reaction_pattern": reaction_pattern,
            "runtime_payload_reviewed": runtime_eligible,
            "runtime_eligible": runtime_eligible,
            "projection_block_reason": projection_block_reason,
        })
    write_jsonl(output / "source_reaction_units.reviewed.jsonl", projected)
    report = {
        "source_units": len(projected),
        "reviewed": len(reviews),
        "review_decisions": dict(Counter(row["semantic_review_decision"] for row in projected)),
        "review_routes": dict(Counter(
            str((row.get("semantic_review") or {}).get("review_route", "unreviewed"))
            for row in projected
        )),
        "runtime_eligible": sum(bool(row["runtime_eligible"]) for row in projected),
        "projection_blocks": dict(Counter(row["projection_block_reason"] for row in projected
                                           if row["projection_block_reason"])),
        "attribution_status": dict(Counter(
            str(row["effective_attribution_status"]) for row in projected
        )),
        "attribution_basis": dict(Counter(
            str(row["effective_attribution_basis"]) for row in projected
        )),
        "runtime_attribution_basis": dict(Counter(
            str(row["effective_attribution_basis"])
            for row in projected if row["runtime_eligible"]
        )),
        "q_projection": sum(row.get("q_projection") is not None for row in projected),
        "a_projection": sum(row.get("a_projection") is not None for row in projected),
        "a_only": sum(row.get("q_projection") is None and row.get("a_projection") is not None for row in projected),
    }
    (output / "projection_manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_review_progress(output, units)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def write_progress(output: Path, *, calls_used: int, tokens_used: int) -> None:
    statuses = read_jsonl(output / "window_status.jsonl")
    records = read_jsonl(output / "extract_call_records.jsonl")
    report = {
        "pipeline_version": PIPELINE_VERSION,
        "window_status": dict(Counter(str(row["status"]) for row in statuses)),
        "calls": calls_used, "tokens": tokens_used,
        "cumulative_calls_including_inherited": len(records),
        "cumulative_tokens_including_inherited": sum(int(row.get("total_tokens") or 0) for row in records),
        "full_scan_complete": bool(statuses) and all(
            row["status"] in TERMINAL_STATUSES for row in statuses
        ),
    }
    (output / "extract_progress.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


def write_review_progress(output: Path, units: list[dict[str, Any]]) -> None:
    reviews = read_jsonl(output / "semantic_reviews.jsonl")
    latest = {str(row["unit_id"]): row for row in reviews}
    records = read_jsonl(output / "review_call_records.jsonl")
    report = {
        "source_units": len(units), "reviewed": len(latest),
        "remaining": len(units) - len(latest),
        "decisions": dict(Counter(str(row.get("decision")) for row in latest.values())),
        "routes": dict(Counter(str(row.get("review_route")) for row in latest.values())),
        "review_attempts": len(reviews),
        "cumulative_calls_including_inherited": len(records),
        "cumulative_tokens_including_inherited": sum(int(row.get("total_tokens") or 0) for row in records),
        "expanded_review_attempts": sum(
            bool(row.get("context_expanded")) for row in reviews
        ),
    }
    (output / "review_progress.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


def status(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    statuses = read_jsonl(output / "window_status.jsonl")
    units = unique_units(output)
    reviews = read_jsonl(output / "semantic_reviews.jsonl")
    latest_reviews = {str(row["unit_id"]): row for row in reviews}
    calls = read_jsonl(output / "extract_call_records.jsonl")
    review_calls = read_jsonl(output / "review_call_records.jsonl")
    print(json.dumps({
        "pipeline_version": PIPELINE_VERSION,
        "window_status": dict(Counter(str(row["status"]) for row in statuses)),
        "source_units": len(units), "reviewed_units": len(latest_reviews),
        "review_required": sum(bool(row["review_required"]) for row in units),
        "extract_calls": len(calls),
        "extract_tokens": sum(int(row.get("total_tokens") or 0) for row in calls),
        "review_calls": len(review_calls),
        "review_tokens": sum(int(row.get("total_tokens") or 0) for row in review_calls),
    }, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=[
            "init", "init-full", "init-selected", "extend", "extract", "review", "project",
            "status", "recover-failed", "record-revision", "render-input",
        ]
    )
    parser.add_argument("--run-id", default="desktop-style-reaction-pipeline-v2")
    parser.add_argument("--parent", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay-windows", type=int, default=16)
    parser.add_argument("--max-calls", type=int, default=24)
    parser.add_argument("--max-total-tokens", type=int, default=0)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--model-max-tokens", type=int, default=5000)
    parser.add_argument("--review-model-max-tokens", type=int, default=3000)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=3)
    parser.add_argument("--review-batch-size", type=int, default=3)
    parser.add_argument("--max-review-units", type=int, default=60)
    parser.add_argument("--unit-ids", nargs="+")
    parser.add_argument("--revisit-review-gaps", action="store_true")
    parser.add_argument("--retry-failures", action="store_true")
    parser.add_argument("--derived-first", action="store_true")
    parser.add_argument("--window-id")
    parser.add_argument("--window-ids", nargs="+")
    parser.add_argument("--recovery-child-chars", type=int, default=1000)
    parser.add_argument("--revision-reason", default="manual_pipeline_revision")
    args = parser.parse_args()
    if args.command in {"init", "init-full", "init-selected"} and args.parent is None:
        parser.error(f"{args.command} requires --parent")
    if args.command == "init-selected" and not args.window_ids:
        parser.error("init-selected requires --window-ids")
    if args.command == "extend" and (args.parent is None or not args.window_id):
        parser.error("extend requires --parent and --window-id")
    if args.command == "render-input" and not args.window_id:
        parser.error("render-input requires --window-id")
    return args


if __name__ == "__main__":
    options = parse_args()
    if options.command == "init":
        initialize(options)
    elif options.command == "init-full":
        initialize_full(options)
    elif options.command == "init-selected":
        initialize_selected(options)
    elif options.command == "extend":
        extend_replay(options)
    elif options.command == "recover-failed":
        recover_failed_windows(options)
    elif options.command == "record-revision":
        record_pipeline_revision(options)
    elif options.command == "render-input":
        render_model_input(options)
    elif options.command == "extract":
        asyncio.run(extract(options))
    elif options.command == "review":
        asyncio.run(review(options))
    elif options.command == "project":
        project(options)
    else:
        status(options)
