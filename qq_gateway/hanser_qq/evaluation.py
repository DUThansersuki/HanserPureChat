"""Isolated real Text A/B runner. No QQ connection and no model judge."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import time
from collections import defaultdict
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path

from .config import load_settings
from .headless import install_backend
from .releases import backup_database, read_json, release_path, write_json, runtime_engine_hashes


ROOT = Path(__file__).resolve().parents[1]


def _seed(database, conversation, user, history):
    from hanser_agent import db
    from datetime import datetime, timezone
    if not history:
        return
    now = datetime.now(timezone.utc).isoformat()
    with db.connect(database) as conn:
        if conn.execute("SELECT 1 FROM conversations WHERE id=?", (conversation,)).fetchone():
            return
        conn.execute("INSERT INTO conversations(id,user_id,title,created_at,updated_at,persona_version,model_profile) VALUES(?,?,?,?,?,?,?)",
                     (conversation, user, "isolated evaluation", now, now, "frozen", "frozen"))
        for index, message in enumerate(history):
            conn.execute("INSERT INTO messages(id,conversation_id,role,content,created_at,turn_index,style_example_ids_json) VALUES(?,?,?,?,?,?,?)",
                         (f"{conversation}-seed-{index}", conversation, message["role"], message["content"], now, index, "[]"))


async def run(release_id: str, cases_path: Path, output: Path, concurrency: int = 4):
    from .runtime_style import attach_release_engine
    config = load_settings(ROOT / "config.yml")
    install_backend(config.backend.package_path)
    from hanser_agent import db
    from hanser_agent.config import load_settings as load_backend
    from hanser_agent.desktop_api import build_desktop_chat_agent
    from hanser_agent.memory.store import MemoryStore
    from hanser_agent.model_gateway import build_model_gateway
    from hanser_agent.models import ChatRequest

    cases_bytes = cases_path.read_bytes()
    cases = [json.loads(line) for line in cases_bytes.decode("utf-8").splitlines() if line.strip()]
    release = release_path(ROOT, release_id)
    manifest = read_json(release / "release.json")
    output.mkdir(parents=True, exist_ok=True)
    snapshot_path = output / "snapshot.json"
    cases_hash = hashlib.sha256(cases_bytes).hexdigest()
    if snapshot_path.exists() and read_json(snapshot_path)["cases_sha256"] != cases_hash:
        raise ValueError("evaluation inputs changed after freeze")
    settings = load_backend(config.backend.config_path)
    persona_root = config.backend.package_path / "hanser_agent/prompts/persona/releases"
    pointer = read_json(persona_root / "production.json")
    snapshot = {"release_id": release_id, "cases_sha256": cases_hash, "case_count": len(cases),
                "engine_sha256": runtime_engine_hashes(), "payload_sha256": manifest["payload_sha256"],
                "variants": {"A": pointer, "B": {"package_id": release_id, "style_generation": manifest["style_generation"]}},
                "profiles": {role: {key: getattr(getattr(settings, role), key) for key in
                                      ("model", "temperature", "top_p", "max_tokens", "think")}
                             for role in ("planner", "responder")},
                "cloud_judge": False, "user_database_modified": False, "qq_connected": False}
    if snapshot_path.exists():
        frozen = read_json(snapshot_path)
        if any(frozen[key] != snapshot[key] for key in ("release_id", "engine_sha256", "payload_sha256", "profiles", "variants")):
            raise ValueError("evaluation runtime changed after freeze")
    else:
        write_json(snapshot_path, snapshot)
    context_capture, generated_capture = ContextVar("context", default=None), ContextVar("generated", default=None)
    provider_capture = ContextVar("provider_capture", default=None)
    services, gateways, databases = {}, {}, {}
    for variant in ("A", "B"):
        database = output / f"{variant}.db"
        if not database.exists():
            source = config.backend.package_path / "documents.seed.db" if variant == "A" else ROOT / ".runtime/releases" / release_id / "candidate.db"
            backup_database(source, database)
        variant_settings = replace(settings, db_path=database)
        gateway = build_model_gateway(variant_settings, record_limit=None)
        original_record = gateway._record_call
        def record_call(*args, _original=original_record, _gateway=gateway, **kwargs):
            _original(*args, **kwargs)
            current = provider_capture.get()
            if current is not None:
                record = dict(_gateway.call_records[-1])
                current["records"].append(record)
                with (output / "provider_calls.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"case_id": current["case_id"], "variant": current["variant"], **record}, ensure_ascii=False) + "\n")
        gateway._record_call = record_call
        agent = build_desktop_chat_agent(variant_settings, gateway, MemoryStore(database),
                                        persona_dir_override=persona_root / pointer["directory"] if variant == "A" else release / "persona")
        if variant == "B":
            agent = attach_release_engine(agent, variant_settings, manifest, ROOT / ".runtime/releases" / release_id / "corpus.sqlite3")
        original_build = agent.context_builder.build
        def build(*args, _original=original_build, **kwargs):
            context = _original(*args, **kwargs)
            context_capture.set(context)
            return context
        agent.context_builder.build = build
        original_respond = agent.responder.respond
        async def respond(*args, _original=original_respond, **kwargs):
            generated = await _original(*args, **kwargs)
            generated_capture.set(generated)
            return generated
        agent.responder.respond = respond
        services[variant], gateways[variant], databases[variant] = agent, gateway, database
    output_path = output / "outputs.jsonl"
    existing = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()] if output_path.exists() else []
    completed = {(row["case_id"], row["variant"]) for row in existing}
    groups = defaultdict(list)
    for case in cases:
        groups[case.get("sequence_id") or case["case_id"]].append(case)
    semaphore, count = asyncio.Semaphore(concurrency), len(existing)

    async def group_task(index, group):
        nonlocal count
        user, conversation = f"qq:100:{10000+index}", f"qq:dm:100:{10000+index}"
        async with semaphore:
            for case in group:
                variants = ["A", "B"] if index % 2 == 0 else ["B", "A"]
                for variant in variants:
                    if (case["case_id"], variant) in completed:
                        continue
                    _seed(databases[variant], conversation, user, case.get("history", []))
                    gateway = gateways[variant]
                    token = gateway.start_call_capture()
                    own_capture = {"case_id": case["case_id"], "variant": variant, "records": []}
                    provider_capture.set(own_capture)
                    context_capture.set(None)
                    generated_capture.set(None)
                    request_id = "eval-" + variant + "-" + case["case_id"]
                    started = time.perf_counter()
                    row = {"case_id": case["case_id"], "variant": variant, "group": case["group"],
                           "sequence_id": case.get("sequence_id"), "input": case["message"]}
                    try:
                        reply = await services[variant].send(ChatRequest(user_id=user, conversation_id=conversation,
                                                           request_id=request_id, message=case["message"]))
                        with db.connect(databases[variant]) as conn:
                            saved = conn.execute("SELECT snapshot_json FROM reply_snapshots WHERE request_id=?", (request_id,)).fetchone()
                        trace = json.loads(saved["snapshot_json"])["persona_trace"]
                        row.update(status="MEASURED", text=reply.text, degraded_reasons=reply.degraded_reasons, trace=trace)
                    except Exception as exc:
                        row.update(status="FAILED", error_type=type(exc).__name__, error_code=getattr(exc, "code", None))
                    finally:
                        gateway.finish_call_capture(token)
                        row["calls"] = own_capture["records"]
                    # ChatAgentService owns its nested per-turn capture. Its
                    # persisted usage includes both Planner and Responder calls.
                    generation = row.get("trace", {}).get("generation", {})
                    row["token_usage"] = generation.get("token_usage", {})
                    row["actual_calls"] = generation.get("planner_calls", 0) + generation.get("responder_calls", 0)
                    context, generated = context_capture.get(), generated_capture.get()
                    if context is not None:
                        row["context_sha256"] = context.prompt_sha256
                        row["messages"] = [item.model_dump(mode="json") for item in context.messages]
                    if generated is not None:
                        row["generated"] = generated.model_dump(mode="json")
                    row["memory_state"] = [
                        {key: getattr(item, key) for key in ("memory_key", "predicate", "object_value", "polarity", "context_tags")}
                        for item in services[variant].memory_store.list_memories(user_id=user)
                    ]
                    if variant == "B" and manifest.get("runtime_engine") == "qq-text-v3":
                        with db.connect(databases[variant]) as conn:
                            action = conn.execute("SELECT result_json FROM qq_memory_actions WHERE user_id=? AND conversation_id=? ORDER BY rowid DESC LIMIT 1",
                                                  (user, conversation)).fetchone()
                        row["latest_memory_action"] = json.loads(action[0]) if action else None
                    row["seconds"] = time.perf_counter() - started
                    with output_path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                    count += 1
                    if count % 12 == 0:
                        print(json.dumps({"completed_outputs": count, "planned_outputs": len(cases)*2}), flush=True)
    try:
        await asyncio.gather(*(group_task(index, group) for index, group in enumerate(groups.values())))
    finally:
        for gateway in gateways.values():
            await gateway.close()
    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    by = {(row["case_id"], row["variant"]): row for row in rows}
    rng = random.Random(202610180746)
    mapping, blind = {}, []
    for case in cases:
        left, right = ("A", "B") if rng.random() < 0.5 else ("B", "A")
        mapping[case["case_id"]] = {"X": left, "Y": right}
        blind.append({"case_id": case["case_id"], "input": case["message"],
                      "X": by[(case["case_id"], left)].get("text"), "Y": by[(case["case_id"], right)].get("text")})
    write_json(output / "blind_mapping.json", mapping)
    write_json(output / "blind_pairs.json", blind)
    summary = {"outputs": len(rows), "failed": sum(row["status"] != "MEASURED" for row in rows),
               "calls": sum(row.get("actual_calls", 0) for row in rows),
               "tokens": sum(row.get("token_usage", {}).get("total_tokens") or 0 for row in rows),
               "candidate_new_source_injected": sum(bool(row.get("trace", {}).get("retrieval", {}).get("match_details", {}).get("full_corpus", {}).get("selected_source_fragments"))
                                                    for row in rows if row["variant"] == "B")}
    write_json(output / "run_summary.json", summary)
    print(json.dumps(summary), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--cases", type=Path, default=ROOT / "eval/cases.v1.jsonl")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    asyncio.run(run(args.release_id, args.cases, args.output, args.concurrency))


if __name__ == "__main__":
    main()
