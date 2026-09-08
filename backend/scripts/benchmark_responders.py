from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hanser_agent import db
from hanser_agent.agent.context_builder import ContextBuilder, ContextBundle
from hanser_agent.agent.tools.style_search import StyleSearchTool
from hanser_agent.config import load_settings
from hanser_agent.model_gateway import build_model_gateway
from hanser_agent.models import (
    ChatMessage,
    DialoguePlan,
    MemoryItem,
    RelationshipState,
    SceneState,
    WikiEvidence,
)
from hanser_agent.persona import PersonaCompiler
from hanser_agent.responder import StyleValidator
from hanser_agent.retrieval import SQLiteVectorStore, build_embedder


class BenchmarkCase(BaseModel):
    id: str
    message: str
    response_mode: str
    target_length: str
    history: list[ChatMessage] = Field(default_factory=list)
    memories: list[dict[str, object]] = Field(default_factory=list)
    wiki_evidence: list[WikiEvidence] = Field(default_factory=list)


class JudgeScores(BaseModel):
    character_fidelity: int = Field(ge=1, le=5)
    voice_fidelity: int = Field(ge=1, le=5)
    naturalness: int = Field(ge=1, le=5)
    grounding: int = Field(ge=1, le=5)
    overall: int = Field(ge=1, le=5)
    issues: list[str] = Field(default_factory=list)


def deterministic_metrics(
    case: BenchmarkCase,
    raw_text: str,
    final_text: str,
) -> dict[str, float]:
    punctuation = sum(raw_text.count(value) for value in "，。！？；：")
    assistantese = sum(
        value in raw_text
        for value in (
            "当然可以",
            "以下是",
            "希望能帮助",
            "如果你愿意",
            "作为AI",
            "作为一个AI",
            "请问还有",
        )
    )
    action_narration = int(
        bool(re.search(r"\*[^*]+\*|（(?:笑|叹气|歪头|摸摸)[^）]*）", raw_text))
    )
    length = len(final_text)
    length_ok = (
        length <= 70
        if case.target_length == "short"
        else 20 <= length <= 220
        if case.target_length == "medium"
        else length >= 80
    )
    unsupported_first_person = int(
        not case.wiki_evidence
        and bool(re.search(r"我(?:小时候|当时|曾经|那时候).{0,20}(?:觉得|感到|参加|经历)", raw_text))
    )
    return {
        "raw_punctuation_count": float(punctuation),
        "punctuation_compliance": float(punctuation == 0),
        "assistantese": float(assistantese > 0),
        "action_narration": float(action_narration),
        "length_compliance": float(length_ok),
        "unsupported_first_person": float(unsupported_first_person),
        "final_characters": float(length),
    }


async def build_contexts(
    cases: list[BenchmarkCase],
    *,
    settings,
) -> dict[str, ContextBundle]:
    package_dir = Path(__file__).resolve().parents[1] / "hanser_agent"
    builder = ContextBuilder(PersonaCompiler(package_dir / "prompts" / "persona"))
    embedder = build_embedder(settings.embedding)
    style_tool = StyleSearchTool(
        settings=settings,
        embedder=embedder,
        vector_store=SQLiteVectorStore(settings.db_path),
    )
    contexts: dict[str, ContextBundle] = {}
    for case in cases:
        plan = DialoguePlan(
            intent="wiki_fact" if case.wiki_evidence else "chitchat",
            need_wiki=bool(case.wiki_evidence),
            need_memory=bool(case.memories),
            standalone_query=case.message,
            keywords=[],
            response_mode=case.response_mode,
            fact_sensitivity="high" if case.wiki_evidence else "low",
            target_length=case.target_length,
        )
        style = await style_tool.search(case.message, plan)
        memories = [
            MemoryItem(
                id=f"benchmark:{case.id}:{index}",
                user_id="benchmark-user",
                conversation_id="benchmark",
                source_message_ids=[f"benchmark-message:{index}"],
                created_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
                **value,
            )
            for index, value in enumerate(case.memories)
        ]
        contexts[case.id] = builder.build(
            current_message=case.message,
            history=case.history,
            plan=plan,
            wiki_evidence=case.wiki_evidence,
            style_examples=style.examples,
            memories=memories,
            relationship_state=RelationshipState(
                familiarity=0.45,
                warmth=0.65,
                trust=0.55,
                teasing_permission=0.4,
                shared_context_density=0.25,
            ),
            scene_state=SceneState(current_topic=case.message[:60]),
        )
    return contexts


async def judge_output(
    *,
    gateway,
    judge_profile: str,
    case: BenchmarkCase,
    context: ContextBundle,
    output: str,
) -> JudgeScores:
    prompt = (
        "你是离线角色对话评测器 只评价候选回答 不续写对话\n"
        "按 1 到 5 分评价 character_fidelity voice_fidelity naturalness "
        "grounding overall\n"
        "Hanser Persona 与事实边界如下\n"
        f"{context.blocks['persona']}\n\n"
        f"用户输入\n{case.message}\n\n"
        f"候选回答\n{output}"
    )
    return await gateway.generate_json(
        judge_profile,
        [ChatMessage(role="user", content=prompt)],
        JudgeScores,
    )


async def benchmark(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    with db.connect(settings.db_path) as conn:
        db.init_db(conn)
    cases = [
        BenchmarkCase.model_validate(value)
        for value in json.loads(args.dataset.read_text(encoding="utf-8"))
    ][: args.limit or None]
    profiles = [value.strip() for value in args.profiles.split(",") if value.strip()]
    gateway = build_model_gateway(settings)
    contexts = await build_contexts(cases, settings=settings)
    validator = StyleValidator(
        Path(__file__).resolve().parents[1]
        / "hanser_agent"
        / "prompts"
        / "persona"
        / "style_constraints.yaml"
    )
    run_id = str(uuid4())
    started_at = datetime.now(timezone.utc)
    report: dict[str, object] = {
        "run_id": run_id,
        "dataset": str(args.dataset),
        "started_at": started_at.isoformat(),
        "profiles": {},
    }
    with db.connect(settings.db_path) as conn:
        conn.execute(
            """
            INSERT INTO evaluation_runs
                (id, kind, dataset, started_at, finished_at, summary_json)
            VALUES (?, 'responder_benchmark', ?, ?, NULL, '{}')
            """,
            (run_id, str(args.dataset), started_at.isoformat()),
        )
        conn.commit()

    for profile in profiles:
        outputs = []
        for case in cases:
            began = time.perf_counter()
            raw_text = await gateway.generate(profile, contexts[case.id].messages)
            latency = time.perf_counter() - began
            final_text = validator.normalize(raw_text).text
            metrics: dict[str, object] = deterministic_metrics(
                case,
                raw_text,
                final_text,
            )
            if args.judge_profile:
                metrics["judge"] = (
                    await judge_output(
                        gateway=gateway,
                        judge_profile=args.judge_profile,
                        case=case,
                        context=contexts[case.id],
                        output=final_text,
                    )
                ).model_dump()
            output = {
                "case_id": case.id,
                "raw_text": raw_text,
                "final_text": final_text,
                "latency_seconds": round(latency, 4),
                "metrics": metrics,
            }
            outputs.append(output)
            with db.connect(settings.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO evaluation_outputs
                        (run_id, profile, case_id, raw_output_text, output_text,
                         latency_seconds, metrics_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        profile,
                        case.id,
                        raw_text,
                        final_text,
                        latency,
                        json.dumps(metrics, ensure_ascii=False),
                    ),
                )
                conn.commit()
        await gateway.unload(profile)
        report["profiles"][profile] = {
            "model": gateway.profiles[profile].model,
            "mean_latency_seconds": round(
                statistics.mean(item["latency_seconds"] for item in outputs),
                4,
            ),
            "punctuation_compliance": round(
                statistics.mean(
                    item["metrics"]["punctuation_compliance"]
                    for item in outputs
                ),
                4,
            ),
            "assistantese_rate": round(
                statistics.mean(item["metrics"]["assistantese"] for item in outputs),
                4,
            ),
            "length_compliance": round(
                statistics.mean(
                    item["metrics"]["length_compliance"] for item in outputs
                ),
                4,
            ),
            "outputs": outputs,
        }

    finished_at = datetime.now(timezone.utc)
    report["finished_at"] = finished_at.isoformat()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"responder_{run_id}.json"
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary = {
        profile: {
            key: value
            for key, value in values.items()
            if key != "outputs"
        }
        for profile, values in report["profiles"].items()
    }
    with db.connect(settings.db_path) as conn:
        conn.execute(
            """
            UPDATE evaluation_runs
            SET finished_at = ?, summary_json = ?
            WHERE id = ?
            """,
            (
                finished_at.isoformat(),
                json.dumps(summary, ensure_ascii=False),
                run_id,
            ),
        )
        conn.commit()
    await gateway.close()
    print(f"run_id={run_id} report={output_path}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    backend_dir = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=backend_dir / "config.yml")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=backend_dir / "data" / "eval" / "responder_phase7.json",
    )
    parser.add_argument(
        "--profiles",
        default="qwen35_4b,qwen3_rpg_4b_q4,qwen35_9b_q4",
    )
    parser.add_argument("--judge-profile")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=backend_dir / "data" / "eval" / "runs",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(benchmark(parse_args())))
