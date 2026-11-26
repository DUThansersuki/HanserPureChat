from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
import sys
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from hanser_agent.persona.schemas import SignalObservation, TurnSignals
from hanser_agent.persona.style_applicability import (
    has_specific_behavior_applicability,
    style_applicability_exclusion_reason,
)
from hanser_agent.retrieval.style_lexical import StyleLexicalIndex


CASES = [
    ("low_topic_same_interaction", "虽然不是同一件事 但我终于做成了", ["acknowledge_shared_detail"], {"dialogue_function": "share", "interaction_frame": "achievement_share", "interaction_target": "current_user", "user_emotion": "positive"}),
    ("same_topic_wrong_reaction", "这件事把我难受坏了", ["acknowledge_specific_distress"], {"dialogue_function": "share", "interaction_frame": "other", "interaction_target": "current_user", "user_emotion": "negative"}),
    ("multiple_valid_reactions", "我今天总算搞定啦", ["acknowledge_shared_detail", "brief_contextual_uptake"], {"dialogue_function": "share", "interaction_frame": "achievement_share", "interaction_target": "current_user", "user_emotion": "positive"}),
    ("low_confidence_plan", "大概还行吧", [], {}),
    ("a_only_no_q", "……", ["listen_without_forcing_advice"], {"dialogue_function": "share", "interaction_target": "current_user", "user_emotion": "negative"}),
    ("self_initiated_meta", "你刚才那个反应还挺有意思", ["brief_contextual_uptake"], {"dialogue_function": "other", "interaction_target": "assistant"}),
    ("should_empty", "请只回答精确的数据库迁移步骤", [], {"dialogue_function": "advice", "interaction_target": "assistant"}),
    ("target_mismatch", "我是在夸第三个人", ["brief_contextual_uptake"], {"dialogue_function": "share", "interaction_frame": "praise", "interaction_target": "third_party"}),
    ("applicability_conflict", "这次是很严肃的指责", ["playful_reframe"], {"dialogue_function": "correct", "interaction_frame": "serious_accusation", "interaction_target": "assistant", "playful_frame": False}),
    ("correction", "不是那样 事实是另一回事", ["update_corrected_premise", "premise_correction"], {"dialogue_function": "correct", "interaction_target": "assistant"}),
    ("closing", "那今天先到这里", ["concise_closing", "natural_close"], {"dialogue_function": "end", "interaction_target": "assistant"}),
    ("playful_frame", "哈哈我就是逗你的", ["playful_reframe", "light_contextual_tease"], {"dialogue_function": "playful", "interaction_frame": "playful_blame", "interaction_target": "assistant", "playful_frame": True}),
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def signals(values: dict[str, bool | str]) -> TurnSignals:
    return TurnSignals(values={
        name: SignalObservation(
            name=name, value=value, source="rule",
            confidence="high", status="observed", evidence_refs=["case:current"],
        )
        for name, value in values.items()
    })


def compatible(unit: dict[str, Any], current: dict[str, bool | str]) -> bool:
    projection = unit.get("a_projection") or {}
    for field, expected in projection.get("compatibility_features", {}).items():
        actual = current.get(field)
        if actual is not None and actual != expected:
            return False
    return True


def applicability_record(unit: dict[str, Any]) -> dict[str, Any]:
    """Use reviewed v3 predicates for Source Units instead of the legacy default."""
    review = unit.get("semantic_review") or {}
    return {**unit, "style_record_version": 3, "applicability": {
        "requires_all": review.get("applicability_requires_all", []),
        "forbids_any": review.get("applicability_forbids_any", []),
    }}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--units", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()
    units = [row for row in read_jsonl(args.units) if row.get("runtime_eligible")]
    units = [applicability_record(row) for row in units]
    by_id = {str(row["unit_id"]): row for row in units}
    index = StyleLexicalIndex.build({
        unit_id: {
            "trigger_text": str(unit.get("source_user_turn") or ""),
            "retrieval_description": str((unit.get("q_projection") or {}).get("scene_description") or ""),
        }
        for unit_id, unit in by_id.items()
        if (unit.get("q_projection") or {}).get("review_status") == "approved"
    })
    rows: list[dict[str, Any]] = []
    for case_id, message, options, signal_values in CASES:
        current_signals = signals(signal_values)
        eligible = {
            unit_id: unit for unit_id, unit in by_id.items()
            if style_applicability_exclusion_reason(unit, current_signals)[0] is None
        }
        q_scores = index.score(message)
        q = [
            unit_id for unit_id, match in sorted(
                q_scores.items(), key=lambda pair: (-pair[1].raw_score, pair[0])
            )
            if unit_id in eligible and match.raw_score > 0
        ][:args.top_k]
        weights = {option: 1.0 - index * 0.1 for index, option in enumerate(options)}
        a_scored = []
        for unit_id, unit in eligible.items():
            projection = unit.get("a_projection") or {}
            if (
                projection.get("review_status") != "approved"
                or not compatible(unit, signal_values)
                or not has_specific_behavior_applicability(unit)
            ):
                continue
            score = sum(weights.get(value, 0.0) for value in projection.get("behavior_ids", []))
            if score > 0:
                a_scored.append((score, unit_id))
        a = [unit_id for _, unit_id in sorted(a_scored, key=lambda pair: (-pair[0], pair[1]))][ : args.top_k]
        qa: list[str] = []
        for offset in range(max(len(q), len(a))):
            for route in (q, a):
                if offset < len(route) and route[offset] not in qa:
                    qa.append(route[offset])
                if len(qa) >= args.top_k:
                    break
            if len(qa) >= args.top_k:
                break
        rows.append({
            "case_id": case_id, "message": message,
            "fixed_behavior_options": weights, "signals": signal_values,
            "Q": q, "A": a, "QA": qa,
            "payloads_differ": len({tuple(q), tuple(a), tuple(qa)}) > 1,
            "applicable_candidates": len(eligible),
        })
    summary = {
        "source_library": len(units), "top_k": args.top_k,
        "cases": len(rows),
        "different_payload_cases": sum(row["payloads_differ"] for row in rows),
        "q_nonempty": sum(bool(row["Q"]) for row in rows),
        "a_nonempty": sum(bool(row["A"]) for row in rows),
        "qa_union_complementary": sum(
            bool(set(row["QA"]) - set(row["Q"])) and bool(set(row["QA"]) - set(row["A"]))
            for row in rows
        ),
        "generation_runs": 0,
        "assumed_units_included": 0,
        "diagnostic_scope": "mechanism-only partial reviewed library",
        "applicability_contract": "v3 reviewed predicates; no implicit legacy bypass for Source Units",
    }
    args.output.write_text(
        json.dumps({"summary": summary, "cases": rows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
