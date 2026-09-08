"""External-only targeted replay over previously retrieved Style candidates.

The script applies the current static runtime guards to frozen prior retrievals.
Filtered slots are intentionally not backfilled because doing so would require a
new query embedding. Results are replay evidence, not full end-to-end retrieval.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from hanser_agent.agent.context_builder import ContextBuilder  # noqa: E402
from hanser_agent.config import load_settings  # noqa: E402
from hanser_agent.model_gateway import ModelGateway  # noqa: E402
from hanser_agent.models import (  # noqa: E402
    ChatMessage, DialoguePlan, RelationshipState, SceneState, StyleExample,
    WikiEvidence,
)
from hanser_agent.persona import PersonaCompiler  # noqa: E402
from hanser_agent.responder import HanserResponder, StyleValidator  # noqa: E402
from retry_p0_missing_judgments import (  # noqa: E402
    RUBRIC, Verdict, assert_external, memory_fixture,
)


FACT_PAYLOAD = re.compile(
    r"(?:我|憨憨|我们).{0,12}(?:喜欢|去过|去|来过|有|没有|没|看过|吃|喝|"
    r"唱|录|配|买|住|工作|出差|过敏|嗓子|小时候|以前|上次|现在|今天|"
    r"最近|打算|准备|觉得|记得|知道)"
)
DEFAULT_CASES = (
    "style_demand,nostalgia,self_deprecation,uncertainty,"
    "false_memory_trap,late_night,storytelling"
)


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def guard(example: StyleExample, message: str) -> str | None:
    if "".join(example.user_context.split()).casefold() == "".join(message.split()).casefold():
        return "exact_prompt"
    if len(example.character_response) > 240:
        return "response_too_long"
    if FACT_PAYLOAD.search(example.character_response):
        return "fact_payload_pattern"
    if example.source_type == "real" and any(
        token in example.character_response for token in ("我", "我们", "憨憨")
    ):
        return "real_first_person_payload"
    return None


async def run(args: argparse.Namespace) -> None:
    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    settings = load_settings(args.config)
    if settings.responder is None:
        raise ValueError("responder profile required")
    candidate_profile = replace(
        settings.responder, temperature=0, top_p=1, fallback_profile=None,
    )
    judge_profile = replace(
        candidate_profile, model=args.judge_model, temperature=0, top_p=1,
        max_tokens=4096, think=args.judge_thinking, fallback_profile=None,
    )
    assert_external(candidate_profile)
    assert_external(judge_profile)

    source_outputs = read_jsonl(args.source_outputs.resolve())
    source_c = {
        row["case_id"]: row
        for row in source_outputs
        if row["variant"] == "C" and row["status"] == "MEASURED"
    }
    frozen = read_jsonl(args.frozen.resolve())
    fixture_by_id = {row["case"]["id"]: row for row in frozen}
    case_ids = [value.strip() for value in args.cases.split(",") if value.strip()]
    missing = [case_id for case_id in case_ids if case_id not in source_c or case_id not in fixture_by_id]
    if missing:
        raise ValueError(f"missing replay fixtures: {missing}")

    builder = ContextBuilder(
        PersonaCompiler(ROOT / "backend/hanser_agent/prompts/persona"),
        settings.context,
        provider_context_window=candidate_profile.context_window,
        provider_max_output_tokens=candidate_profile.max_tokens,
    )
    gateway = ModelGateway({"candidate": candidate_profile, "judge": judge_profile})
    responder = HanserResponder(
        gateway,
        StyleValidator(ROOT / "backend/hanser_agent/prompts/persona/style_constraints.yaml"),
        profile_name="candidate",
    )
    contexts: dict[tuple[str, str], tuple[object, list[StyleExample]]] = {}
    retrieval_replay: list[dict] = []
    for case_id in case_ids:
        fixture = fixture_by_id[case_id]
        case = fixture["case"]
        original = [StyleExample.model_validate(item) for item in source_c[case_id]["style_examples"]]
        kept: list[StyleExample] = []
        removed = []
        for example in original:
            reason = guard(example, case["message"])
            if reason:
                removed.append({"id": example.id, "reason": reason})
            else:
                kept.append(example)
        retrieval_replay.append({
            "case_id": case_id,
            "source_example_ids": [item.id for item in original],
            "kept_example_ids": [item.id for item in kept],
            "removed": removed,
            "backfilled": False,
        })
        factual = case["response_mode"] == "factual"
        plan = DialoguePlan(
            intent="wiki_fact" if factual else "chitchat", need_wiki=factual,
            standalone_query=case["message"], keywords=[],
            response_mode=case["response_mode"],
            fact_sensitivity="high" if factual else "low",
            target_length=case["target_length"],
        )
        shared = dict(
            current_message=case["message"], history=[], plan=plan,
            wiki_evidence=[WikiEvidence.model_validate(item) for item in fixture["evidence"]],
            memories=memory_fixture(case_id),
            relationship_state=RelationshipState(familiarity=0.6, warmth=0.6),
            scene_state=SceneState(current_topic="之前聊过日常"),
        )
        contexts[(case_id, "B")] = (builder.build(**shared, style_examples=[]), [])
        contexts[(case_id, "C")] = (builder.build(**shared, style_examples=kept), kept)

    semaphore = asyncio.Semaphore(args.concurrency)

    async def generate(case_id: str, variant: str) -> dict:
        context, examples = contexts[(case_id, variant)]
        if variant == "C" and not examples:
            return {"case_id": case_id, "variant": "C", "copy_from_b": True}
        async with semaphore:
            started = time.perf_counter()
            try:
                response = await asyncio.wait_for(responder.respond(context), args.timeout)
                row = {
                    "case_id": case_id, "variant": variant, "status": "MEASURED",
                    "text": response.text, "raw_text": response.raw_text,
                    "validator_actions": response.validator_actions, "error": None,
                }
            except Exception as exc:
                row = {
                    "case_id": case_id, "variant": variant, "status": "NOT_EXECUTED",
                    "text": "", "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                }
            row.update({
                "latency_seconds": time.perf_counter() - started,
                "style_examples": [item.model_dump(mode="json") for item in examples],
                "prompt_sha256": context.prompt_sha256,
            })
            print(f"generated {case_id} {variant} {row['status']}", flush=True)
            return row

    jobs = [(case_id, variant) for case_id in case_ids for variant in ("B", "C")]
    generated = await asyncio.gather(*(generate(*job) for job in jobs))
    generated_by = {
        (row["case_id"], row["variant"]): row
        for row in generated if not row.get("copy_from_b")
    }
    for row in generated:
        if not row.get("copy_from_b"):
            continue
        baseline = generated_by[(row["case_id"], "B")]
        context, _ = contexts[(row["case_id"], "C")]
        copied = dict(baseline)
        copied.update({
            "variant": "C", "copied_from": "B", "style_examples": [],
            "prompt_sha256": context.prompt_sha256,
        })
        generated_by[(row["case_id"], "C")] = copied
    output_rows = [generated_by[(case_id, variant)] for case_id in case_ids for variant in ("B", "C")]
    write_jsonl(output_dir / "outputs.jsonl", output_rows)

    async def judge(case_id: str, reverse: bool) -> dict:
        left, right = (("C", "B") if reverse else ("B", "C"))
        left_row, right_row = generated_by[(case_id, left)], generated_by[(case_id, right)]
        if left_row["status"] != "MEASURED" or right_row["status"] != "MEASURED":
            return {
                "case_id": case_id, "reverse": reverse, "left_variant": left,
                "right_variant": right, "status": "NOT_EXECUTED",
                "error": "candidate_generation_missing",
            }
        if left_row["text"] == right_row["text"]:
            neutral = {
                "character_fidelity": 3, "voice_fidelity": 3,
                "behavioral_fidelity": 3, "naturalness": 3, "grounding": 3,
                "unsupported_first_person": False, "style_fact_leakage": False,
            }
            return {
                "case_id": case_id, "reverse": reverse, "left_variant": left,
                "right_variant": right, "status": "MEASURED", "auto_identical": True,
                "verdict": {"preferred": "tie", "left": neutral, "right": neutral,
                            "issues": ["byte-identical outputs; no style effect"]},
            }
        fixture = fixture_by_id[case_id]
        payload = {
            "case": fixture["case"], "left": left_row["text"], "right": right_row["text"],
            "wiki_evidence": fixture["evidence"],
            "left_style_examples": left_row["style_examples"],
            "right_style_examples": right_row["style_examples"],
            "memory_evidence": [item.model_dump(mode="json") for item in memory_fixture(case_id)],
        }
        async with semaphore:
            for attempt in range(1, args.attempts + 1):
                try:
                    verdict = await asyncio.wait_for(gateway.generate_json("judge", [
                        ChatMessage(role="system", content=RUBRIC),
                        ChatMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
                    ], Verdict), args.timeout)
                    print(f"judged {case_id} reverse={reverse} attempt={attempt}", flush=True)
                    return {
                        "case_id": case_id, "reverse": reverse, "left_variant": left,
                        "right_variant": right, "status": "MEASURED",
                        "verdict": verdict.model_dump(), "attempt": attempt,
                    }
                except Exception as exc:
                    error = f"{type(exc).__name__}: {str(exc)[:300]}"
                    print(f"judge failed {case_id} reverse={reverse} attempt={attempt}: {type(exc).__name__}", flush=True)
            return {
                "case_id": case_id, "reverse": reverse, "left_variant": left,
                "right_variant": right, "status": "NOT_EXECUTED", "error": error,
            }

    judgments = await asyncio.gather(*(
        judge(case_id, reverse) for case_id in case_ids for reverse in (False, True)
    ))
    await gateway.close()
    write_jsonl(output_dir / "judgments.jsonl", judgments)
    (output_dir / "retrieval_replay.json").write_text(
        json.dumps(retrieval_replay, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_type": "external_only_conservative_replay",
        "not_full_end_to_end": True,
        "reason": "uses prior top-k retrieval, applies current static guards, and does not backfill filtered slots",
        "source_outputs": str(args.source_outputs.resolve()),
        "source_outputs_sha256": hashlib.sha256(args.source_outputs.resolve().read_bytes()).hexdigest(),
        "style_search_source_sha256": hashlib.sha256(
            (ROOT / "backend/hanser_agent/agent/tools/style_search.py").read_bytes()
        ).hexdigest(),
        "candidate_model": candidate_profile.model,
        "judge_model": judge_profile.model,
        "external_only_guard": True,
        "local_model_components_constructed": False,
        "cases": case_ids,
        "generated_measured": sum(row["status"] == "MEASURED" for row in output_rows),
        "judgments_measured": sum(row["status"] == "MEASURED" for row in judgments),
        "removed_style_examples": sum(len(row["removed"]) for row in retrieval_replay),
        "production_switched": False,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--source-outputs", type=Path, required=True)
    parser.add_argument("--frozen", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", default=DEFAULT_CASES)
    parser.add_argument("--judge-model", default="deepseek-v4-pro")
    parser.add_argument("--judge-thinking", action="store_true")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=240)
    asyncio.run(run(parser.parse_args()))
