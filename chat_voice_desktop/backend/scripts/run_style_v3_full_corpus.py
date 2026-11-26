from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import shutil
import sqlite3
import sys
import time
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
from scripts.run_style_v3_pilot import (
    ALLOWED_BEHAVIORS,
    PREDICATE_DOMAINS,
    RANGE_EXTRACT_SYSTEM,
    REVIEW_SYSTEM,
    RangeExtractionResult,
    ReviewResult,
    SpanRef,
    resolve_span,
    segment_window,
    validate_predicates,
)


SOURCE_GROUP_DATE = re.compile(r"(?P<date>20\d{2}年\d{1,2}月\d{1,2}日)")
SPEAKER_LINE = re.compile(
    r"(?im)^\s*(?:弹幕|憨憨|憨|Hanser|hanser|茶|海|七海|阿萨|缘|鸟|毛怪\d*)[:：]"
)
TIME_MARKER = re.compile(r"(?m)(?:^|\n)\s*\d{1,2}[.:：]\d{2}")
FULL_RANGE_EXTRACT_SYSTEM = RANGE_EXTRACT_SYSTEM + """
全量批处理补充硬约束：
1. trigger_basis=explicit 时 source_user_span 必须非空；若能理解有外部触发、但无法在独立 segment/range 中精确定位原话，source_user_span 保持 null 且 trigger_basis 改为 inferred 或 restated。
2. interaction_target 只能是 assistant、current_user、third_party；audience 只属于顶层 audience 字段，绝不能写入 interaction_target。
3. start/end 针对 segment.text 的完整字符串计算，包含说话人前缀和标点；end 绝不能超过给出的 length。
4. 不因窗口内容丰富而凑满两个单元；事实、自传或说话人不明的内容可以零产出。
"""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def source_group(filename: str) -> str:
    match = SOURCE_GROUP_DATE.search(filename)
    if match:
        return match.group("date")
    stem = Path(filename).stem
    return re.sub(r"(?:\s*\(\d+\)|[-_ ]?[AB]\d*)$", "", stem, flags=re.I).strip()


def content_windows(text: str, *, target_chars: int, overlap_chars: int) -> list[tuple[int, int]]:
    if not text:
        return []
    if len(text) <= target_chars:
        return [(0, len(text))]
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        desired = min(len(text), start + target_chars)
        if desired == len(text):
            end = len(text)
        else:
            lower = start + max(1, int(target_chars * 0.72))
            newline = text.rfind("\n", lower, min(len(text), desired + 240))
            end = newline + 1 if newline >= lower else desired
        if end <= start:
            end = min(len(text), start + target_chars)
        ranges.append((start, end))
        if end >= len(text):
            break
        start = max(start + 1, end - overlap_chars)
    return ranges


def pilot_document_ids() -> set[int]:
    path = PROJECT_ROOT / "audit_artifacts" / "desktop_style_pilot_20260914_001" / "windows.jsonl"
    return {int(row["document_id"]) for row in read_jsonl(path)}


def freeze(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    database = args.database.resolve()
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id,filename,filepath,content,mtime,size,indexed_at FROM documents ORDER BY id"
        ).fetchall()

    seen_fingerprints: dict[str, int] = {}
    sources: list[dict[str, Any]] = []
    eligible_groups: set[str] = set()
    seen_in_development = pilot_document_ids()
    for row in rows:
        content = str(row["content"] or "")
        filename = str(row["filename"])
        extension = Path(filename).suffix.casefold()
        fingerprint = hashlib.sha256(content.encode("utf-8")).hexdigest()
        group = source_group(filename)
        status = "eligible"
        duplicate_of = None
        exclusion_reason = None
        if extension != ".docx":
            status = "non_style_source"
            exclusion_reason = "non_dialogue_or_non_transcript_extension"
        elif not content.strip():
            status = "unreadable"
            exclusion_reason = "empty_content"
        elif fingerprint in seen_fingerprints:
            status = "duplicate"
            duplicate_of = seen_fingerprints[fingerprint]
            exclusion_reason = "exact_content_duplicate"
        else:
            seen_fingerprints[fingerprint] = int(row["id"])
            eligible_groups.add(group)
        original_path = PROJECT_ROOT / "source_data" / str(row["filepath"])
        explicit_speakers = len(SPEAKER_LINE.findall(content))
        time_markers = len(TIME_MARKER.findall(content))
        sources.append({
            "source_id": f"DOC-{int(row['id']):04d}",
            "document_id": int(row["id"]),
            "filename": filename,
            "database_filepath": str(row["filepath"]),
            "resolved_file_path": str(original_path),
            "source_file_exists": original_path.exists(),
            "source_type": "live_transcript" if extension == ".docx" else "excluded_document",
            "content_chars": len(content),
            "content_bytes_utf8": len(content.encode("utf-8")),
            "content_sha256": fingerprint,
            "source_group": group,
            "seen_in_prior_pilot": int(row["id"]) in seen_in_development,
            "speaker_marking": (
                "explicit" if explicit_speakers >= 3
                else "partial" if explicit_speakers else "unmarked"
            ),
            "explicit_speaker_markers": explicit_speakers,
            "time_markers": time_markers,
            "estimated_windows": len(content_windows(
                content,
                target_chars=args.target_chars,
                overlap_chars=args.overlap_chars,
            )) if status == "eligible" else 0,
            "inventory_status": status,
            "duplicate_of_document_id": duplicate_of,
            "exclusion_reason": exclusion_reason,
            "split": None,
            "processing_status": "not_windowed" if status == "eligible" else "excluded",
        })

    candidate_holdout_groups = sorted(
        group for group in eligible_groups
        if not any(
            source["source_group"] == group and source["seen_in_prior_pilot"]
            for source in sources
        )
    )
    holdout_count = max(1, round(len(candidate_holdout_groups) * args.holdout_fraction))
    holdout_groups = set(sorted(
        candidate_holdout_groups,
        key=lambda value: hashlib.sha256(
            f"{args.split_seed}:{value}".encode("utf-8")
        ).hexdigest(),
    )[:holdout_count])
    for source in sources:
        if source["inventory_status"] != "eligible":
            source["split"] = "excluded"
        elif source["source_group"] in holdout_groups:
            source["split"] = "independent_evaluation_holdout"
            source["processing_status"] = "held_out_before_windowing"
        else:
            source["split"] = "corpus_build"

    write_jsonl(output / "sources.frozen.jsonl", sources)
    split_manifest = {
        "run_id": args.run_id,
        "created_before_extraction": True,
        "database": str(database),
        "source_count": len(sources),
        "inventory_counts": dict(Counter(str(row["inventory_status"]) for row in sources)),
        "split_counts": dict(Counter(str(row["split"]) for row in sources)),
        "holdout_fraction": args.holdout_fraction,
        "split_seed": args.split_seed,
        "holdout_source_groups": sorted(holdout_groups),
        "prior_pilot_document_ids": sorted(seen_in_development),
        "target_chars": args.target_chars,
        "overlap_chars": args.overlap_chars,
        "sources_sha256": hashlib.sha256((output / "sources.frozen.jsonl").read_bytes()).hexdigest(),
        "production_database_read_only": True,
        "production_pointer_modified": False,
    }
    (output / "split_manifest.json").write_text(
        json.dumps(split_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(split_manifest, ensure_ascii=False, indent=2))


def make_windows(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    windows_path = output / "windows.frozen.jsonl"
    if windows_path.exists():
        raise FileExistsError("frozen windows already exist")
    sources = read_jsonl(output / "sources.frozen.jsonl")
    build_ids = {
        int(row["document_id"])
        for row in sources if row.get("split") == "corpus_build"
    }
    with sqlite3.connect(f"file:{args.database.resolve().as_posix()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id,filename,content FROM documents ORDER BY id"
        ).fetchall()
    windows: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    for row in rows:
        document_id = int(row["id"])
        if document_id not in build_ids:
            continue
        text = str(row["content"])
        ranges = content_windows(
            text, target_chars=args.target_chars, overlap_chars=args.overlap_chars
        )
        for index, (start, end) in enumerate(ranges, start=1):
            windows.append({
                "window_id": f"DOC-{document_id:04d}-W{index:04d}",
                "source_id": f"DOC-{document_id:04d}",
                "document_id": document_id,
                "filename": str(row["filename"]),
                "sampling_stratum": "full_corpus_coverage",
                "source_start_char": start,
                "source_end_char": end,
                "source_span": text[start:end],
                "parent_window_id": None,
                "subdivision_depth": 0,
            })
        covered_to_end = bool(ranges and ranges[0][0] == 0 and ranges[-1][1] == len(text))
        coverage.append({
            "source_id": f"DOC-{document_id:04d}",
            "document_id": document_id,
            "content_chars": len(text),
            "window_count": len(ranges),
            "covered_to_end": covered_to_end,
            "coverage_status": "windowed" if covered_to_end else "coverage_error",
        })
    write_jsonl(windows_path, windows)
    write_jsonl(output / "source_coverage.jsonl", coverage)
    write_jsonl(output / "window_status.jsonl", [{
        "window_id": row["window_id"],
        "status": "pending",
        "attempts": 0,
        "last_error": None,
    } for row in windows])
    summary = {
        "build_sources": len(coverage),
        "windows": len(windows),
        "coverage_errors": sum(not row["covered_to_end"] for row in coverage),
        "window_chars": sum(len(str(row["source_span"])) for row in windows),
        "unique_source_chars": sum(int(row["content_chars"]) for row in coverage),
        "windows_sha256": hashlib.sha256(windows_path.read_bytes()).hexdigest(),
    }
    (output / "window_manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def gateway_for(max_tokens: int) -> ModelGateway:
    settings = load_settings(PROJECT_ROOT / "backend" / "config.yml")
    return ModelGateway({
        "planner": replace(settings.planner, max_tokens=max_tokens, temperature=0.0)
    })


def replace_statuses(
    output: Path,
    updates: dict[str, dict[str, Any]],
) -> None:
    path = output / "window_status.jsonl"
    statuses = read_jsonl(path)
    for row in statuses:
        update = updates.get(str(row["window_id"]))
        if update:
            row.update(update)
    write_jsonl(path, statuses)


def recover_successful_raw_results(
    output: Path,
    windows: dict[str, dict[str, Any]],
) -> dict[str, int]:
    """Finish local projection after a crash without repeating provider calls."""

    raw_by_window = {
        str(row["window_id"]): row
        for row in read_jsonl(output / "extract_raw.jsonl")
    }
    if not raw_by_window:
        return {"raw_windows": 0, "recovered_windows": 0, "valid_units": 0}
    statuses = {
        str(row["window_id"]): row
        for row in read_jsonl(output / "window_status.jsonl")
    }
    needs_recovery = {
        window_id for window_id in raw_by_window
        if statuses.get(window_id, {}).get("status") not in {"success", "no_unit"}
    }
    if not needs_recovery:
        return {
            "raw_windows": len(raw_by_window), "recovered_windows": 0,
            "valid_units": 0,
        }
    existing = [
        row for row in read_jsonl(output / "extractions.jsonl")
        if str(row.get("window_id")) not in needs_recovery
    ]
    recovered: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    updates: dict[str, dict[str, Any]] = {}
    for window_id in sorted(needs_recovery):
        window = windows[window_id]
        raw = raw_by_window[window_id]
        result = RangeExtractionResult.model_validate(raw["result"])
        units, unit_failures = materialize_units({
            "window": window,
            "result": result,
            "segments": {
                str(row["segment_id"]): row
                for row in segment_window(str(window["source_span"]))
            },
        })
        recovered.extend(units)
        failures.extend(unit_failures)
        valid_units = sum(row.get("unit_id") is not None for row in units)
        updates[window_id] = {
            "status": "success" if valid_units else "no_unit",
            "last_error": None,
            "raw_unit_count": len(result.units),
            "valid_unit_count": valid_units,
            "saturated": len(result.units) >= 2,
            "recovered_from_raw": True,
        }
    write_jsonl(output / "extractions.jsonl", [*existing, *recovered])
    append_jsonl(output / "reprojection_failures.jsonl", failures)
    replace_statuses(output, updates)
    report = {
        "raw_windows": len(raw_by_window),
        "recovered_windows": len(needs_recovery),
        "valid_units": sum(row.get("unit_id") is not None for row in recovered),
    }
    (output / "raw_recovery_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def engineering_selection(
    eligible: list[dict[str, Any]],
    windows: dict[str, dict[str, Any]],
    sources: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    source_meta = {str(row["source_id"]): row for row in sources}
    by_bucket: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for status_row in eligible:
        window = windows[str(status_row["window_id"])]
        source = source_meta[str(window["source_id"])]
        length = int(source["content_chars"])
        length_bucket = "short" if length < 1800 else "long" if length > 8500 else "medium"
        key = (str(source["speaker_marking"]), length_bucket)
        by_bucket.setdefault(key, []).append(status_row)
    for rows in by_bucket.values():
        rows.sort(key=lambda row: hashlib.sha256(
            str(row["window_id"]).encode("utf-8")
        ).hexdigest())
    selected: list[dict[str, Any]] = []
    used_sources: set[str] = set()
    bucket_keys = sorted(by_bucket)
    while len(selected) < limit:
        progressed = False
        for key in bucket_keys:
            rows = by_bucket[key]
            while rows:
                candidate = rows.pop(0)
                source_id = str(windows[str(candidate["window_id"])]["source_id"])
                if source_id in used_sources:
                    continue
                selected.append(candidate)
                used_sources.add(source_id)
                progressed = True
                break
            if len(selected) >= limit:
                break
        if not progressed:
            break
    return selected


def derived_children(window: dict[str, Any]) -> list[dict[str, Any]]:
    text = str(window["source_span"])
    depth = int(window.get("subdivision_depth", 0))
    if depth >= 2 or len(text) < 900:
        return []
    midpoint = len(text) // 2
    newline = text.find("\n", midpoint, min(len(text), midpoint + 180))
    if newline < 0:
        newline = text.rfind("\n", max(0, midpoint - 180), midpoint)
    split = newline + 1 if newline >= 0 else midpoint
    overlap = min(180, max(60, len(text) // 12))
    local_ranges = [(0, min(len(text), split + overlap)), (max(0, split - overlap), len(text))]
    children: list[dict[str, Any]] = []
    for suffix, (left, right) in zip(("A", "B"), local_ranges):
        absolute_start = int(window["source_start_char"]) + left
        absolute_end = int(window["source_start_char"]) + right
        children.append({
            **window,
            "window_id": f"{window['window_id']}-D{depth + 1}{suffix}",
            "source_start_char": absolute_start,
            "source_end_char": absolute_end,
            "source_span": text[left:right],
            "parent_window_id": window["window_id"],
            "subdivision_depth": depth + 1,
            "derivation_reason": "parent_reached_unit_return_cap",
        })
    return children


def resolve_full_span(
    selection: SpanRef,
    segments: dict[str, dict[str, Any]],
) -> tuple[str, int, int, str, str | None]:
    try:
        text, start, end, speaker = resolve_span(selection, segments)
        return text, start, end, speaker, None
    except ValueError as exc:
        if str(exc) != "segment_range_invalid":
            raise
    segment = segments.get(selection.segment_id)
    if segment is None:
        raise ValueError("unknown_segment_id")
    segment_start = int(segment["start"])
    segment_end = int(segment["end"])
    if not (
        segment_start <= selection.start < selection.end <= segment_end
    ):
        raise ValueError("segment_range_invalid")
    normalized = SpanRef(
        segment_id=selection.segment_id,
        start=selection.start - segment_start,
        end=selection.end - segment_start,
    )
    text, start, end, speaker = resolve_span(normalized, segments)
    return text, start, end, speaker, "converted_window_offsets_to_segment_offsets"


async def extract_one(
    gateway: ModelGateway,
    window: dict[str, Any],
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    segment_rows = segment_window(str(window["source_span"]))
    segments = {str(row["segment_id"]): row for row in segment_rows}
    payload = {
        "window_id": window["window_id"],
        "source_id": window["source_id"],
        "document_id": window["document_id"],
        "segments": segment_rows,
        "allowed_behavior_tags": ALLOWED_BEHAVIORS,
        "predicate_domains": PREDICATE_DOMAINS,
        "output_schema": RangeExtractionResult.model_json_schema(),
        "output_rule": "只返回符合 output_schema 的 JSON 对象；顶层只能有 units 和 no_unit_reason。",
    }
    capture = gateway.start_call_capture()
    started = time.perf_counter()
    try:
        async with semaphore:
            result = await gateway.generate_json(
                "planner",
                [
                    ChatMessage(role="system", content=FULL_RANGE_EXTRACT_SYSTEM),
                    ChatMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
                ],
                RangeExtractionResult,
            )
        status = "success"
        error = None
    except Exception as exc:
        result = None
        status = "failed"
        error = {"error_type": type(exc).__name__, "message": str(exc)[:500]}
    calls = gateway.finish_call_capture(capture)
    return {
        "window": window,
        "segments": segments,
        "result": result,
        "status": status,
        "error": error,
        "call_records": calls,
        "elapsed_seconds": time.perf_counter() - started,
    }


def materialize_units(item: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    window = item["window"]
    result = item["result"]
    segments = item["segments"]
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    if result is None:
        return rows, failures
    for unit_index, unit in enumerate(result.units, start=1):
        normalization_operations: list[str] = []
        try:
            response, response_pos, response_end, response_speaker, response_normalization = resolve_full_span(
                unit.response_span, segments
            )
            if response_normalization:
                normalization_operations.append(response_normalization)
            user_turn = user_speaker = None
            user_pos = user_end = None
            if unit.source_user_span is not None:
                user_turn, user_pos, user_end, user_speaker, user_normalization = resolve_full_span(
                    unit.source_user_span, segments
                )
                if user_normalization:
                    normalization_operations.append(
                        "source_user_" + user_normalization
                    )
            predicate_reason = validate_predicates([*unit.requires_all, *unit.forbids_any])
            if predicate_reason:
                raise ValueError(predicate_reason)
            unknown_behaviors = sorted(set(unit.behavior_tags) - set(ALLOWED_BEHAVIORS))
            if unknown_behaviors:
                raise ValueError("unknown_behavior_tags:" + ",".join(unknown_behaviors))
            if unit.trigger_basis == "explicit" and unit.source_user_span is None:
                unit.trigger_basis = "inferred"
                normalization_operations.append(
                    "downgraded_explicit_trigger_without_source_span_to_inferred"
                )
            if unit.trigger_basis != "explicit" and unit.source_user_span is not None:
                raise ValueError("non_explicit_trigger_has_source_user_span")
        except ValueError as exc:
            failures.append({
                "window_id": window["window_id"],
                "unit_index": unit_index,
                "stage": "mechanical_validation",
                "error": str(exc),
                "draft": unit.model_dump(mode="json"),
            })
            continue
        response_source_start = int(window["source_start_char"]) + response_pos
        response_source_end = int(window["source_start_char"]) + response_end
        stable_key = (
            f"{window['source_id']}:{response_source_start}:{response_source_end}:"
            f"{hashlib.sha256(response.encode('utf-8')).hexdigest()}"
        )
        unit_id = "SRU-" + hashlib.sha256(stable_key.encode("utf-8")).hexdigest()[:24]
        attribution_status = (
            "verified" if response_speaker == "hanser"
            else "assumed" if response_speaker == "unknown"
            else "external"
        )
        trigger_ref = (
            f"{window['source_id']}:{int(window['source_start_char']) + user_pos}:"
            f"{int(window['source_start_char']) + user_end}"
            if user_pos is not None and user_end is not None else None
        )
        row = unit.model_dump(mode="json")
        row.update({
            "unit_id": unit_id,
            "window_id": window["window_id"],
            "parent_window_id": window.get("parent_window_id"),
            "source_id": window["source_id"],
            "document_id": window["document_id"],
            "filename": window["filename"],
            "source_span": window["source_span"],
            "source_user_turn": user_turn,
            "character_response": response,
            "source_speaker": response_speaker,
            "source_user_speaker": user_speaker,
            "response_exact_match": True,
            "user_turn_exact_match": True,
            "response_window_start": response_pos,
            "response_window_end": response_end,
            "user_window_start": user_pos,
            "user_window_end": user_end,
            "response_source_start_char": response_source_start,
            "response_source_end_char": response_source_end,
            "normalization_operations": normalization_operations,
            "speaker_candidate": "hanser" if response_speaker in {"hanser", "unknown"} else response_speaker,
            "attribution_status": attribution_status,
            "attribution_evidence_refs": [
                f"{window['source_id']}:{response_source_start}:{response_source_end}"
            ],
            "projection_schema_version": "source-reaction-unit-projection-v1",
            "style_record_version": 3,
            "projection_version": "extract-derived-v1",
            "source_revision": str(
                window.get("source_revision") or "frozen-source-v1"
            ),
            "q_projection": (
                {
                    "review_status": "pending",
                    "trigger_text_ref": trigger_ref,
                    "scene_description": unit.retrieval_description,
                    "input_features": {
                        predicate.field: predicate.one_of[0]
                        for predicate in unit.requires_all
                        if len(predicate.one_of) == 1
                    },
                    "evidence_refs": list(unit.trigger_evidence_segment_ids),
                }
                if unit.source_user_span is not None or unit.retrieval_description.strip()
                else None
            ),
            "a_projection": (
                {
                    "review_status": "pending",
                    "behavior_ids": list(unit.behavior_tags),
                    "mechanism_description": "",
                    "compatibility_features": {
                        predicate.field: predicate.one_of[0]
                        for predicate in unit.requires_all
                        if predicate.field in {
                            "interaction_frame", "interaction_target", "dialogue_function"
                        } and len(predicate.one_of) == 1
                    },
                    "evidence_refs": [
                        f"{window['source_id']}:{response_source_start}:{response_source_end}"
                    ],
                }
                if unit.behavior_tags else None
            ),
        })
        rows.append(row)
    if not result.units:
        rows.append({
            "unit_id": None,
            "window_id": window["window_id"],
            "source_id": window["source_id"],
            "document_id": window["document_id"],
            "filename": window["filename"],
            "no_unit_reason": result.no_unit_reason,
        })
    return rows, failures


async def extract(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    all_windows = [
        *read_jsonl(output / "windows.frozen.jsonl"),
        *read_jsonl(output / "windows.derived.jsonl"),
    ]
    windows = {row["window_id"]: row for row in all_windows}
    recovery = recover_successful_raw_results(output, windows)
    if recovery["recovered_windows"]:
        print(json.dumps({"raw_recovery": recovery}, ensure_ascii=False))
    statuses = read_jsonl(output / "window_status.jsonl")
    eligible = [
        row for row in statuses
        if row["status"] == "pending"
        or (
            args.retry_failures
            and row["status"] == "failed"
            and int(row.get("attempts", 0)) < args.max_attempts
        )
    ]
    if args.engineering_check:
        eligible = engineering_selection(
            eligible,
            windows,
            read_jsonl(output / "sources.frozen.jsonl"),
            args.max_calls,
        )
    else:
        eligible = eligible[:args.max_calls]
    gateway = gateway_for(max_tokens=args.model_max_tokens)
    semaphore = asyncio.Semaphore(args.concurrency)
    calls_used = 0
    tokens_used = 0
    try:
        for batch_start in range(0, len(eligible), args.batch_size):
            if calls_used >= args.max_calls or tokens_used >= args.max_total_tokens:
                break
            batch_status = eligible[batch_start:batch_start + min(
                args.batch_size, args.max_calls - calls_used
            )]
            results = await asyncio.gather(*(
                extract_one(gateway, windows[str(status["window_id"])], semaphore)
                for status in batch_status
            ))
            raw_rows: list[dict[str, Any]] = []
            extraction_rows: list[dict[str, Any]] = []
            failure_rows: list[dict[str, Any]] = []
            call_rows: list[dict[str, Any]] = []
            updates: dict[str, dict[str, Any]] = {}
            new_children: list[dict[str, Any]] = []
            for item, prior_status in zip(results, batch_status):
                window = item["window"]
                calls_used += len(item["call_records"])
                item_tokens = sum(int(row.get("total_tokens") or 0) for row in item["call_records"])
                tokens_used += item_tokens
                call_rows.extend({
                    **record,
                    "window_id": window["window_id"],
                    "source_id": window["source_id"],
                    "stage": "extract",
                } for record in item["call_records"])
                if item["status"] == "failed":
                    failure = {
                        "window_id": window["window_id"],
                        "source_id": window["source_id"],
                        "stage": "extract",
                        **item["error"],
                    }
                    failure_rows.append(failure)
                    updates[window["window_id"]] = {
                        "status": "failed",
                        "attempts": int(prior_status.get("attempts", 0)) + 1,
                        "last_error": failure,
                    }
                    continue
                result = item["result"]
                raw_rows.append({
                    "window_id": window["window_id"],
                    "source_id": window["source_id"],
                    "elapsed_seconds": item["elapsed_seconds"],
                    "provider_tokens": item_tokens,
                    "result": result.model_dump(mode="json"),
                })
                units, mechanical_failures = materialize_units(item)
                extraction_rows.extend(units)
                failure_rows.extend(mechanical_failures)
                valid_units = sum(row.get("unit_id") is not None for row in units)
                updates[window["window_id"]] = {
                    "status": "success" if valid_units else "no_unit",
                    "attempts": int(prior_status.get("attempts", 0)) + 1,
                    "last_error": None,
                    "raw_unit_count": len(result.units),
                    "valid_unit_count": valid_units,
                    "saturated": len(result.units) >= 2,
                }
                if len(result.units) >= 2:
                    existing_ids = set(windows) | {str(row["window_id"]) for row in new_children}
                    new_children.extend(
                        child for child in derived_children(window)
                        if str(child["window_id"]) not in existing_ids
                    )
            append_jsonl(output / "extract_raw.jsonl", raw_rows)
            append_jsonl(output / "extractions.jsonl", extraction_rows)
            append_jsonl(output / "extract_failures.jsonl", failure_rows)
            append_jsonl(output / "extract_call_records.jsonl", call_rows)
            replace_statuses(output, updates)
            if new_children:
                append_jsonl(output / "windows.derived.jsonl", new_children)
                append_jsonl(output / "window_status.jsonl", [{
                    "window_id": child["window_id"],
                    "status": "pending",
                    "attempts": 0,
                    "last_error": None,
                } for child in new_children])
                windows.update({str(child["window_id"]): child for child in new_children})
            print(
                f"extract calls={calls_used}/{args.max_calls} tokens={tokens_used}/"
                f"{args.max_total_tokens} windows={batch_start + len(batch_status)}/{len(eligible)}"
            )
            resource_errors = [
                str((item.get("error") or {}).get("message", ""))
                for item in results if item.get("status") == "failed"
            ]
            if any("Insufficient Balance" in message for message in resource_errors):
                halt = {
                    "stage": "extract",
                    "reason": "provider_resource_budget_exhausted",
                    "calls_used": calls_used,
                    "tokens_used": tokens_used,
                    "remaining_before_derived_refresh": max(
                        0, len(eligible) - batch_start - len(batch_status)
                    ),
                }
                (output / "execution_halt.json").write_text(
                    json.dumps(halt, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                print(json.dumps(halt, ensure_ascii=False), flush=True)
                break
    finally:
        await gateway.close()
    status_rows = read_jsonl(output / "window_status.jsonl")
    summary = {
        "invocation_limits": {
            "max_calls": args.max_calls,
            "max_total_tokens": args.max_total_tokens,
            "max_attempts": args.max_attempts,
            "concurrency": args.concurrency,
            "batch_size": args.batch_size,
            "engineering_check": args.engineering_check,
        },
        "this_invocation": {"calls": calls_used, "tokens": tokens_used},
        "window_status": dict(Counter(str(row["status"]) for row in status_rows)),
        "remaining": sum(row["status"] in {"pending", "failed"} for row in status_rows),
        "full_scan_complete": all(row["status"] in {"success", "no_unit"} for row in status_rows),
    }
    (output / "extract_progress.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def status(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    sources = read_jsonl(output / "sources.frozen.jsonl")
    windows = read_jsonl(output / "window_status.jsonl")
    extractions = read_jsonl(output / "extractions.jsonl")
    calls = read_jsonl(output / "extract_call_records.jsonl")
    summary = {
        "sources": len(sources),
        "source_splits": dict(Counter(str(row.get("split")) for row in sources)),
        "windows": len(windows),
        "window_status": dict(Counter(str(row.get("status")) for row in windows)),
        "valid_unit_rows": sum(row.get("unit_id") is not None for row in extractions),
        "unique_source_units": len({
            str(row["unit_id"]) for row in extractions if row.get("unit_id")
        }),
        "no_unit_rows": sum(row.get("unit_id") is None for row in extractions),
        "extract_calls": len(calls),
        "extract_tokens": sum(int(row.get("total_tokens") or 0) for row in calls),
        "full_scan_complete": bool(windows) and all(
            row.get("status") in {"success", "no_unit"} for row in windows
        ),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def unique_source_units(output: Path) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(output / "extractions.jsonl"):
        unit_id = row.get("unit_id")
        if not unit_id:
            continue
        key = str(unit_id)
        if key not in merged:
            merged[key] = {
                **row,
                "source_lineage": [{
                    "window_id": row.get("window_id"),
                    "parent_window_id": row.get("parent_window_id"),
                }],
            }
        else:
            merged[key]["source_lineage"].append({
                "window_id": row.get("window_id"),
                "parent_window_id": row.get("parent_window_id"),
            })
    return [merged[key] for key in sorted(merged)]


async def review(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    units = unique_source_units(output)
    prior_reviews = read_jsonl(output / "reviews.jsonl")
    reviewed_ids = {str(row["unit_id"]) for row in prior_reviews}
    remaining = [row for row in units if str(row["unit_id"]) not in reviewed_ids]
    gateway = gateway_for(max_tokens=args.model_max_tokens)
    calls_used = 0
    tokens_used = 0
    try:
        for batch_start in range(0, len(remaining), 5):
            if calls_used >= args.max_calls or tokens_used >= args.max_total_tokens:
                break
            batch = remaining[batch_start:batch_start + 5]
            compact = [{key: row.get(key) for key in (
                "unit_id", "window_id", "source_span", "source_user_turn",
                "character_response", "trigger_basis", "retrieval_description",
                "behavior_tags", "payload_class", "transfer_scope", "requires_all",
                "forbids_any", "audience", "source_speaker", "response_exact_match",
                "user_turn_exact_match", "response_span", "source_user_span",
                "trigger_evidence_segment_ids", "intervening_segment_ids",
                "relation_basis", "source_user_speaker", "attribution_status",
                "attribution_evidence_refs",
            )} for row in batch]
            capture = gateway.start_call_capture()
            try:
                result = await gateway.generate_json(
                    "planner",
                    [
                        ChatMessage(role="system", content=REVIEW_SYSTEM),
                        ChatMessage(role="user", content=json.dumps({
                            "candidates": compact,
                            "allowed_behavior_tags": ALLOWED_BEHAVIORS,
                            "output_schema": ReviewResult.model_json_schema(),
                            "output_rule": "只返回符合 output_schema 的对象；顶层只能有 reviews。",
                        }, ensure_ascii=False)),
                    ],
                    ReviewResult,
                )
                call_rows = gateway.finish_call_capture(capture)
            except Exception as exc:
                call_rows = gateway.finish_call_capture(capture)
                append_jsonl(output / "review_failures.jsonl", [{
                    "batch_start": batch_start,
                    "unit_ids": [row["unit_id"] for row in batch],
                    "stage": "semantic_review", "error_type": type(exc).__name__,
                    "message": str(exc)[:500],
                }])
                append_jsonl(output / "review_call_records.jsonl", [{
                    **record, "stage": "semantic_review",
                    "unit_ids": [row["unit_id"] for row in batch],
                } for record in call_rows])
                calls_used += len(call_rows)
                tokens_used += sum(int(row.get("total_tokens") or 0) for row in call_rows)
                if "Insufficient Balance" in str(exc):
                    halt = {
                        "stage": "semantic_review",
                        "reason": "provider_resource_budget_exhausted",
                        "calls_used": calls_used,
                        "tokens_used": tokens_used,
                        "remaining_units": len(remaining) - batch_start,
                    }
                    (output / "review_halt.json").write_text(
                        json.dumps(halt, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    print(json.dumps(halt, ensure_ascii=False), flush=True)
                    break
                continue
            by_id = {str(row["unit_id"]): row for row in batch}
            review_rows: list[dict[str, Any]] = []
            for item in result.reviews:
                source = by_id.get(item.unit_id)
                row = item.model_dump(mode="json")
                if source is None:
                    row["orphan_review"] = True
                else:
                    row.update({
                        "window_id": source.get("window_id"),
                        "source_lineage": source.get("source_lineage", []),
                        "review_version": "semantic-review-v1",
                    })
                review_rows.append(row)
            append_jsonl(output / "reviews.jsonl", review_rows)
            append_jsonl(output / "review_call_records.jsonl", [{
                **record, "stage": "semantic_review",
                "unit_ids": [row["unit_id"] for row in batch],
            } for record in call_rows])
            calls_used += len(call_rows)
            tokens_used += sum(int(row.get("total_tokens") or 0) for row in call_rows)
            print(
                f"review calls={calls_used}/{args.max_calls} tokens={tokens_used}/"
                f"{args.max_total_tokens} units={min(batch_start + 5, len(remaining))}/"
                f"{len(remaining)}",
                flush=True,
            )
    finally:
        await gateway.close()
    all_reviews = read_jsonl(output / "reviews.jsonl")
    report = {
        "source_units": len(units),
        "reviewed_units": len({str(row["unit_id"]) for row in all_reviews}),
        "remaining_units": len(units) - len({str(row["unit_id"]) for row in all_reviews}),
        "decisions": dict(Counter(str(row.get("decision")) for row in all_reviews)),
        "this_invocation": {"calls": calls_used, "tokens": tokens_used},
    }
    (output / "review_progress.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


def project(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    units = {str(row["unit_id"]): row for row in unique_source_units(output)}
    reviews = {
        str(row["unit_id"]): row for row in read_jsonl(output / "reviews.jsonl")
        if not row.get("orphan_review")
    }
    projected: list[dict[str, Any]] = []
    for unit_id, source in units.items():
        review_row = reviews.get(unit_id)
        decision = str(review_row.get("decision")) if review_row else "pending"
        verified = source.get("attribution_status") == "verified"
        approved = bool(review_row) and decision == "approved"
        behavior_ids = (
            list(review_row.get("corrected_behavior_tags", []))
            if review_row else list(source.get("behavior_tags", []))
        )
        requires_all = (
            list(review_row.get("corrected_requires_all", []))
            if review_row else list(source.get("requires_all", []))
        )
        transfer_scope = (
            review_row.get("corrected_transfer_scope")
            if review_row else source.get("transfer_scope")
        )
        safe_payload = (
            approved and verified and bool(behavior_ids)
            and transfer_scope in {"general", "requires_context"}
        )
        fragment_ok = (
            safe_payload
            and transfer_scope == "general"
            and review_row.get("corrected_payload_class") in {"reaction_only", "turn_local_stance"}
            and len(str(source.get("character_response", ""))) <= 80
        )
        runtime_kind = "reviewed_fragment" if fragment_ok else "pattern_only"
        q_projection = source.get("q_projection")
        if q_projection is not None:
            q_projection = {**q_projection, "review_status": "approved" if approved else decision}
        a_projection = source.get("a_projection")
        if a_projection is not None:
            a_projection = {
                **a_projection,
                "review_status": "approved" if approved else decision,
                "behavior_ids": behavior_ids,
            }
        projected.append({
            **source,
            "style_record_version": 3,
            "semantic_review": review_row,
            "semantic_review_decision": decision,
            "payload_class": (
                review_row.get("corrected_payload_class")
                if review_row else source.get("payload_class")
            ),
            "transfer_scope": transfer_scope,
            "behavior_tags": behavior_ids,
            "applicability": {
                "requires_all": requires_all,
                "forbids_any": (
                    list(review_row.get("corrected_forbids_any", []))
                    if review_row else list(source.get("forbids_any", []))
                ),
            },
            "q_projection": q_projection,
            "a_projection": a_projection,
            "projection_version": "semantic-review-projection-v1",
            "runtime_payload_kind": runtime_kind,
            "runtime_reaction_fragment": (
                source.get("character_response") if fragment_ok else None
            ),
            "runtime_payload_reviewed": safe_payload,
            "runtime_scope": ["style_runtime"] if safe_payload else [],
            "runtime_eligible": safe_payload,
        })
    write_jsonl(output / "source_reaction_units.reviewed.jsonl", projected)
    projection_counts = Counter(
        "QA" if (row.get("q_projection") or {}).get("review_status") == "approved"
        and (row.get("a_projection") or {}).get("review_status") == "approved"
        else "Q-only" if (row.get("q_projection") or {}).get("review_status") == "approved"
        else "A-only" if (row.get("a_projection") or {}).get("review_status") == "approved"
        else "none"
        for row in projected
    )
    report = {
        "source_units": len(projected),
        "review_decisions": dict(Counter(row["semantic_review_decision"] for row in projected)),
        "attribution": dict(Counter(str(row.get("attribution_status")) for row in projected)),
        "projection_status": dict(projection_counts),
        "payload_kind": dict(Counter(row["runtime_payload_kind"] for row in projected)),
        "runtime_eligible": sum(bool(row["runtime_eligible"]) for row in projected),
        "projection_schema_version": "source-reaction-unit-projection-v1",
        "projection_version": "semantic-review-projection-v1",
    }
    (output / "projection_manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


def reproject(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    backup_extractions = output / "extractions.pre_reprojection.jsonl"
    backup_status = output / "window_status.pre_reprojection.jsonl"
    if backup_extractions.exists() or backup_status.exists():
        raise FileExistsError("reprojection backup already exists")
    shutil.copy2(output / "extractions.jsonl", backup_extractions)
    shutil.copy2(output / "window_status.jsonl", backup_status)
    all_windows = [
        *read_jsonl(output / "windows.frozen.jsonl"),
        *read_jsonl(output / "windows.derived.jsonl"),
    ]
    windows = {str(row["window_id"]): row for row in all_windows}
    projected: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    updates: dict[str, dict[str, Any]] = {}
    for raw in read_jsonl(output / "extract_raw.jsonl"):
        window = windows[str(raw["window_id"])]
        result = RangeExtractionResult.model_validate(raw["result"])
        item = {
            "window": window,
            "result": result,
            "segments": {
                str(row["segment_id"]): row
                for row in segment_window(str(window["source_span"]))
            },
        }
        units, unit_failures = materialize_units(item)
        projected.extend(units)
        failures.extend(unit_failures)
        valid_units = sum(row.get("unit_id") is not None for row in units)
        updates[str(window["window_id"])] = {
            "status": "success" if valid_units else "no_unit",
            "last_error": None,
            "raw_unit_count": len(result.units),
            "valid_unit_count": valid_units,
            "saturated": len(result.units) >= 2,
            "reprojected": True,
        }
    write_jsonl(output / "extractions.jsonl", projected)
    write_jsonl(output / "reprojection_failures.jsonl", failures)
    replace_statuses(output, updates)
    report = {
        "raw_windows_reprojected": len(updates),
        "projected_rows": len(projected),
        "valid_units": sum(row.get("unit_id") is not None for row in projected),
        "remaining_mechanical_failures": len(failures),
        "historical_failures_preserved": True,
    }
    (output / "reprojection_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=["freeze", "window", "extract", "review", "project", "reproject", "status"]
    )
    parser.add_argument("--run-id", default="desktop-style-full-corpus-20260916-001")
    parser.add_argument(
        "--database", type=Path,
        default=PROJECT_ROOT / "pure_chat_desktop" / "resources" / "database" / "documents.dev.db",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-chars", type=int, default=2200)
    parser.add_argument("--overlap-chars", type=int, default=320)
    parser.add_argument("--holdout-fraction", type=float, default=0.10)
    parser.add_argument("--split-seed", default="style-full-corpus-20260916-v1")
    parser.add_argument("--max-calls", type=int, default=24)
    parser.add_argument("--max-total-tokens", type=int, default=120000)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--model-max-tokens", type=int, default=1800)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=6)
    parser.add_argument("--retry-failures", action="store_true")
    parser.add_argument("--engineering-check", action="store_true")
    args = parser.parse_args()
    if args.target_chars <= args.overlap_chars:
        parser.error("target-chars must be greater than overlap-chars")
    if not 0 < args.holdout_fraction < 0.5:
        parser.error("holdout-fraction must be between 0 and 0.5")
    if min(args.max_calls, args.max_total_tokens, args.max_attempts, args.concurrency, args.batch_size) <= 0:
        parser.error("execution limits must be positive")
    return args


if __name__ == "__main__":
    options = parse_args()
    if options.command == "freeze":
        freeze(options)
    elif options.command == "window":
        make_windows(options)
    elif options.command == "extract":
        asyncio.run(extract(options))
    elif options.command == "review":
        asyncio.run(review(options))
    elif options.command == "project":
        project(options)
    elif options.command == "reproject":
        reproject(options)
    else:
        status(options)
