"""Focused real-provider E03 check in a separate database, with source traces."""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from dataclasses import replace
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from hanser_agent import db
from hanser_agent.config import load_settings
from hanser_agent.desktop_api import build_desktop_chat_agent
from hanser_agent.memory import MemoryStore
from hanser_agent.model_gateway import build_model_gateway
from hanser_agent.models import ChatRequest


async def run(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    database = output / "isolated.db"
    with sqlite3.connect(f"file:{args.seed.resolve().as_posix()}?mode=ro", uri=True) as source:
        with sqlite3.connect(database) as target:
            source.backup(target)
    with db.connect(database) as conn:
        db.init_db(conn)
    settings = replace(load_settings(args.config), db_path=database)
    memories = MemoryStore(database)
    gateway = build_model_gateway(settings)
    agent = build_desktop_chat_agent(settings, gateway, memories)
    messages = agent.conversations
    user = "continuity-eval"
    source_ids = messages.append_turn(conversation_id="old-work", user_id=user,
        user_text="请给蓝鲸实验取一个记录标题。温度保持37度，原始读数保存为CSV。",
        assistant_text="标题可以叫蓝鲸观测日志；先保存原始读数，再归一化。",
        model_name="fixture", trace_id="frozen-source", persona_version="fixture")
    for index in range(120):
        messages.append_turn(conversation_id="old-work", user_id=user,
            user_text=f"无关闲聊第{index}轮", assistant_text="收到。", model_name="fixture",
            trace_id=f"padding-{index}", persona_version="fixture")
    messages.append_turn(conversation_id="other-user", user_id="isolated-owner",
        user_text="蓝鲸实验的秘密密码是紫色长颈鹿", assistant_text="紫色长颈鹿",
        model_name="fixture", trace_id="owner-negative", persona_version="fixture")
    # Fixtures are explicitly saved conversation sources; they do not claim
    # that a previous model call or real-world joint experiment took place.
    cases = [
        ("old-work", "蓝鲸实验之前定的标题和数据处理顺序是什么？"),
        ("new-work", "上次蓝鲸实验定了多少度，原始读数保存什么格式？"),
        ("new-work", "我们上次一起去月球的事，你还记得吗？"),
    ]
    (output / "inputs.json").write_text(json.dumps({"cases": cases, "source_ids": source_ids,
        "padding_turns": 120, "fixture_kind": "saved conversation fixtures; not prior live generations"},
        ensure_ascii=False, indent=2), encoding="utf-8")
    inputs = []
    generate = gateway.generate
    async def capture(profile, prompt, **kwargs):
        inputs.append({"profile": profile, "messages": [item.model_dump() for item in prompt]})
        return await generate(profile, prompt, **kwargs)
    gateway.generate = capture
    try:
        rows = []
        for index, (conversation, query) in enumerate(cases):
            started = time.perf_counter()
            response = await agent.send(ChatRequest(user_id=user, conversation_id=conversation,
                request_id=f"continuity-{index}", message=query))
            rows.append({"case_index": index, "query": query, "response": response.model_dump(mode="json"),
                         "elapsed_seconds": time.perf_counter() - started})
            with (output / "results.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(rows[-1], ensure_ascii=False) + "\n")
            print(json.dumps({"case": index, "status": response.status, "text": response.text}, ensure_ascii=False), flush=True)
        request_text = json.dumps(inputs, ensure_ascii=False)
        checks = {
            "old_source_ids_reached_provider": all(value in request_text for value in source_ids),
            "other_owner_never_reached_provider": "紫色长颈鹿" not in request_text,
            "same_session_recalls_title_and_order": "蓝鲸观测日志" in rows[0]["response"]["text"]
                and "原始" in rows[0]["response"]["text"],
            "new_session_recalls_middle_details": "37" in rows[1]["response"]["text"]
                and "CSV" in rows[1]["response"]["text"],
            "shared_claim_not_promoted": not any(item.type == "shared_event"
                for item in memories.list_memories(user_id=user)),
        }
        (output / "provider_inputs.json").write_text(json.dumps(inputs, ensure_ascii=False, indent=2), encoding="utf-8")
        (output / "calls.json").write_text(json.dumps(gateway.call_records, ensure_ascii=False, indent=2), encoding="utf-8")
        (output / "summary.json").write_text(json.dumps({"status": "PASS" if all(checks.values()) else "FAIL",
            "checks": checks, "cases": len(rows), "provider_calls": len(gateway.call_records),
            "tokens": sum(item.get("total_tokens") or 0 for item in gateway.call_records),
            "user_database_modified": False, "quality_ab": "NOT_EXECUTED"}, ensure_ascii=False, indent=2), encoding="utf-8")
        if not all(checks.values()):
            raise AssertionError(checks)
    finally:
        await gateway.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
