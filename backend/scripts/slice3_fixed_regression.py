"""Rebuild and replay the 37 fixed Phase 6 cases through the Slice 3 contract.

Retrieval inputs are taken from the immutable Phase 6 fixture so this run isolates
context construction and responder behavior.  No application database is opened.
"""
from __future__ import annotations

import argparse
import asyncio
import contextvars
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from hanser_agent.agent.context_builder import ContextBuilder
from hanser_agent.config import load_settings
from hanser_agent.model_gateway import build_model_gateway
from hanser_agent.models import (
    DialoguePlan,
    MemoryItem,
    RelationshipState,
    SceneState,
    StyleExample,
    WikiEvidence,
)
from hanser_agent.persona import PersonaCompiler
from hanser_agent.responder import HanserResponder, StyleValidator


_response_usage: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "slice3_response_usage", default=None
)


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _memory(case_id: str) -> list[MemoryItem]:
    if case_id not in {"recall", "cross_session_recall", "conflict", "correction"}:
        return []
    content = (
        "用户希望被称为小林"
        if case_id == "cross_session_recall"
        else "用户喜欢茶"
    )
    return [
        MemoryItem(
            id=f"fixture:{case_id}",
            user_id="eval",
            type="user_preference",
            content=content,
            importance=0.8,
            confidence=0.95,
            source_message_ids=["fixture:user-statement"],
            created_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
        )
    ]


async def run(args: argparse.Namespace) -> None:
    baseline = args.baseline.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "fixed37_outputs.jsonl"
    summary_path = output_dir / "fixed37_summary.json"
    if output_path.exists() or summary_path.exists():
        raise FileExistsError("fixed37 output already exists; choose a new output directory")

    fixture_rows = [
        json.loads(line)
        for line in baseline.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(fixture_rows) != 37:
        raise ValueError(f"expected 37 frozen cases, got {len(fixture_rows)}")

    settings = load_settings(args.config)
    profile = settings.responder
    if profile is None:
        raise ValueError("responder profile is required")
    builder = ContextBuilder(
        PersonaCompiler(ROOT / "backend/hanser_agent/prompts/persona"),
        settings.context,
        provider_context_window=profile.context_window,
        provider_max_output_tokens=profile.max_tokens,
    )
    contexts = []
    for row in fixture_rows:
        case = row["case"]
        factual = case["response_mode"] == "factual"
        plan = DialoguePlan(
            intent="wiki_fact" if factual else "chitchat",
            need_wiki=factual,
            standalone_query=case["message"],
            keywords=[],
            response_mode=case["response_mode"],
            fact_sensitivity="high" if factual else "low",
            target_length=case["target_length"],
        )
        context = builder.build(
            current_message=case["message"],
            history=[],
            plan=plan,
            wiki_evidence=[WikiEvidence.model_validate(x) for x in row["evidence"]],
            style_examples=[StyleExample.model_validate(x) for x in row["style"]],
            memories=_memory(case["id"]),
            relationship_state=RelationshipState(familiarity=0.6, warmth=0.6),
            scene_state=SceneState(current_topic="之前聊过日常"),
        )
        if context.messages[-1].content != case["message"]:
            raise AssertionError(f"current request changed for {case['id']}")
        if context.estimated_input_tokens > context.input_token_budget:
            raise AssertionError(f"budget exceeded for {case['id']}")
        if not context.prompt_sha256:
            raise AssertionError(f"missing prompt identity for {case['id']}")
        contexts.append((case, context))

    if args.dry_run:
        print(json.dumps({
            "validated_contexts": len(contexts),
            "network_calls": 0,
            "max_estimated_input_tokens": max(c.estimated_input_tokens for _, c in contexts),
            "contract_versions": sorted({c.contract_version for _, c in contexts}),
        }))
        return

    gateway = build_model_gateway(settings)
    async def capture_usage(response):
        await response.aread()
        try:
            payload = response.json()
            _response_usage.set(payload.get("usage"))
        except (ValueError, AttributeError):
            _response_usage.set(None)

    gateway._client.event_hooks.setdefault("response", []).append(capture_usage)
    responder = HanserResponder(
        gateway,
        StyleValidator(ROOT / "backend/hanser_agent/prompts/persona/style_constraints.yaml"),
    )
    semaphore = asyncio.Semaphore(args.concurrency)

    async def generate(case: dict, context):
        async with semaphore:
            _response_usage.set(None)
            started = time.perf_counter()
            record = {
                "case_id": case["id"],
                "status": "NOT_EXECUTED",
                "contract_version": context.contract_version,
                "model": profile.model,
                "thinking": profile.think,
                "generation_parameters": {
                    "temperature": profile.temperature,
                    "top_p": profile.top_p,
                    "max_tokens": profile.max_tokens,
                    "context_window": profile.context_window,
                },
                "context": context.model_dump(mode="json"),
            }
            try:
                result = await asyncio.wait_for(
                    responder.respond(context), timeout=args.timeout
                )
                record.update(
                    status="MEASURED",
                    raw_text=result.raw_text,
                    text=result.text,
                    actions=result.validator_actions,
                    usage=_response_usage.get(),
                    latency_seconds=time.perf_counter() - started,
                )
            except Exception as error:
                record.update(
                    error=type(error).__name__,
                    detail=str(error)[:500],
                    latency_seconds=time.perf_counter() - started,
                )
            print(case["id"], record["status"], flush=True)
            return record

    try:
        records = await asyncio.gather(*(generate(case, ctx) for case, ctx in contexts))
    finally:
        await gateway.close()

    with output_path.open("x", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline": str(baseline),
        "baseline_sha256": _sha(baseline.read_bytes()),
        "config_sha256": _sha(args.config.resolve().read_bytes()),
        "context_builder_sha256": _sha(
            (ROOT / "backend/hanser_agent/agent/context_builder.py").read_bytes()
        ),
        "persona_compiler_sha256": _sha(
            (ROOT / "backend/hanser_agent/persona/compiler.py").read_bytes()
        ),
        "total": len(records),
        "measured": sum(r["status"] == "MEASURED" for r in records),
        "failed": sum(r["status"] != "MEASURED" for r in records),
        "provider": profile.provider,
        "model": profile.model,
        "thinking": profile.think,
        "concurrency": args.concurrency,
        "input_token_budget": settings.context.input_token_budget,
        "output_reserve_tokens": settings.context.output_reserve_tokens,
        "provider_context_window": profile.context_window,
        "max_estimated_input_tokens": max(
            r["context"]["estimated_input_tokens"] for r in records
        ),
        "budget_violations": sum(
            r["context"]["estimated_input_tokens"]
            > r["context"]["input_token_budget"]
            for r in records
        ),
        "missing_prompt_hashes": sum(
            not r["context"]["prompt_sha256"] for r in records
        ),
        "drop_events": sum(len(r["context"]["drop_ledger"]) for r in records),
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--baseline",
        type=Path,
        default=ROOT / "audit_artifacts/phase6/frozen_contexts.jsonl",
    )
    parser.add_argument("--config", type=Path, default=ROOT / "backend/config.yml")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--dry-run", action="store_true")
    asyncio.run(run(parser.parse_args()))
