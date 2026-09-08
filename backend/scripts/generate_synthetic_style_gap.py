"""Generate and explicitly label small synthetic fallbacks for sparse style scenes."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import shutil
import sqlite3
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from hanser_agent.agent.context_builder import ContextBuilder  # noqa: E402
from hanser_agent.config import load_settings  # noqa: E402
from hanser_agent.model_gateway import ModelGateway  # noqa: E402
from hanser_agent.models import ChatMessage, DialoguePlan  # noqa: E402
from hanser_agent.persona import PersonaCompiler  # noqa: E402
from hanser_agent.persona.data_pipeline import label_style  # noqa: E402
from hanser_agent.responder import HanserResponder, StyleValidator  # noqa: E402


GENERATION = "p0-gap-synthetic-001"
SCENARIOS = [
    ("greeting", "早上好呀，我来报到啦"),
    ("greeting", "晚上好，我下班来报到了"),
    ("greeting", "你好呀，第一次来直播间"),
    ("greeting", "好久不见，我来看看你"),
    ("greeting", "嗨，我又来找你聊天啦"),
    ("greeting", "晚安，睡前来和你说一声"),
    ("greeting", "憨憨晚上好，今天辛苦啦"),
    ("greeting", "早安，新的一天开始啦"),
    ("comfort", "我今天工作出了好多错，有点难过"),
    ("comfort", "最近压力特别大，感觉好累"),
    ("comfort", "我考试没考好，心里很委屈"),
    ("comfort", "刚和朋友吵架了，现在想哭"),
    ("comfort", "我失恋了，可以陪我说两句吗"),
    ("comfort", "努力了很久还是失败了，能安慰我一下吗"),
    ("comfort", "今天被人误解了，真的很伤心"),
    ("comfort", "最近总觉得自己不够好，有点撑不住了"),
    ("comfort", "项目赶不完，我累坏了"),
    ("comfort", "一个人搬到新城市，有点孤单和难过"),
    ("comfort", "我鼓起勇气做的事被拒绝了，好失落"),
    ("comfort", "今天什么都不顺，想听你哄哄我"),
]
SPEAKER_LEAK = re.compile(
    r"(?:^|\n)\s*(?:用户|assistant|system|Hanser|憨憨|Q|A|yousa|包包|小缘)[：:]",
    re.IGNORECASE,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes())
    return digest.hexdigest()


def _validate(scene: str, prompt: str, response: str) -> list[str]:
    failures: list[str] = []
    if not response.strip(): failures.append("empty")
    if len(response) > 320: failures.append("too_long")
    if SPEAKER_LEAK.search(response): failures.append("speaker_leak")
    if "http://" in response or "https://" in response: failures.append("url")
    if any(text in response for text in (
        "我都看在眼里", "上次直播", "我们以前", "还记得我们", "憨憨以前", "憨憨也经常",
        "刚醒", "准备睡", "最近还行", "忙着唱歌", "今天天气",
    )):
        failures.append("invented_history")
    if re.search(r"憨憨.{0,8}(?:最近|今天|准备|有时候|刚)", response):
        failures.append("invented_current_state")
    if scene == "comfort" and any(text in response for text in (
        "而已", "别想那么多", "值得这么难过", "当个废物", "比那些", "想开点",
        "不是你的问题", "出错说明",
    )):
        failures.append("minimizes_feeling")
    if scene == "comfort" and label_style(prompt, response)["scene"] != "comfort":
        failures.append("prompt_scene_mismatch")
    return failures


async def generate(args: argparse.Namespace) -> None:
    if args.records.exists():
        raise FileExistsError(args.records)
    settings = load_settings(args.config)
    if settings.responder is None:
        raise ValueError("responder profile is required")
    profile = replace(
        settings.responder, model="deepseek-v4-flash", think=False,
        temperature=0.55, top_p=0.9, max_tokens=256,
    )
    gateway = ModelGateway({"synthetic": profile})
    builder = ContextBuilder(
        PersonaCompiler(ROOT / "backend/hanser_agent/prompts/persona"),
        settings.context,
        provider_context_window=profile.context_window,
        provider_max_output_tokens=profile.max_tokens,
    )
    responder = HanserResponder(
        gateway,
        StyleValidator(ROOT / "backend/hanser_agent/prompts/persona/style_constraints.yaml"),
        profile_name="synthetic",
    )
    semaphore = asyncio.Semaphore(args.concurrency)

    async def one(index: int, scene: str, prompt: str) -> dict[str, object]:
        mode = "emotional" if scene == "comfort" else "casual"
        plan = DialoguePlan(
            intent="relationship" if scene == "comfort" else "chitchat",
            need_wiki=False, standalone_query=prompt, keywords=[],
            response_mode=mode, fact_sensitivity="low", target_length="short",
        )
        context = builder.build(current_message=prompt, history=[], plan=plan)
        generation_constraint = ChatMessage(
            role="system",
            content=(
                "[SYNTHETIC STYLE SAMPLE CONSTRAINT]\n"
                "只输出对当前用户消息的自然答复，不解释任务。不得编造你和用户共同经历、"
                "对用户长期行为的观察或具体的个人履历事件。问候控制在1至2句；安慰先承认"
                "感受，再给一个温和可执行的选择，不淡化对方情绪，不强迫积极。"
                "反例：‘一次考试而已’、‘想开点’、‘你比不敢尝试的人强’、编造‘我以前也经常’；"
                "正例：承认‘这确实很难受’，再邀请对方选择倾诉、休息或稍后处理。"
                "不要声称当前天气、刚睡醒、正在忙什么、准备睡觉等真实状态，也不要替用户断言"
                "原因或把错误解释成某种优点。"
            ),
        )
        context = context.model_copy(update={
            "messages": [*context.messages[:-1], generation_constraint, context.messages[-1]],
        })
        async with semaphore:
            try:
                result = await asyncio.wait_for(responder.respond(context), args.timeout)
                failures = _validate(scene, prompt, result.text)
                print(f"{index:02d} {scene} {'PASS' if not failures else failures}", flush=True)
                return {
                    "index": index, "scene": scene, "prompt": prompt,
                    "response": result.text, "raw_response": result.raw_text,
                    "validator_actions": result.validator_actions,
                    "failures": failures, "prompt_sha256": context.prompt_sha256,
                }
            except Exception as error:
                return {"index": index, "scene": scene, "prompt": prompt,
                        "response": "", "failures": [type(error).__name__],
                        "error": str(error)[:500]}
    try:
        records = await asyncio.gather(*(
            one(index, scene, prompt)
            for index, (scene, prompt) in enumerate(SCENARIOS, 1)
        ))
    finally:
        await gateway.close()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generation": GENERATION, "model": profile.model, "thinking": profile.think,
        "concurrency": args.concurrency, "records": records,
        "passed": sum(not item["failures"] for item in records),
        "failed": sum(bool(item["failures"]) for item in records),
    }
    args.records.parent.mkdir(parents=True, exist_ok=True)
    args.records.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: payload[key] for key in ("passed", "failed", "model", "concurrency")}))


def apply_records(args: argparse.Namespace) -> None:
    payload = json.loads(args.records.read_text(encoding="utf-8"))
    records = [item for item in payload["records"] if not item["failures"]]
    if len(records) != len(SCENARIOS):
        raise SystemExit("not all synthetic records passed; candidate was not modified")
    database = args.database.resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = database.with_name(f"{database.stem}.pre_synthetic.{stamp}{database.suffix}")
    shutil.copy2(database, backup)
    inserted: list[int] = []
    with sqlite3.connect(database) as conn:
        existing = conn.execute(
            "SELECT count(*) FROM style_examples WHERE index_generation=?", (GENERATION,)
        ).fetchone()[0]
        if existing:
            raise SystemExit(f"generation already exists ({existing} rows)")
        for item in records:
            labels = label_style(item["prompt"], item["response"])
            response_mode = "emotional" if item["scene"] == "comfort" else "casual"
            source_ref = "synthetic:deepseek-v4-flash:" + hashlib.sha256(
                (item["prompt"] + "\n" + item["response"]).encode("utf-8")
            ).hexdigest()[:20]
            cursor = conn.execute(
                """
                INSERT INTO style_examples
                  (prompt,response,context_before,context_after,scene,speech_act,tone_json,
                   relationship_level,energy,teasing_level,answer_length,response_mode,
                   source_type,source_ref,authenticity_score,quality_score,metadata_json,
                   review_status,source_tier,source_speaker,source_user_turn,
                   source_response_turn,source_raw_text,cleaning_operations_json,
                   reviewer_id,review_notes,reviewed_at,index_generation)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),?)
                """,
                (
                    item["prompt"], item["response"], "", "", item["scene"],
                    "comfort" if item["scene"] == "comfort" else "greet",
                    json.dumps(labels["tone"], ensure_ascii=False), "audience",
                    labels["energy"], labels["teasing_level"], labels["answer_length"],
                    response_mode, "synthetic", source_ref, 0.5, 0.82,
                    json.dumps({"generator": payload["model"], "explicit_synthetic": True}, ensure_ascii=False),
                    "approved", "synthetic", "hanser", item["prompt"], item["response"],
                    json.dumps({"prompt": item["prompt"], "response": item["response"]}, ensure_ascii=False),
                    json.dumps(["model_generated", "style_normalized"], ensure_ascii=False),
                    "owner_authorized_synthetic:2026-09-06",
                    "Owner-authorized synthetic fallback for a corpus gap; never a real quote.",
                    GENERATION,
                ),
            )
            inserted.append(int(cursor.lastrowid))
        conn.commit()
    report = {
        "database": str(database), "database_sha256": _sha256(database),
        "backup": str(backup), "records_sha256": _sha256(args.records),
        "inserted_count": len(inserted), "inserted_ids": inserted,
        "generation": GENERATION, "production_switched": False,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def curate(args: argparse.Namespace) -> None:
    if args.records.exists():
        raise FileExistsError(args.records)
    candidates: dict[tuple[str, str], list[dict[str, object]]] = {}
    for source in args.curate_from:
        payload = json.loads(source.read_text(encoding="utf-8"))
        for item in payload["records"]:
            candidates.setdefault((item["scene"], item["prompt"]), []).append(item)
    selected: list[dict[str, object]] = []
    for index, (scene, prompt) in enumerate(SCENARIOS, 1):
        options = candidates.get((scene, prompt), [])
        passing = [
            item for item in options
            if not _validate(scene, prompt, str(item.get("response", "")))
        ]
        if not passing:
            raise SystemExit(f"no passing generated candidate for scenario {index}: {prompt}")
        chosen = dict(passing[0])
        chosen["index"] = index
        chosen["failures"] = []
        selected.append(chosen)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generation": GENERATION, "model": "deepseek-v4-flash", "thinking": False,
        "curated_from": [str(path.resolve()) for path in args.curate_from],
        "records": selected, "passed": len(selected), "failed": 0,
    }
    args.records.parent.mkdir(parents=True, exist_ok=True)
    args.records.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": len(selected), "failed": 0, "curated": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--curate-from", type=Path, nargs="+")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=180.0)
    parsed = parser.parse_args()
    if parsed.curate_from:
        curate(parsed)
    elif parsed.apply:
        if parsed.database is None or parsed.report is None:
            parser.error("--database and --report are required with --apply")
        apply_records(parsed)
    else:
        asyncio.run(generate(parsed))
