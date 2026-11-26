from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from hanser_agent.config import load_settings
from hanser_agent.desktop_api import create_desktop_app
from hanser_agent.models import ChatMessage
from hanser_agent.persona.expression import observe_recent_expressions
from hanser_agent.persona.schemas import LanguageControl
from hanser_agent.responder.validator import StyleValidator


SOURCE_CONVERSATION_ID = "0aa823d6-37cd-4f70-98ad-95238a7525e2"
_XING_OPENER = re.compile(r"(?:^|\n)\s*(?:行吧|行了|行)(?=$|[\s，,。！？!?；;])")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run exactly one isolated adaptive Persona dialogue replay."
    )
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def _messages(db_path: Path, conversation_id: str) -> list[dict[str, object]]:
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(
            """
            SELECT role, content, trace_id, style_example_ids_json, turn_index
            FROM messages
            WHERE conversation_id = ?
            ORDER BY turn_index
            """,
            (conversation_id,),
        )]
    finally:
        connection.close()


def _snapshot(db_path: Path, request_id: str) -> dict[str, object]:
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT snapshot_json FROM reply_snapshots WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        return json.loads(str(row[0])) if row else {}
    finally:
        connection.close()


def _existing_replay(db_path: Path) -> tuple[str | None, dict[str, dict[str, object]]]:
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        conversation = connection.execute(
            "SELECT id FROM conversations WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1",
            ("persona-replay-local",),
        ).fetchone()
        rows = connection.execute(
            """
            SELECT request_id, response_json
            FROM request_executions
            WHERE user_id = ? AND status = 'completed'
            """,
            ("persona-replay-local",),
        ).fetchall()
        return (
            str(conversation["id"]) if conversation else None,
            {
                str(row["request_id"]): json.loads(str(row["response_json"]))
                for row in rows
            },
        )
    finally:
        connection.close()


def _adaptive_user_turn(index: int, previous_reply: str) -> tuple[str, str]:
    """Choose one coherent next turn from the model's actual previous reply."""

    previous = " ".join(previous_reply.split())
    if index == 1:
        return "晚上好~", "开启问候场景"
    if index == 2:
        if any(marker in previous for marker in ("吃", "饭", "晚餐", "夜宵")):
            return "刚吃完饭呀 来找你聊聊天", "回答上一句的吃饭话题"
        if any(marker in previous for marker in ("睡", "醒", "困")):
            return "没睡啦 刚吃完饭就来找你了", "回答上一句的作息话题"
        return "刚吃完饭呀 就过来找你聊聊天了", "承接问候并主动给出近况"
    if index == 3:
        if any(marker in previous for marker in ("吃的什么", "吃了什么", "什么饭")):
            return "泡面 懒得做饭了", "直接回答上一句的具体问题"
        return "其实就吃了个泡面 懒得做饭了", "补充上一句正在聊的吃饭细节"
    if index == 4:
        if any(marker in previous for marker in ("营养", "少吃", "别吃", "不健康", "胃")):
            return "诶呀 偶尔一顿嘛 你不会真要管我吧~", "回应上一句对泡面的担心"
        if any(marker in previous for marker in ("管", "念", "说你", "糊弄")):
            return "你自己吃饭不也会糊弄嘛", "沿用上一句的管饭玩笑反问"
        return "你这语气听着怎么像要管我呀", "追问上一句对泡面的态度"
    if index == 5:
        if any(marker in previous for marker in ("不管", "管不着", "随你", "没管")):
            return "那你刚才说得跟真的一样 我还以为你不高兴了", "回应上一句否认管束"
        return "好好好 那我下次尽量不拿泡面糊弄", "顺着上一句的实际态度让步"
    if index == 6:
        if any(marker in previous for marker in ("生气", "不高兴", "不至于")):
            return "你看 你还是有点生气了吧", "追问上一句明确提到的情绪"
        return "怎么 感觉你突然认真起来了", "确认上一句语气是否转严肃"
    if index == 7:
        return "好家伙 你怎么又开始抠字眼了", "制造适合一次轻粗口的玩笑机会"
    if index == 8:
        if any(marker in previous for marker in ("我靠", "卧槽", "妈的", "他妈", "老子", "老娘")):
            return "诶呀 怎么还爆粗了 我又没真说你~", "回应上一句实际出现的轻粗口"
        return "诶呀 嘴还挺硬 我又没真说你~", "回应上一句未出现粗口时的强硬语气"
    if index == 9:
        if any(marker in previous for marker in ("生气", "气", "不爽", "不高兴")):
            return "你看 这不还是生气了嘛", "只在上一句出现情绪词时继续判断"
        return "你看 你说着说着又认真了", "根据上一句语气继续轻微逗趣"
    if index == 10:
        if any(marker in previous for marker in ("认定", "结论", "非说", "说了算")):
            return "诶呀 我收回还不行嘛 我错了我错了", "回应上一句对武断判断的反驳"
        return "诶呀 我错了我错了 不逗你了", "根据上一句的不满及时收回玩笑"
    if index == 11:
        if any(marker in previous for marker in ("没生气", "没记", "算了", "原谅")):
            return "那我就当你原谅我了哦~", "承接上一句已经缓和的态度"
        return "那要怎么样才肯原谅我嘛~", "承接上一句尚未缓和的态度"
    if index == 12:
        if any(marker in previous for marker in ("原谅", "没事", "不计较", "翻篇")):
            return "好 那这次就真的翻篇啦", "确认上一句已经接受和解"
        return "我都认错了 你还真记着呀", "继续回应上一句没有接受和解"
    if index == 13:
        if any(marker in previous for marker in ("翻篇", "不提", "算了", "结束")):
            return "你不会转头又翻旧账吧", "围绕上一句的结束承诺追问"
        return "那我可当你没有真生气了", "根据上一句尚未明确结束作确认"
    return "好呀好呀 那我先去洗碗啦 喵~", "依据当前气氛自然结束连续对话"


def _baseline_fallback_count(db_path: Path, assistant_rows: list[dict[str, object]]) -> int:
    trace_ids = [str(row["trace_id"]) for row in assistant_rows if row.get("trace_id")]
    if not trace_ids:
        return 0
    placeholders = ",".join("?" for _ in trace_ids)
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            f"SELECT response_json FROM request_executions WHERE trace_id IN ({placeholders})",
            trace_ids,
        ).fetchall()
        return sum(
            "planner_deterministic_fallback"
            in json.loads(str(row[0])).get("degraded_reasons", [])
            for row in rows
        )
    finally:
        connection.close()


def _metrics(
    replies: list[str],
    style_ids: list[str],
    *,
    planner_fallbacks: int,
    validator: StyleValidator,
) -> dict[str, object]:
    profanity_turns = sum(
        observe_recent_expressions([
            ChatMessage(role="assistant", content=text)
        ]).features["profanity"].value == "present"
        for text in replies
    )
    generic_tails = sum(
        "generic_continuation_tail"
        in validator.validate_semantic_output(
            text,
            language_control=LanguageControl(),
        ).violations
        for text in replies
    )
    counts = Counter(style_ids)
    return {
        "assistant_turns": len(replies),
        "planner_fallbacks": planner_fallbacks,
        "profanity_turns": profanity_turns,
        "aiya_turns": sum("诶呀" in text for text in replies),
        "tilde_turns": sum("~" in text or "～" in text for text in replies),
        "stamp_metaphor_turns": sum("盖章" in text for text in replies),
        "xing_family_paragraph_openers": sum(
            len(_XING_OPENER.findall(text)) for text in replies
        ),
        "generic_continuation_tails": generic_tails,
        "style_example_reuses": sum(count - 1 for count in counts.values() if count > 1),
        "most_used_style": (
            {"id": counts.most_common(1)[0][0], "uses": counts.most_common(1)[0][1]}
            if counts else None
        ),
    }


def main() -> None:
    args = _args()
    output_dir = args.output_dir.resolve()
    if (output_dir / "result.json").exists():
        raise FileExistsError(f"refusing to rerun completed output: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    replay_db = output_dir / "replay.db"
    if not replay_db.exists():
        shutil.copy2(args.source_db.resolve(), replay_db)

    source_rows = _messages(args.source_db.resolve(), SOURCE_CONVERSATION_ID)
    source_user_turns = [str(row["content"]) for row in source_rows if row["role"] == "user"]
    baseline_assistant = [row for row in source_rows if row["role"] == "assistant"]
    if not source_user_turns or len(source_user_turns) != len(baseline_assistant):
        raise RuntimeError("source conversation is missing or not a complete paired dialogue")

    settings = replace(load_settings(args.config.resolve()), db_path=replay_db)
    app = create_desktop_app(settings=settings)
    replay_conversation_id, completed_requests = _existing_replay(replay_db)
    replay_conversation_id = replay_conversation_id or str(uuid4())
    turns: list[dict[str, object]] = []
    candidate_style_ids: list[str] = []
    planner_fallbacks = 0
    responder_retries = 0
    candidate_validator_actions: list[str] = []
    token_usage = Counter()

    with TestClient(app) as client:
        for index in range(1, len(source_user_turns) + 1):
            previous_reply = str(turns[-1]["candidate"]) if turns else ""
            user_text, adaptation_basis = _adaptive_user_turn(index, previous_reply)
            request_id = f"dialogue-quality2-turn-{index:02d}"
            payload = completed_requests.get(request_id)
            if payload is None:
                response = client.post(
                    "/v1/chat",
                    json={
                        "conversation_id": replay_conversation_id,
                        "user_id": "persona-replay-local",
                        "message": user_text,
                        "request_id": request_id,
                    },
                )
                if response.status_code != 200:
                    raise RuntimeError(
                        f"turn {index} failed with {response.status_code}: {response.text}"
                    )
                payload = response.json()
            snapshot = _snapshot(replay_db, request_id)
            trace = snapshot.get("persona_trace", {})
            generation = trace.get("generation", {})
            retrieval = trace.get("retrieval", {})
            selected_ids = [str(value) for value in retrieval.get("selected_style_ids", [])]
            candidate_style_ids.extend(selected_ids)
            actions = [str(value) for value in generation.get("validator_actions", [])]
            candidate_validator_actions.extend(actions)
            responder_retries += max(0, int(generation.get("responder_attempts", 1)) - 1)
            planner_fallbacks += int(
                "planner_deterministic_fallback" in payload.get("degraded_reasons", [])
            )
            for key, value in generation.get("token_usage", {}).items():
                if isinstance(value, int) and key != "calls":
                    token_usage[key] += value
            turns.append({
                "turn": index,
                "user": user_text,
                "adaptation_basis": adaptation_basis,
                "candidate": str(payload["text"]),
                "degraded_reasons": payload.get("degraded_reasons", []),
                "dialogue_function": (
                    trace.get("signals", {}).get("values", {}).get("dialogue_function", {}).get("value")
                ),
                "continuation_policy": trace.get("language", {}).get("control", {}).get("continuation_policy"),
                "style_ids": selected_ids,
                "validator_actions": actions,
                "planner_failure_kind": generation.get("planner_failure_kind"),
                "planner_records": generation.get("planner_records", []),
            })

    validator = StyleValidator(
        Path(__file__).parents[1]
        / "hanser_agent"
        / "prompts"
        / "persona"
        / "candidates"
        / "hanser-persona-v2-candidate"
        / "style_constraints.yaml"
    )
    baseline_style_ids = [
        str(value)
        for row in baseline_assistant
        for value in json.loads(str(row["style_example_ids_json"] or "[]"))
    ]
    result = {
        "run_id": output_dir.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_conversation_id": SOURCE_CONVERSATION_ID,
        "dialogue_mode": "adaptive_user_simulation",
        "candidate_conversation_id": replay_conversation_id,
        "persona_package": settings.persona.active_package,
        "model": settings.default_model,
        "historical_source_metrics": _metrics(
            [str(row["content"]) for row in baseline_assistant],
            baseline_style_ids,
            planner_fallbacks=_baseline_fallback_count(
                args.source_db.resolve(), baseline_assistant
            ),
            validator=validator,
        ),
        "candidate_metrics": _metrics(
            [str(turn["candidate"]) for turn in turns],
            candidate_style_ids,
            planner_fallbacks=planner_fallbacks,
            validator=validator,
        ),
        "candidate_runtime": {
            "responder_retries": responder_retries,
            "validator_actions": candidate_validator_actions,
            "token_usage": dict(token_usage),
        },
        "turns": turns,
    }
    (output_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    replay_db.unlink()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
