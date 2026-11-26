from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .config import LLMTaskConfig, Settings
from .models import ChatMessage


class LLMClient:
    """OpenAI-compatible /chat/completions client.

    max_tokens is task-scoped, not inferred from context-window size. This avoids
    the 1,000,000-token bug from the original C# model-name lookup table.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = httpx.AsyncClient(timeout=600.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def chat(self, messages: list[ChatMessage], task: LLMTaskConfig, *, json_mode: bool = False) -> str:
        payload: dict[str, Any] = {
            "model": task.model,
            "messages": [m.model_dump() for m in messages],
            "temperature": task.temperature,
            "top_p": task.top_p,
            "max_tokens": task.max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        response = await self._client.post(
            f"{self.settings.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.settings.api_key}"},
            json=payload,
        )
        if response.is_error:
            raise RuntimeError(f"LLM 接口调用失败（{response.status_code}）：{response.text}")
        data = response.json()
        return str(data["choices"][0]["message"]["content"] or "").strip()

    async def chat_json(self, messages: list[ChatMessage], task: LLMTaskConfig) -> Any:
        last_error: Exception | None = None
        for _ in range(2):
            text = await self.chat(messages, task, json_mode=True)
            try:
                return parse_json(text)
            except Exception as exc:  # retry once, matching current C# behavior
                last_error = exc
        raise RuntimeError(f"模型两次均未返回有效 JSON：{last_error}")


def parse_json(text: str) -> Any:
    value = text.strip()
    value = re.sub(r"^```[a-zA-Z]*\s*", "", value)
    value = re.sub(r"\s*```$", "", value).strip()
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        starts = [i for i in (value.find("{"), value.find("[")) if i >= 0]
        if not starts:
            raise
        start = min(starts)
        end = max(value.rfind("}"), value.rfind("]"))
        if end <= start:
            raise
        return json.loads(value[start : end + 1])
