from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
PILOT = ROOT / "audit_artifacts" / "desktop_style_pilot_20260914_001"
REPAIR = ROOT / "audit_artifacts" / "desktop_style_pilot_20260914_003"
OUTPUT = PILOT / "manual_review_90"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def predicate_text(items: list[dict[str, Any]]) -> str:
    if not items:
        return "[]"
    return "; ".join(
        f"{item.get('field')} in {json.dumps(item.get('one_of', []), ensure_ascii=False)}"
        for item in items
    )


def quote(value: Any, *, missing: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        text = missing
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


def json_cell(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def match_superseded(
    approved: dict[str, Any],
    superseded: list[dict[str, Any]],
) -> dict[str, Any] | None:
    candidates = [
        item for item in superseded if item.get("window_id") == approved.get("window_id")
    ]
    start = approved.get("source_start_char")
    if start is not None:
        exact = [item for item in candidates if item.get("response_source_start_char") == start]
        if len(exact) == 1:
            return exact[0]
    response = str(approved.get("character_response") or "")
    containing = [
        item for item in candidates
        if response and response in str(item.get("character_response") or "")
    ]
    return containing[0] if len(containing) == 1 else None


def main() -> None:
    extractions = [
        row for row in read_jsonl(PILOT / "extractions.jsonl") if row.get("unit_id")
    ]
    reviews = {row["unit_id"]: row for row in read_jsonl(PILOT / "reviews.jsonl")}
    adjudications = {
        row["unit_id"]: row for row in read_jsonl(PILOT / "final_adjudication.jsonl")
    }
    initial_approved = read_jsonl(PILOT / "approved_units.jsonl")
    repair_decisions = {
        row["parent_unit_id"]: row
        for row in read_jsonl(REPAIR / "adjudication_v2.jsonl")
    }
    superseded_ids = {
        unit_id
        for unit_id, row in adjudications.items()
        if row.get("final_decision") == "superseded_by_agent_correction"
    }
    superseded_rows = [row for row in extractions if row["unit_id"] in superseded_ids]

    records: list[dict[str, Any]] = []
    for extraction in extractions:
        unit_id = extraction["unit_id"]
        if unit_id in superseded_ids:
            continue
        review = reviews[unit_id]
        final = adjudications[unit_id]
        records.append({
            "unit_id": unit_id,
            "replaces_unit_id": None,
            "window_id": extraction.get("window_id"),
            "document_id": extraction.get("document_id"),
            "filename": extraction.get("filename"),
            "sampling_stratum": extraction.get("sampling_stratum"),
            "source_speaker": extraction.get("source_speaker"),
            "source_user_turn": extraction.get("source_user_turn"),
            "character_response": extraction.get("character_response"),
            "trigger_basis": extraction.get("trigger_basis"),
            "retrieval_description": extraction.get("retrieval_description"),
            "behavior_tags": review.get("corrected_behavior_tags", extraction.get("behavior_tags", [])),
            "payload_class": review.get("corrected_payload_class", extraction.get("payload_class")),
            "transfer_scope": review.get("corrected_transfer_scope", extraction.get("transfer_scope")),
            "requires_all": review.get("corrected_requires_all", extraction.get("requires_all", [])),
            "forbids_any": review.get("corrected_forbids_any", extraction.get("forbids_any", [])),
            "source_span": extraction.get("source_span"),
            "source_start_char": extraction.get("response_source_start_char"),
            "source_end_char": extraction.get("response_source_end_char"),
            "source_user_turn_exact": extraction.get("user_turn_exact_match"),
            "character_response_exact": extraction.get("response_exact_match"),
            "semantic_review_decision": review.get("decision"),
            "initial_final_gate": final.get("final_decision"),
            "latest_repair_decision": None,
            "current_runtime_status": (
                "excluded_requires_context"
                if final.get("final_decision") == "requires_context"
                else "rejected"
            ),
            "source_or_speaker_error": review.get("source_or_speaker_error"),
            "mixed_events": review.get("mixed_events"),
            "reaction_truncated": review.get("reaction_truncated"),
            "needs_wider_window": review.get("needs_wider_window"),
            "applicability_issue": review.get("applicability_issue"),
            "unsafe_transfer": review.get("unsafe_transfer"),
            "review_basis": review.get("review_basis"),
            "final_basis": final.get("basis"),
        })

    for approved in initial_approved:
        repair = repair_decisions.get(approved["unit_id"])
        old = match_superseded(approved, superseded_rows)
        old_review = reviews.get(old["unit_id"], {}) if old else {}
        latest = repair.get("decision") if repair else "approved"
        records.append({
            "unit_id": approved["unit_id"],
            "replaces_unit_id": old.get("unit_id") if old else None,
            "window_id": approved.get("window_id"),
            "document_id": approved.get("document_id"),
            "filename": approved.get("filename"),
            "sampling_stratum": old.get("sampling_stratum") if old else None,
            "source_speaker": approved.get("source_speaker"),
            "source_user_turn": approved.get("source_user_turn"),
            "character_response": approved.get("character_response"),
            "trigger_basis": approved.get("trigger_basis"),
            "retrieval_description": approved.get("retrieval_description"),
            "behavior_tags": approved.get("behavior_tags", []),
            "payload_class": approved.get("payload_class"),
            "transfer_scope": approved.get("transfer_scope"),
            "requires_all": approved.get("requires_all", []),
            "forbids_any": approved.get("forbids_any", []),
            "source_span": approved.get("source_span"),
            "source_start_char": approved.get("source_start_char"),
            "source_end_char": approved.get("source_end_char"),
            "source_user_turn_exact": True,
            "character_response_exact": True,
            "semantic_review_decision": old_review.get("decision") if old else "agent_supplemented",
            "initial_final_gate": "approved",
            "latest_repair_decision": latest,
            "current_runtime_status": latest,
            "source_or_speaker_error": old_review.get("source_or_speaker_error", False),
            "mixed_events": old_review.get("mixed_events", False),
            "reaction_truncated": old_review.get("reaction_truncated", False),
            "needs_wider_window": old_review.get("needs_wider_window", False),
            "applicability_issue": old_review.get("applicability_issue", "none"),
            "unsafe_transfer": old_review.get("unsafe_transfer", False),
            "review_basis": approved.get("review_basis"),
            "final_basis": repair.get("rationale") if repair else approved.get("review_basis"),
        })

    status_order = {"approved": 0, "pending": 1, "excluded_requires_context": 2, "rejected": 3}
    records.sort(key=lambda row: (
        status_order.get(str(row["current_runtime_status"]), 9),
        str(row.get("window_id") or ""),
        str(row["unit_id"]),
    ))
    if len(records) != 90:
        raise ValueError(f"expected 90 final candidate units, got {len(records)}")
    if len({row["unit_id"] for row in records}) != 90:
        raise ValueError("final candidate unit ids are not unique")

    for index, row in enumerate(records, start=1):
        row["review_index"] = index

    OUTPUT.mkdir(parents=True, exist_ok=True)
    jsonl_path = OUTPUT / "reaction_units_90_review.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for row in records:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    csv_fields = [
        "review_index", "unit_id", "replaces_unit_id", "current_runtime_status",
        "window_id", "filename", "source_speaker", "source_user_turn",
        "character_response", "retrieval_description", "behavior_tags", "payload_class",
        "transfer_scope", "trigger_basis", "requires_all", "forbids_any",
        "source_user_turn_exact", "character_response_exact", "semantic_review_decision",
        "initial_final_gate", "latest_repair_decision", "source_or_speaker_error",
        "mixed_events", "applicability_issue", "unsafe_transfer", "review_basis",
        "final_basis",
    ]
    with (OUTPUT / "reaction_units_90_review.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields)
        writer.writeheader()
        for row in records:
            flat = {key: row.get(key) for key in csv_fields}
            for key in ("behavior_tags", "requires_all", "forbids_any"):
                flat[key] = json_cell(flat[key])
            writer.writerow(flat)

    status_counts = Counter(str(row["current_runtime_status"]) for row in records)
    initial_counts = Counter(str(row["initial_final_gate"]) for row in records)
    lines = [
        "# Style v3 Pilot：90 个最终候选单元人工 Review 视图",
        "",
        "本文件将首轮 87 个模型抽取单元中的 9 个被纠正旧版本移除，再加入 12 个逐字修正/补回单元，形成去重后的 90 个最终候选。Q/A 默认保持记录中的原文；Q 缺失或未核实时明确标记，不用 retrieval description 冒充真实弹幕。",
        "",
        "## 汇总",
        "",
        "| 口径 | 状态 | 数量 |",
        "|---|---|---:|",
        f"| 首轮最终门禁 | approved | {initial_counts['approved']} |",
        f"| 首轮最终门禁 | requires_context | {initial_counts['requires_context']} |",
        f"| 首轮最终门禁 | rejected | {initial_counts['rejected']} |",
        f"| 最新 runtime 复审 | approved | {status_counts['approved']} |",
        f"| 最新 runtime 复审 | pending | {status_counts['pending']} |",
        f"| 最新 runtime 复审 | excluded_requires_context | {status_counts['excluded_requires_context']} |",
        f"| 最新 runtime 复审 | rejected | {status_counts['rejected']} |",
        "",
        "说明：`excluded_requires_context` 是首轮判为 requires_context、但没有进入后续 12 条 runtime 候选复审的单元；它不等于永久拒绝。`pending` 是原 12 条中经第二轮复审暂缓注入的单元。",
        "",
    ]

    section_names = {
        "approved": "A. 当前 approved（6 条）",
        "pending": "B. 当前 pending（5 条）",
        "excluded_requires_context": "C. 首轮 requires_context、未进入 runtime（14 条）",
        "rejected": "D. 当前 rejected（65 条）",
    }
    for status in ("approved", "pending", "excluded_requires_context", "rejected"):
        lines.extend([f"## {section_names[status]}", ""])
        for row in (item for item in records if item["current_runtime_status"] == status):
            q_exact = row.get("source_user_turn_exact")
            a_exact = row.get("character_response_exact")
            source = f"{row.get('filename')} / {row.get('window_id')}"
            if row.get("source_start_char") is not None:
                source += f" / chars {row.get('source_start_char')}–{row.get('source_end_char')}"
            lines.extend([
                f"### {row['review_index']:02d}. `{row['unit_id']}` — {status}",
                "",
                f"- 来源：{source}",
                f"- 替代旧单元：`{row.get('replaces_unit_id')}`" if row.get("replaces_unit_id") else "- 替代旧单元：无",
                f"- 说话人记录：`{row.get('source_speaker')}`",
                f"- Q 逐字状态：`{q_exact}`；A 逐字状态：`{a_exact}`；trigger：`{row.get('trigger_basis')}`",
                f"- Style 标签：`{', '.join(row.get('behavior_tags') or []) or '[]'}`",
                f"- payload / transfer：`{row.get('payload_class')}` / `{row.get('transfer_scope')}`",
                f"- requires_all：{predicate_text(row.get('requires_all') or [])}",
                f"- forbids_any：{predicate_text(row.get('forbids_any') or [])}",
                f"- 裁决链：semantic=`{row.get('semantic_review_decision')}` → first_gate=`{row.get('initial_final_gate')}` → repair=`{row.get('latest_repair_decision')}`",
                f"- 风险：source_or_speaker_error=`{row.get('source_or_speaker_error')}`；mixed_events=`{row.get('mixed_events')}`；applicability=`{row.get('applicability_issue')}`；unsafe_transfer=`{row.get('unsafe_transfer')}`",
                "",
                "**处理后 Q（仅 source_user_turn）：**",
                "",
                quote(row.get("source_user_turn"), missing="[缺失或不可核验；未用推断场景补写]"),
                "",
                "**处理后 A（候选 character_response）：**",
                "",
                quote(row.get("character_response"), missing="[缺失]"),
                "",
                "**检索场景描述：**",
                "",
                quote(row.get("retrieval_description"), missing="[缺失]"),
                "",
                "**审核依据 / 当前结论：**",
                "",
                quote(row.get("final_basis") or row.get("review_basis"), missing="[未记录]"),
                "",
            ])

    (OUTPUT / "reaction_units_90_review.md").write_text(
        "\n".join(lines).rstrip() + "\n", encoding="utf-8"
    )
    summary = {
        "final_candidate_units": len(records),
        "initial_final_gate": dict(sorted(initial_counts.items())),
        "current_runtime_status": dict(sorted(status_counts.items())),
        "deduplication": {
            "model_extractions": len(extractions),
            "superseded_model_units_removed": len(superseded_ids),
            "corrected_or_supplemented_units_added": len(initial_approved),
        },
        "artifacts": [
            "reaction_units_90_review.md",
            "reaction_units_90_review.csv",
            "reaction_units_90_review.jsonl",
        ],
    }
    (OUTPUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
