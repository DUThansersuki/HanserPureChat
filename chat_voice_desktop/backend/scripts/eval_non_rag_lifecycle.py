"""Focused real-provider service evaluation against isolated seed data.

No corpus extraction, index switch, user database writes, Judge, or TTS.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import sys
import time
from dataclasses import replace
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from hanser_agent import db
from hanser_agent.config import load_settings
from hanser_agent.desktop_api import build_desktop_chat_agent, create_desktop_app
from hanser_agent.memory import MemoryStore
from hanser_agent.model_gateway import build_model_gateway
from hanser_agent.persona.permissions import infer_expression_permissions

CASES = [
    "以后默认简短回答。",
    "她喜欢咖啡，我只是转述她的话。我喜欢茶。",
    "我喜欢哪种饮料？",
    "刚才说错了，我不喜欢茶，我喜欢牛奶。",
    "这件事不要开玩笑，我们认真聊。",
    "你继续说刚才那件事。",
    "换个话题，聊聊今天怎么安排休息。",
    "这条请详细说明如何把五件事排优先级，给例子。",
    "我上面说了什么偏好？",
    "你好，今天可以一起把休息时间安排好吗？",
]


async def run(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=args.resume)
    cases = output / "cases.json"
    if not args.resume:
        cases.write_text(json.dumps(CASES, ensure_ascii=False, indent=2), encoding="utf-8")
    elif json.loads(cases.read_text(encoding="utf-8")) != CASES:
        raise ValueError("resume cases differ from frozen input")
    database = output / "isolated.db"
    if not args.resume:
        source = sqlite3.connect(f"file:{args.seed.resolve().as_posix()}?mode=ro", uri=True)
        target = sqlite3.connect(database)
        try:
            source.backup(target)
        finally:
            source.close(); target.close()
    base = load_settings(args.config)
    settings = replace(base, db_path=database)
    store = MemoryStore(database)
    gateway = build_model_gateway(settings)
    generate = gateway.generate
    async def recorded_generate(profile, messages, **kwargs):
        value = await generate(profile, messages, **kwargs)
        with (output / "provider_outputs.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"profile": profile, "raw_output": value,
                "structured_instruction_present": any("只输出一个JSON对象" in item.content for item in messages),
                "json_object_requested": kwargs.get("json_object", False),
                "call": gateway.current_call_record()}, ensure_ascii=False) + "\n")
        if kwargs.get("json_object"):
            (output / "structured_request_messages.json").write_text(json.dumps(
                [item.model_dump() for item in messages], ensure_ascii=False, indent=2), encoding="utf-8")
        return value
    gateway.generate = recorded_generate
    agent = build_desktop_chat_agent(settings, gateway, store)
    frozen = json.loads((output / "frozen.json").read_text(encoding="utf-8")) if args.resume else {"cases_sha256": hashlib.sha256(cases.read_bytes()).hexdigest(),
              "seed_sha256": hashlib.sha256(args.seed.read_bytes()).hexdigest(),
              "persona_source_sha256": agent.context_builder.persona_compiler.source_sha256,
              "persona_package_id": agent.context_builder.persona_compiler.package_id,
              "style_generation": agent.context_builder.persona_compiler.manifest.style_generation,
              "planner_model": settings.planner.model, "responder_model": settings.responder.model,
              "structured_performance": settings.performance.structured_performance_enabled,
              "scope": "service lifecycle contracts; no quality A/B or voice certification"}
    if not args.resume:
        (output / "frozen.json").write_text(json.dumps(frozen, ensure_ascii=False, indent=2), encoding="utf-8")
    app = create_desktop_app(settings=settings, chat_agent=agent, model_gateway=gateway, desktop_token="evaluation-only")
    results = [json.loads(line) for line in (output / "results.jsonl").read_text(encoding="utf-8").splitlines()] if args.resume else []
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://eval", timeout=300,
                                         headers={"X-Hanser-Desktop-Token": "evaluation-only"}) as client:
                for index, message in enumerate(CASES):
                    if any(row["case_index"] == index for row in results):
                        continue
                    request = {"user_id": "non-rag-eval", "conversation_id": "lifecycle", "request_id": f"lifecycle-{index}", "message": message}
                    if index == 9:
                        request["output_preferences"] = {"text": True, "speech": True}
                    started = time.perf_counter()
                    response = await client.post("/v1/chat", json=request)
                    elapsed = time.perf_counter() - started
                    if response.status_code != 200:
                        (output / "failure.json").write_text(json.dumps({"case_index": index,
                            "http_status": response.status_code, "response": response.json(),
                            "calls": [{key: record.get(key) for key in ("profile", "model", "status", "error_type", "latency_seconds")}
                                      for record in gateway.call_records]}, ensure_ascii=False, indent=2), encoding="utf-8")
                        raise RuntimeError(f"case {index} failed: HTTP {response.status_code}")
                    result = response.json()
                    memories = store.list_memories(user_id="non-rag-eval")
                    permissions = infer_expression_permissions(store.list_permission_events(user_id="non-rag-eval", conversation_id="lifecycle"))
                    with db.connect(database) as conn:
                        timing = conn.execute("SELECT stage_timings_json FROM request_executions WHERE request_id=?", (request["request_id"],)).fetchone()
                    row = {"case_index": index, "message": message, "response": result,
                           "elapsed_seconds": elapsed, "stage_timings": json.loads(timing[0]),
                           "memory_contents": [item.content for item in memories], "permissions": permissions}
                    results.append(row)
                    with (output / "results.jsonl").open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                    print(json.dumps({"case_index": index, "status": result.get("status", "ok"), "seconds": round(elapsed, 2)}, ensure_ascii=False), flush=True)
                    if index == 1:
                        assert "用户喜欢茶" in row["memory_contents"] and "用户喜欢咖啡" not in row["memory_contents"]
                    if index == 5:
                        assert permissions.get("humor") == "deny"
                    if index == 6:
                        assert "humor" not in permissions
                    if index == 8:
                        before = agent.conversations.message_count("lifecycle", user_id="non-rag-eval")
                        replay = (await client.post("/v1/chat", json=request)).json()
                        assert replay["reply_id"] == result["reply_id"]
                        assert before == agent.conversations.message_count("lifecycle", user_id="non-rag-eval")
                    if index == 9:
                        assert result["reply_id"]
                        committed = agent.conversations.get_reply_snapshot(result["reply_id"], user_id="non-rag-eval")
                        assert committed.text_source == "validated_semantic"
                last_request = {"user_id": "non-rag-eval", "conversation_id": "lifecycle", "request_id": "lifecycle-8", "message": CASES[8]}
                before = agent.conversations.message_count("lifecycle", user_id="non-rag-eval")
                replay = (await client.post("/v1/chat", json=last_request)).json()
                assert replay["trace_id"] == next(row["response"]["trace_id"] for row in results if row["case_index"] == 8)
                assert before == agent.conversations.message_count("lifecycle", user_id="non-rag-eval")
                health = (await client.get("/health")).json()
        summary = {"contract_status": "PASS", "completed_cases": len(results), "health": health,
                   "model_calls_in_this_execution": len(gateway.call_records), "voice_evaluation": "NOT_EXECUTED",
                   "quality_ab": "NOT_EXECUTED", "user_database_modified": False}
        (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        await gateway.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    asyncio.run(run(parser.parse_args()))
