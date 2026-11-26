from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

from hanser_agent.agent.planner import DialoguePlanner, PlannerDecision
from hanser_agent.config import load_settings
from hanser_agent.llm import parse_json
from hanser_agent.models import ChatMessage


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay one failed Planner input directly against the API."
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--replay-result", type=Path, required=True)
    parser.add_argument("--turn", type=int, required=True)
    parser.add_argument("--attempts", type=int, default=3)
    return parser.parse_args()


def _case(result_path: Path, turn_index: int) -> tuple[str, list[ChatMessage]]:
    result = json.loads(result_path.read_text(encoding="utf-8"))
    turns = result.get("turns", [])
    target = next(
        (item for item in turns if int(item.get("turn", -1)) == turn_index),
        None,
    )
    if target is None:
        raise RuntimeError(f"turn {turn_index} not found in {result_path}")
    history: list[ChatMessage] = []
    for item in turns:
        if int(item.get("turn", -1)) >= turn_index:
            break
        history.extend([
            ChatMessage(role="user", content=str(item["user"])),
            ChatMessage(role="assistant", content=str(item["candidate"])),
        ])
    return str(target["user"]), history


async def _call(
    client: httpx.AsyncClient,
    *,
    endpoint: str,
    api_key: str,
    model: str,
    temperature: float,
    top_p: float,
    max_tokens: int,
    messages: list[ChatMessage],
    variant: str,
    attempt: int,
) -> dict[str, object]:
    payload = {
        "model": model,
        "messages": [message.model_dump() for message in messages],
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
    }
    started = time.perf_counter()
    response = await client.post(
        f"{endpoint}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
    )
    record: dict[str, object] = {
        "variant": variant,
        "attempt": attempt,
        "status_code": response.status_code,
        "latency_seconds": round(time.perf_counter() - started, 3),
    }
    if response.is_error:
        record["error"] = response.text[:1000]
        return record
    body = response.json()
    choice = body["choices"][0]
    message = choice["message"]
    content = str(message.get("content") or "")
    record.update({
        "returned_model": body.get("model"),
        "finish_reason": choice.get("finish_reason"),
        "content": content,
        "reasoning_content": message.get("reasoning_content"),
        "usage": body.get("usage"),
    })
    try:
        parsed = parse_json(content)
        record["json_parse"] = "ok"
        try:
            PlannerDecision.model_validate(parsed)
            record["schema_validation"] = "ok"
        except Exception as exc:
            record["schema_validation"] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        record["json_parse"] = f"{type(exc).__name__}: {exc}"
    return record


async def main() -> None:
    args = _args()
    settings = load_settings(args.config.resolve())
    current_message, history = _case(args.replay_result.resolve(), args.turn)
    planner = DialoguePlanner(None)  # type: ignore[arg-type]
    exact_messages = planner.build_messages(current_message, history)
    first_pair_messages = planner.build_messages(current_message, history[:2])
    latest_pair_messages = planner.build_messages(current_message, history[-2:])
    user_history_messages = planner.build_messages(
        current_message,
        [message for message in history if message.role == "user"],
    )
    placeholder_history_messages = planner.build_messages(current_message, [
        ChatMessage(role="user", content="历史用户消息一"),
        ChatMessage(role="assistant", content="历史助手消息一"),
        ChatMessage(role="user", content="历史用户消息二"),
        ChatMessage(role="assistant", content="历史助手消息二"),
    ])
    generic_first_pair_messages = planner.build_messages(current_message, [
        ChatMessage(role="user", content="历史用户消息一"),
        ChatMessage(role="assistant", content="历史助手消息一"),
        *history[-2:],
    ])
    generic_latest_pair_messages = planner.build_messages(current_message, [
        *history[:2],
        ChatMessage(role="user", content="历史用户消息二"),
        ChatMessage(role="assistant", content="历史助手消息二"),
    ])
    final_reminder_messages = [
        *exact_messages[:-1],
        ChatMessage(
            role="user",
            content=(
                current_message
                + "\n\n[PLANNER OUTPUT] 这是路由判断任务 现在只输出一个JSON对象"
            ),
        ),
    ]
    no_history_messages = planner.build_messages(current_message, [])
    variants = (
        ("exact_full_history", exact_messages),
        ("first_turn_pair", first_pair_messages),
        ("latest_turn_pair", latest_pair_messages),
        ("user_history_only", user_history_messages),
        ("four_roles_placeholder_content", placeholder_history_messages),
        ("generic_first_pair", generic_first_pair_messages),
        ("generic_latest_pair", generic_latest_pair_messages),
        ("exact_history_final_json_reminder", final_reminder_messages),
        ("same_user_without_history", no_history_messages),
    )
    records: list[dict[str, object]] = []
    async with httpx.AsyncClient(timeout=settings.planner.request_timeout_seconds) as client:
        for variant, messages in variants:
            for attempt in range(1, max(1, args.attempts) + 1):
                records.append(await _call(
                    client,
                    endpoint=settings.planner.endpoint,
                    api_key=settings.planner.api_key,
                    model=settings.planner.model,
                    temperature=settings.planner.temperature,
                    top_p=settings.planner.top_p,
                    max_tokens=settings.planner.max_tokens,
                    messages=messages,
                    variant=variant,
                    attempt=attempt,
                ))

    print(json.dumps({
        "model": settings.planner.model,
        "turn": args.turn,
        "current_message": current_message,
        "history": [message.model_dump() for message in history],
        "planner_system_prompt": planner.prompt,
        "parameters": {
            "temperature": settings.planner.temperature,
            "top_p": settings.planner.top_p,
            "max_tokens": settings.planner.max_tokens,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
        },
        "records": records,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
