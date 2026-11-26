from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from hanser_agent.agent.context_builder import ContextBuilder
from hanser_agent.config import load_settings
from hanser_agent.model_gateway import ModelGateway
from hanser_agent.models import ChatMessage, DialoguePlan
from hanser_agent.persona.compiler import PersonaCompiler
from hanser_agent.persona.data_pipeline import style_row_to_model
from hanser_agent.persona.policy import build_guidance
from hanser_agent.persona.schemas import TurnSignals
from hanser_agent.responder.service import HanserResponder
from hanser_agent.responder.validator import StyleValidator

CASE_IDS = [
    "G01-guess-pos", "G02-vent-explicit", "G02-vent-implicit", "G05-exact-praise",
    "G07-assistant-corrected", "G11-user-achievement", "G11-ambiguous", "G11-thirdparty-achievement",
]


class BlindRating(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    preference: Literal["A", "B", "tie", "both_bad"]
    style_naturalness: Literal["A", "B", "tie"]
    risk_flags_a: list[Literal["copying", "fact_transfer", "too_strong", "target_mismatch", "unsupported_action", "none"]] = Field(default_factory=list)
    risk_flags_b: list[Literal["copying", "fact_transfer", "too_strong", "target_mismatch", "unsupported_action", "none"]] = Field(default_factory=list)
    rationale: str = Field(max_length=400)


class BlindRatings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ratings: list[BlindRating]


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


async def run(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    settings = load_settings(PROJECT_ROOT / "backend" / "config.yml")
    settings.db_path = output / "candidate_pilot.db"
    gateway = ModelGateway({
        "planner": replace(settings.planner, temperature=0.0, max_tokens=3000),
        "responder": replace(settings.responder, temperature=0.0),
    })
    persona_dir = BACKEND_ROOT / "hanser_agent" / "prompts" / "persona" / "candidates" / "hanser-persona-v2-candidate"
    compiler = PersonaCompiler(persona_dir, settings_lifecycle="preview")
    if compiler.effective_settings is None:
        raise ValueError("persona effective settings unavailable")
    builder = ContextBuilder(compiler, settings.context, provider_context_window=settings.responder.context_window, provider_max_output_tokens=settings.responder.max_tokens)
    responder = HanserResponder(gateway, StyleValidator(persona_dir / "style_constraints.yaml"), profile_name="responder", structured_performance_enabled=False)
    replay = {row["case_id"]: row for row in read_jsonl(output / "signal_replay_results.jsonl") if row["signal_mode"] == "predicted_wired"}
    cases = {row["case_id"]: row for row in read_jsonl(output / "cases.dev.v2.jsonl")}
    units = {row["unit_id"]: row for row in read_jsonl(output / "approved_units.jsonl")}
    con = sqlite3.connect(output / "candidate_pilot.db")
    con.row_factory = sqlite3.Row
    raw: list[dict[str, object]] = []
    blind: list[dict[str, object]] = []
    key: list[dict[str, str]] = []
    try:
        for case_id in CASE_IDS:
            case, fixed = cases[case_id], replay[case_id]
            plan = DialoguePlan.model_validate(fixed["plan"])
            signals = TurnSignals.model_validate(fixed["signals"])
            behavior = build_guidance(
                signals, {}, None, compiler.effective_settings,
                response_mode=plan.response_mode, fact_sensitivity=plan.fact_sensitivity,
                need_wiki=plan.need_wiki, behavior_priors=compiler.behavior_priors,
                current_message=str(case["message"]),
            )
            selected_ids = list(fixed["selected_unit_ids"][:1])
            styles = []
            for unit_id in selected_ids:
                row = con.execute("SELECT * FROM style_examples WHERE id=?", (int(units[unit_id]["database_id"]),)).fetchone()
                styles.append(style_row_to_model(row))
            history = [ChatMessage.model_validate(item) for item in case.get("history", [])]
            bundles = {
                "N": builder.build(current_message=case["message"], history=history, plan=plan, wiki_evidence=[], style_examples=[], memories=[], turn_signals=signals, behavior_decision=behavior, effective_persona=compiler.effective_settings),
                "C": builder.build(current_message=case["message"], history=history, plan=plan, wiki_evidence=[], style_examples=styles, memories=[], turn_signals=signals, behavior_decision=behavior, effective_persona=compiler.effective_settings),
            }
            answers = {}
            for variant, bundle in bundles.items():
                generated = await responder.respond(bundle, structured_performance=False)
                answers[variant] = {
                    "text": generated.text,
                    "estimated_input_tokens": bundle.estimated_input_tokens,
                    "style_ids": bundle.injected_style_ids,
                    "attempts": generated.attempts,
                    "generation_status": generated.generation_status,
                    "validator_actions": generated.validator_actions,
                }
            swap = int(hashlib.sha256(case_id.encode()).hexdigest(), 16) % 2 == 1
            mapping = {"A": "C", "B": "N"} if swap else {"A": "N", "B": "C"}
            raw.append({"case_id": case_id, "message": case["message"], "selected_unit_ids": selected_ids, "fixed_plan": fixed["plan"], "fixed_signals": fixed["signals"], "N": answers["N"], "C": answers["C"]})
            blind.append({"case_id": case_id, "message": case["message"], "history": case.get("history", []), "risk_tags": case.get("risk_tags", []), "answer_A": answers[mapping["A"]]["text"], "answer_B": answers[mapping["B"]]["text"]})
            key.append({"case_id": case_id, **mapping})
            print(f"N/C {case_id} style={selected_ids}")
        write_jsonl(output / "responder_nc_results.v2.jsonl", raw)
        write_jsonl(output / "responder_blind_inputs.jsonl", blind)
        write_jsonl(output / "responder_blind_key.jsonl", key)
        rating = await gateway.generate_json(
            "planner",
            [ChatMessage(role="system", content="你是盲化回答质量审核员。不知道 A/B 对应哪个系统。根据自然接话、对象正确、不增加无依据事实或行动承诺逐例评审。允许 both_bad。严格返回 JSON 对象。"), ChatMessage(role="user", content=json.dumps({"cases": blind, "output_schema": BlindRatings.model_json_schema()}, ensure_ascii=False))],
            BlindRatings,
        )
    finally:
        con.close()
        await gateway.close()
    ratings = [item.model_dump(mode="json") for item in rating.ratings]
    revealed = []
    key_by_id = {row["case_id"]: row for row in key}
    for item in ratings:
        mapping = key_by_id[item["case_id"]]
        pref = item["preference"]
        item["preferred_variant"] = mapping[pref] if pref in {"A", "B"} else pref
        revealed.append(item)
    write_jsonl(output / "responder_blind_ratings.jsonl", revealed)
    call_path = output / "responder_call_records.v2.jsonl"
    prior_calls = read_jsonl(call_path) if call_path.exists() else []
    write_jsonl(output / "responder_call_records.latest.jsonl", gateway.call_records)
    write_jsonl(call_path, [*prior_calls, *gateway.call_records])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
