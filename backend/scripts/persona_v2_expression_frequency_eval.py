"""Measure actual expression rates in unguided daily multi-turn chat."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
import shutil
import sqlite3
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from hanser_agent import db  # noqa: E402
from hanser_agent.api import build_chat_agent  # noqa: E402
from hanser_agent.config import load_settings  # noqa: E402
from hanser_agent.memory import MemoryStore  # noqa: E402
from hanser_agent.model_gateway import build_model_gateway  # noqa: E402
from hanser_agent.models import ChatMessage, ChatRequest  # noqa: E402
from hanser_agent.persona.expression import observe_recent_expressions  # noqa: E402


def _is_profanity(text: str) -> bool:
    observed = observe_recent_expressions(
        [ChatMessage(role="assistant", content=text)]
    )
    return observed.features["profanity"].value == "present"


def _strong_self_reference(text: str) -> str | None:
    if "老子" in text:
        return "老子"
    if "老娘" in text:
        return "老娘"
    return None


def _reply_trace(run_db: Path, request_id: str) -> dict[str, object]:
    with sqlite3.connect(run_db) as conn:
        row = conn.execute(
            "SELECT snapshot_json FROM reply_snapshots WHERE request_id=?",
            (request_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"missing reply snapshot for {request_id}")
    snapshot = json.loads(str(row[0]))
    return dict(snapshot.get("persona_trace") or {})


async def run(args: argparse.Namespace) -> None:
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    run_db = output / "candidate.db"
    shutil.copy2(args.candidate_db.resolve(), run_db)
    settings = load_settings(args.config.resolve())
    settings = replace(
        settings,
        db_path=run_db,
        persona=replace(settings.persona, active_package="hanser-persona-v2-candidate"),
        style=replace(settings.style, enabled=True, reviewed_only=True),
        embedding=replace(
            settings.embedding,
            device="cuda",
            dtype="float16",
            local_files_only=True,
        ),
    )
    with db.connect(run_db) as conn:
        db.init_db(conn)
    store = MemoryStore(run_db)
    gateway = build_model_gateway(settings)
    agent = build_chat_agent(settings, gateway, store)
    sequences = [
        json.loads(line)
        for line in args.sequences.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ][: args.sequence_limit]
    rows: list[dict[str, object]] = []
    started = time.perf_counter()
    try:
        total = sum(len(sequence["turns"]) for sequence in sequences)
        completed = 0
        for sequence in sequences:
            sequence_id = str(sequence["sequence_id"])
            for turn_index, message in enumerate(sequence["turns"], start=1):
                request_id = f"{sequence_id}:{turn_index:02d}"
                response = await agent.send(
                    ChatRequest(
                        user_id="frequency-eval-user",
                        conversation_id=sequence_id,
                        message=str(message),
                        request_id=request_id,
                    )
                )
                text = response.text
                trace = _reply_trace(run_db, request_id)
                policy = dict(trace.get("policy") or {})
                retrieval = dict(trace.get("retrieval") or {})
                generation = dict(trace.get("generation") or {})
                requirement_ids = [
                    str(item.get("requirement_id"))
                    for item in policy.get("must_do", [])
                    if isinstance(item, dict)
                ]
                rows.append(
                    {
                        "sequence_id": sequence_id,
                        "turn": turn_index,
                        "input": message,
                        "output": text,
                        "profanity": _is_profanity(text),
                        "strong_self_reference": _strong_self_reference(text),
                        "profanity_scheduled": "expression.use_light_profanity" in requirement_ids,
                        "style_2010_retrieved": "style:2010" in retrieval.get("selected_style_ids", []),
                        "generation_status": generation.get("status"),
                        "validator_actions": generation.get("validator_actions", []),
                        "status": response.status,
                        "degraded_reasons": response.degraded_reasons,
                    }
                )
                completed += 1
                print(f"completed {completed}/{total}", flush=True)
    finally:
        await gateway.close()

    profanity_rows = [row for row in rows if row["profanity"]]
    strong_rows = [row for row in rows if row["strong_self_reference"]]
    degraded_reasons = Counter(
        reason for row in rows for reason in row["degraded_reasons"]
    )
    validator_actions = Counter(
        action for row in rows for action in row["validator_actions"]
    )
    summary = {
        "schema_version": 1,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "execution_mode": "real_chat_agent_service_unguided_daily_multiturn",
        "package_id": agent.context_builder.persona_compiler.package_id,
        "style_generation": agent.style_tool.pinned_generation,
        "detector_version": settings.persona.detector_version,
        "target_profanity_rate": agent.context_builder.persona_compiler.effective_settings.profanity_target_rate,
        "turns": len(rows),
        "profanity_turns": len(profanity_rows),
        "profanity_rate": round(len(profanity_rows) / len(rows), 6) if rows else 0.0,
        "strong_self_reference_turns": len(strong_rows),
        "scheduled_profanity_turns": sum(row["profanity_scheduled"] for row in rows),
        "style_2010_retrieval_turns": sum(row["style_2010_retrieved"] for row in rows),
        "optional_feature_unfulfilled_turns": sum(
            "optional_feature_unfulfilled:profanity" in row["validator_actions"]
            for row in rows
        ),
        "contract_fallback_turns": sum(
            row["generation_status"] == "contract_fallback" for row in rows
        ),
        "validator_action_counts": dict(sorted(validator_actions.items())),
        "degraded_turns": sum(row["status"] != "ok" for row in rows),
        "degraded_reason_counts": dict(sorted(degraded_reasons.items())),
        "model_calls": len(gateway.call_records),
        "elapsed_seconds": round(time.perf_counter() - started, 6),
    }
    (output / "outputs.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--candidate-db", type=Path, required=True)
    parser.add_argument("--sequences", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sequence-limit", type=int, default=10)
    asyncio.run(run(parser.parse_args()))
