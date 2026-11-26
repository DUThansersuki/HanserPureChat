from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from hanser_agent.agent.context_builder import ContextBuilder
from hanser_agent.agent.planner import DialoguePlanner
from hanser_agent.agent.tools.style_search import StyleSearchTool
from hanser_agent.config import load_settings
from hanser_agent.model_gateway import ModelGateway
from hanser_agent.models import ChatMessage
from hanser_agent.persona.compiler import PersonaCompiler
from hanser_agent.persona.data_pipeline import style_row_to_model
from hanser_agent.persona.policy import build_guidance
from hanser_agent.persona.signals import build_turn_signals
from hanser_agent.responder.service import HanserResponder
from hanser_agent.responder.validator import StyleValidator


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


async def run(output: Path, cases_name: str, result_name: str) -> None:
    output = output.resolve()
    settings = load_settings(PROJECT_ROOT / "backend" / "config.yml")
    settings.db_path = output / "candidate_pilot.db"
    gateway = ModelGateway({
        "planner": replace(settings.planner, temperature=0.0, max_tokens=3000),
        "responder": replace(settings.responder, temperature=0.0),
    })
    planner = DialoguePlanner(gateway)
    persona_dir = BACKEND_ROOT / "hanser_agent" / "prompts" / "persona" / "candidates" / "hanser-persona-v2-candidate"
    compiler = PersonaCompiler(persona_dir, settings_lifecycle="preview")
    if compiler.effective_settings is None:
        raise ValueError("persona effective settings unavailable")
    generation = json.loads((output / "candidate_manifest.json").read_text(encoding="utf-8"))["generation"]
    style_tool = StyleSearchTool(settings=settings, strict_v2=True, pinned_generation=generation)
    builder = ContextBuilder(
        compiler,
        settings.context,
        provider_context_window=settings.responder.context_window,
        provider_max_output_tokens=settings.responder.max_tokens,
    )
    responder = HanserResponder(
        gateway,
        StyleValidator(persona_dir / "style_constraints.yaml"),
        profile_name="responder",
        structured_performance_enabled=False,
    )
    units = {row["database_id"]: row["unit_id"] for row in read_jsonl(output / "approved_units.jsonl")}
    con = sqlite3.connect(output / "candidate_pilot.db")
    con.row_factory = sqlite3.Row
    results: list[dict[str, object]] = []
    try:
        for case in read_jsonl(output / cases_name):
            message = str(case["message"])
            history = [ChatMessage.model_validate(item) for item in case.get("history", [])]
            plan = await planner.plan(message, history, None)
            signals = build_turn_signals(
                message,
                current_message_ref=f"unseen:{case['case_id']}",
                planner_payload=plan.persona_signals,
                history_messages=history,
            )
            behavior = build_guidance(
                signals,
                {},
                None,
                compiler.effective_settings,
                response_mode=plan.response_mode,
                fact_sensitivity=plan.fact_sensitivity,
                need_wiki=plan.need_wiki,
                behavior_priors=compiler.behavior_priors,
                current_message=message,
            )
            search = await style_tool.search(
                message,
                plan,
                turn_signals=signals,
                behavior_decision=behavior,
            )
            styles = []
            for item in search.examples[:1]:
                row = con.execute("SELECT * FROM style_examples WHERE id=?", (int(item.id.split(":", 1)[1]),)).fetchone()
                styles.append(style_row_to_model(row))
            bundle = builder.build(
                current_message=message,
                history=history,
                plan=plan,
                wiki_evidence=[],
                style_examples=styles,
                memories=[],
                turn_signals=signals,
                behavior_decision=behavior,
                effective_persona=compiler.effective_settings,
            )
            generated = await responder.respond(bundle, structured_performance=False)
            predicted = {
                name: observation.value
                for name, observation in signals.values.items()
                if observation.status == "observed" and observation.value is not None
            }
            selected_units = [units.get(item.id.split(":", 1)[1], f"unknown:{item.id}") for item in search.examples]
            results.append({
                **case,
                "predicted_signals": predicted,
                "selected_unit_ids": selected_units,
                "selector_empty_reason": search.empty_reason,
                "response": generated.text,
                "response_attempts": generated.attempts,
                "generation_status": generated.generation_status,
                "validator_actions": generated.validator_actions,
                "estimated_input_tokens": bundle.estimated_input_tokens,
            })
            print(f"unseen {case['case_id']} style={selected_units}")
    finally:
        con.close()
        await gateway.close()
    write_jsonl(output / result_name, results)
    write_jsonl(output / f"{Path(result_name).stem}.call_records.jsonl", gateway.call_records)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", default="cases.unseen.v1.jsonl")
    parser.add_argument("--result", default="unseen_results.jsonl")
    args = parser.parse_args()
    asyncio.run(run(args.output, args.cases, args.result))


if __name__ == "__main__":
    main()
