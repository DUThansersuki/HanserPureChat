"""Alternating Wiki/casual resource experiment for Slice 4."""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from hanser_agent.api import build_chat_agent, create_app  # noqa: E402
from hanser_agent.config import load_settings  # noqa: E402
from hanser_agent.memory import MemoryStore  # noqa: E402
from hanser_agent.model_gateway import build_model_gateway  # noqa: E402


MESSAGES = [
    "Hanser什么时候退出VirtuaReal",
    "晚上好 今天随便聊聊",
    "Hanser加入VirtuaReal是哪年",
    "路上看到一只橘猫",
    "Hanser在哪里上过大学",
    "嗯 你接着说",
    "Hanser以前在哪个平台直播",
    "今天有一点累 先别给建议",
    "Hanser配音过哪些作品",
    "哈哈 换个轻松话题",
] * 2


def gpu_sample() -> dict[str, int] | None:
    try:
        raw = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used,utilization.gpu", "--format=csv,noheader,nounits"],
            text=True,
            timeout=5,
        ).strip().splitlines()[0]
        memory, utilization = [int(value.strip()) for value in raw.split(",")]
        return {"memory_used_mib": memory, "utilization_percent": utilization}
    except Exception:
        return None


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--source-database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--policy", choices=["retain", "release_reranker"], default="retain"
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    audit_db = args.output_dir / "audit.db"
    shutil.copy2(args.source_database, audit_db)

    settings = load_settings(args.config)
    settings = replace(
        settings,
        db_path=audit_db,
        embedding=replace(settings.embedding, local_files_only=True),
        reranker=replace(
            settings.reranker,
            local_files_only=True,
            release_after_request=args.policy == "release_reranker",
        ),
    )
    gateway = build_model_gateway(settings)
    agent = build_chat_agent(settings, gateway, MemoryStore(audit_db))
    timings: dict[str, float] = {}

    def wrap(obj, method: str, label: str) -> None:
        original = getattr(obj, method)

        async def measured(*values, **kwargs):
            started = time.perf_counter()
            try:
                return await original(*values, **kwargs)
            finally:
                timings[label] = time.perf_counter() - started

        setattr(obj, method, measured)

    for obj, method, label in [
        (agent.planner, "plan", "planner"),
        (agent.wiki_tool, "search", "wiki"),
        (agent.style_tool, "search", "style"),
        (agent.memory_tool, "search", "memory"),
        (agent.responder, "respond", "responder"),
        (agent.post_turn, "process", "post_turn"),
    ]:
        wrap(obj, method, label)

    samples: list[dict[str, object]] = []
    stop_sampling = asyncio.Event()

    async def sample_loop() -> None:
        while not stop_sampling.is_set():
            value = await asyncio.to_thread(gpu_sample)
            if value:
                samples.append({"at": time.time(), **value})
            try:
                await asyncio.wait_for(stop_sampling.wait(), timeout=0.5)
            except TimeoutError:
                pass

    try:
        await gateway.unload("planner")
    except Exception:
        pass
    sampler = asyncio.create_task(sample_loop())
    application = create_app(settings=settings, chat_agent=agent, memory_store=agent.memory_store)
    rows: list[dict[str, object]] = []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://slice4.local",
        timeout=240,
    ) as client:
        for index, message in enumerate(MESSAGES, 1):
            timings.clear()
            started = time.perf_counter()
            response = await client.post(
                "/v1/chat",
                json={"user_id": "slice4-resource", "conversation_id": "slice4-resource", "message": message},
            )
            try:
                response_body = response.json()
            except ValueError:
                response_body = {}
            rows.append(
                {
                    "turn": index,
                    "kind": "wiki" if index % 2 else "casual",
                    "message": message,
                    "status": response.status_code,
                    "response_status": response_body.get("status", "ok"),
                    "degraded_reasons": response_body.get(
                        "degraded_reasons", []
                    ),
                    "seconds": time.perf_counter() - started,
                    "stage_seconds": dict(timings),
                }
            )
            print(index, rows[-1]["kind"], response.status_code, round(rows[-1]["seconds"], 3), flush=True)
    stop_sampling.set()
    await sampler
    await gateway.close()
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "policy": args.policy,
        "config": {
            "planner_model": settings.planner.model,
            "responder_model": settings.responder.model,
            "embedding_model": settings.embedding.model,
            "reranker_model": settings.reranker.model,
        },
        "turns": rows,
        "gpu_samples": samples,
        "gpu_peak_mib": max((int(row["memory_used_mib"]) for row in samples), default=None),
        "gpu_min_mib": min((int(row["memory_used_mib"]) for row in samples), default=None),
    }
    (args.output_dir / "resource_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    asyncio.run(main())
