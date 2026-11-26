from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

import httpx
import yaml


MODEL_IDS = (
    "deepseek-flash",
    "DeepSeek-V4.1-Flash",
    "deepseek-v4-flash",
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe DeepSeek model IDs without starting the application."
    )
    parser.add_argument("--config", type=Path, required=True)
    return parser.parse_args()


def _connection(config_path: Path) -> tuple[str, str]:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    llm = raw.get("llm", {}) or {}
    base_url = str(
        llm.get("base_url")
        or os.getenv("OPENAI_BASE_URL")
        or "https://api.deepseek.com"
    ).rstrip("/")
    api_key = str(llm.get("api_key") or os.getenv("OPENAI_API_KEY") or "")
    if not api_key:
        raise RuntimeError("missing llm.api_key / OPENAI_API_KEY")
    return base_url, api_key


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:500]
    return json.dumps(body, ensure_ascii=False)[:500]


async def _post(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    model: str,
    mode: str,
) -> dict[str, object]:
    if mode == "plain":
        payload: dict[str, object] = {
            "model": model,
            "messages": [{"role": "user", "content": "Reply exactly: OK"}],
            "max_tokens": 16,
        }
    else:
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "Return one JSON object and no other text.",
                },
                {"role": "user", "content": 'Return {"ok":true}'},
            ],
            "max_tokens": 64,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
        }
    started = time.perf_counter()
    try:
        response = await client.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=payload,
        )
    except httpx.HTTPError as exc:
        return {
            "model": model,
            "mode": mode,
            "transport_error": type(exc).__name__,
            "latency_seconds": round(time.perf_counter() - started, 3),
        }
    result: dict[str, object] = {
        "model": model,
        "mode": mode,
        "status_code": response.status_code,
        "latency_seconds": round(time.perf_counter() - started, 3),
    }
    if response.is_error:
        result["error"] = _error_text(response)
        return result
    body = response.json()
    choice = body.get("choices", [{}])[0]
    message = choice.get("message", {})
    result.update({
        "returned_model": body.get("model"),
        "finish_reason": choice.get("finish_reason"),
        "content": message.get("content"),
        "reasoning_chars": len(message.get("reasoning_content") or ""),
    })
    return result


async def main() -> None:
    args = _args()
    base_url, api_key = _connection(args.config.resolve())
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        models_started = time.perf_counter()
        models_response = await client.get(f"{base_url}/models", headers=headers)
        models_result: dict[str, object] = {
            "status_code": models_response.status_code,
            "latency_seconds": round(time.perf_counter() - models_started, 3),
        }
        if models_response.is_error:
            models_result["error"] = _error_text(models_response)
        else:
            body = models_response.json()
            models_result["ids"] = [
                item.get("id")
                for item in body.get("data", [])
                if isinstance(item, dict)
            ]

        calls = []
        for model in MODEL_IDS:
            calls.append(await _post(client, base_url, headers, model, "plain"))
            calls.append(await _post(client, base_url, headers, model, "planner_json"))

    print(json.dumps({
        "base_url": base_url,
        "models_endpoint": models_result,
        "calls": calls,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
