from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from hanser_agent.agent.planner import DialoguePlanner
from hanser_agent.agent.tools.style_search import StyleSearchTool
from hanser_agent.config import load_settings
from hanser_agent.model_gateway import ModelGateway
from hanser_agent.models import ChatMessage, DialoguePlan
from hanser_agent.persona.compiler import PersonaCompiler
from hanser_agent.persona.policy import build_guidance
from hanser_agent.persona.signals import build_turn_signals

def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


async def run(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    generation = json.loads((output / "candidate_manifest.json").read_text(encoding="utf-8"))["generation"]
    settings = load_settings(PROJECT_ROOT / "backend" / "config.yml")
    settings.db_path = output / "candidate_pilot.db"
    gateway = ModelGateway({"planner": replace(settings.planner, temperature=0.0)})
    planner = DialoguePlanner(gateway)
    persona_dir = BACKEND_ROOT / "hanser_agent" / "prompts" / "persona" / "candidates" / "hanser-persona-v2-candidate"
    compiler = PersonaCompiler(persona_dir, settings_lifecycle="preview")
    if compiler.effective_settings is None:
        raise ValueError("persona effective settings unavailable")
    style_tool = StyleSearchTool(settings=settings, strict_v2=True, pinned_generation=generation)
    units = {row["unit_id"]: row for row in read_jsonl(output / "approved_units.jsonl")}
    db_to_unit = {f"style:{row['database_id']}": unit_id for unit_id, row in units.items()}
    results: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    prior_path = output / "signal_replay_results.jsonl"
    prior_plans = {
        row["case_id"]: row["plan"] for row in read_jsonl(prior_path)
        if row.get("signal_mode") == "predicted_wired"
    } if prior_path.exists() and not args.fresh_planner else {}
    try:
        for index, case in enumerate(read_jsonl(output / "cases.dev.v2.jsonl"), start=1):
            message = str(case["message"])
            history = [ChatMessage.model_validate(item) for item in case.get("history", [])]
            try:
                plan = (
                    DialoguePlan.model_validate(prior_plans[case["case_id"]])
                    if case["case_id"] in prior_plans
                    else await planner.plan(message, history, None)
                )
                payloads = {
                    "predicted_legacy": {key: value for key, value in plan.persona_signals.items() if key not in {"interaction_frame", "interaction_target"}},
                    "oracle": dict(case["oracle_signals"]),
                    "predicted_wired": dict(plan.persona_signals),
                }
                for signal_mode, payload in payloads.items():
                    signals = build_turn_signals(message, current_message_ref=f"repair:{case['case_id']}", planner_payload=payload)
                    behavior = build_guidance(
                        signals, {}, None, compiler.effective_settings,
                        response_mode=plan.response_mode, fact_sensitivity=plan.fact_sensitivity,
                        need_wiki=plan.need_wiki, behavior_priors=compiler.behavior_priors,
                        current_message=message,
                    )
                    search = await style_tool.search(message, plan, turn_signals=signals, behavior_decision=behavior)
                    selected = [db_to_unit.get(item.id, f"unknown:{item.id}") for item in search.examples]
                    acceptable = set(str(item) for item in case["acceptable_unit_ids"])
                    target = case.get("target_unit_id")
                    results.append({
                        "case_id": case["case_id"], "cluster_id": case["cluster_id"], "signal_mode": signal_mode,
                        "message": message, "target_unit_id": target, "target_applicability": case["target_applicability"],
                        "acceptable_unit_ids": sorted(acceptable), "expect_empty": case.get("expect_empty"),
                        "plan": plan.model_dump(mode="json"), "signals": signals.model_dump(mode="json"),
                        "selected_unit_ids": selected, "selected_database_ids": [item.id for item in search.examples],
                        "target_selected": bool(target and target in selected),
                        "acceptable_hit": bool(acceptable.intersection(selected)),
                        "unacceptable_admissions": [item for item in selected if item not in acceptable],
                        "returned_empty": not selected, "empty_reason": search.empty_reason,
                        "target_exclusion": search.excluded.get(f"style:{units[target]['database_id']}") if target else None,
                        "excluded": search.excluded, "candidate_counts": search.candidate_counts,
                    })
            except Exception as exc:
                failures.append({"case_id": case["case_id"], "error": type(exc).__name__, "message": str(exc)[:500]})
            print(f"selector {index}/30 {case['case_id']}")
    finally:
        await gateway.close()
    write_jsonl(output / "signal_replay_results.jsonl", results)
    write_jsonl(output / "selector_failures.jsonl", failures)
    call_path = output / "planner_call_records.jsonl"
    prior_calls = read_jsonl(call_path) if call_path.exists() else []
    if gateway.call_records:
        write_jsonl(call_path, [*prior_calls, *gateway.call_records])
    summary: dict[str, object] = {"cases": 30, "failures": len(failures), "modes": {}}
    for mode in ("predicted_legacy", "oracle", "predicted_wired"):
        rows = [row for row in results if row["signal_mode"] == mode]
        expected_nonempty = [row for row in rows if row["acceptable_unit_ids"]]
        checked_empty = [row for row in rows if row["expect_empty"] is True]
        summary["modes"][mode] = {
            "rows": len(rows),
            "target_hits": sum(bool(row["target_selected"]) for row in rows),
            "acceptable_hits": sum(bool(row["acceptable_hit"]) for row in expected_nonempty),
            "expected_nonempty": len(expected_nonempty),
            "unacceptable_admissions": sum(len(row["unacceptable_admissions"]) for row in rows),
            "correct_empty": sum(bool(row["returned_empty"]) for row in checked_empty),
            "checked_empty": len(checked_empty),
            "returned_empty": sum(bool(row["returned_empty"]) for row in rows),
            "target_exclusions": dict(Counter(str(row["target_exclusion"]) for row in rows if row["target_exclusion"])),
        }
    (output / "selector_summary.v2.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fresh-planner", action="store_true")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
