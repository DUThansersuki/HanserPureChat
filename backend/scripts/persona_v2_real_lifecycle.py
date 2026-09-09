"""Run the frozen cross-session/cross-user scenario through real ChatAgentService."""
from __future__ import annotations

import argparse
import asyncio
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
from hanser_agent.models import ChatRequest  # noqa: E402


async def run(args: argparse.Namespace) -> None:
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    db_path = output / "lifecycle.db"
    shutil.copy2(args.candidate_db.resolve(), db_path)
    settings = load_settings(args.config.resolve())
    settings = replace(
        settings,
        db_path=db_path,
        persona=replace(settings.persona, active_package="hanser-persona-v2-candidate"),
        style=replace(settings.style, enabled=True, reviewed_only=True),
        embedding=replace(settings.embedding, device="cuda", dtype="float16", local_files_only=True),
    )
    with db.connect(db_path) as conn:
        db.init_db(conn)
    store = MemoryStore(db_path)
    gateway = build_model_gateway(settings)
    agent = build_chat_agent(settings, gateway, store)
    frozen = [
        row for row in (
            json.loads(line)
            for line in args.sequences.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        if row["sequence_id"] == "daily.sequence.real_lifecycle.004"
    ][0]
    outputs: list[dict[str, object]] = []
    started = time.perf_counter()
    try:
        for index, turn in enumerate(frozen["turns"], start=1):
            request = ChatRequest(
                user_id=str(turn["actor"]),
                conversation_id=str(turn["session"]),
                message=str(turn["user"]),
                request_id=f"lifecycle-{index:02d}",
            )
            before = len(gateway.call_records)
            response = await agent.send(request)
            records = gateway.call_records[before:]
            outputs.append({
                "turn": index,
                "actor": turn["actor"],
                "session": turn["session"],
                "input": turn["user"],
                "expected": turn["expected"],
                "response": response.model_dump(mode="json"),
                "model_calls": records,
            })
            print(f"completed {index}/{len(frozen['turns'])}", flush=True)
    finally:
        await gateway.close()

    memories = {
        user: [item.model_dump(mode="json") for item in store.list_memories(user_id=user)]
        for user in ("user-a", "user-b")
    }
    with sqlite3.connect(db_path) as conn:
        snapshots = [
            json.loads(row[0])
            for row in conn.execute(
                "SELECT snapshot_json FROM reply_snapshots ORDER BY created_at"
            ).fetchall()
        ]
        ownership = [
            {"id": row[0], "user_id": row[1]}
            for row in conn.execute(
                "SELECT id, user_id FROM conversations ORDER BY id"
            ).fetchall()
        ]

    pref_a = [m for m in memories["user-a"] if m.get("predicate") == "expression_permission"]
    pref_b = [m for m in memories["user-b"] if m.get("predicate") == "expression_permission"]
    turn_11_trace = snapshots[10].get("persona_trace", {}) if len(snapshots) >= 11 else {}
    trace_complete = all(
        snapshot.get("persona_trace", {}).get("generation", {}).get("planner_calls") is not None
        and "permissions" in snapshot.get("persona_trace", {})
        and "score_components" in snapshot.get("persona_trace", {}).get("retrieval", {})
        for snapshot in snapshots
    )
    checks = {
        "all_turns_completed": len(outputs) == 12,
        "post_turn_completed": all(row["response"].get("post_turn_status", "completed") == "completed" for row in outputs),
        "conversation_ownership": ownership == [
            {"id": "s1", "user_id": "user-a"},
            {"id": "s2", "user_id": "user-a"},
            {"id": "s3", "user_id": "user-b"},
        ],
        "user_a_durable_profanity_deny": any(m.get("memory_key") == "preference:expression:profanity:*" and m.get("object_value") == "deny" for m in pref_a),
        "user_b_current_revoke_wins": turn_11_trace.get("permissions", {}).get("effective", {}).get("profanity") == "deny",
        "no_cross_user_memory_ids": not ({m["id"] for m in memories["user-a"]} & {m["id"] for m in memories["user-b"]}),
        "trace_complete": trace_complete,
        "candidate_package_frozen": all(snapshot.get("persona_trace", {}).get("combination", {}).get("package_id") == "hanser-persona-v2-candidate-20260908" for snapshot in snapshots),
        "safe_generation_frozen": all(snapshot.get("persona_trace", {}).get("combination", {}).get("style_generation") == "persona-v2-style-safe-7bd0970e2164b249" for snapshot in snapshots),
    }
    result = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "execution_mode": "real_chat_agent_service_memory_post_turn_conversation_ownership",
        "elapsed_seconds": round(time.perf_counter() - started, 6),
        "checks": checks,
        "verdict": "pass" if all(checks.values()) else "fail",
        "ownership": ownership,
        "memory_counts": {user: len(items) for user, items in memories.items()},
        "expression_preferences": {"user-a": pref_a, "user-b": pref_b},
        "model_calls": gateway.call_records,
    }
    (output / "outputs.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in outputs),
        encoding="utf-8",
    )
    (output / "reply_snapshots.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in snapshots),
        encoding="utf-8",
    )
    (output / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--candidate-db", type=Path, required=True)
    parser.add_argument("--sequences", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
