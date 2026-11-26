from __future__ import annotations

import time
from contextvars import ContextVar, Token
from typing import TypeVar
from urllib.parse import urlsplit

from .local_models import ollama_origin

import httpx
from pydantic import BaseModel

from .config import ModelProfileConfig, Settings
from .failures import ServiceFailure
from .llm import parse_json
from .models import ChatMessage


StructuredResult = TypeVar("StructuredResult", bound=BaseModel)


class ModelGateway:
    """OpenAI-compatible external generation for named model profiles."""

    _RECORD_LIMIT = 256

    def __init__(
        self,
        profiles: dict[str, ModelProfileConfig],
        *,
        client: httpx.AsyncClient | None = None,
        record_limit: int | None = 256,
    ):
        self.profiles = profiles
        self._client = client or httpx.AsyncClient(timeout=600.0)
        self._owns_client = client is None
        self.call_records: list[dict[str, object]] = []
        self.record_limit = record_limit
        self._current_call_record: ContextVar[dict[str, object] | None] = ContextVar(
            "model_gateway_current_call_record",
            default=None,
        )
        self._captured_call_records: ContextVar[list[dict[str, object]] | None] = (
            ContextVar("model_gateway_captured_call_records", default=None)
        )

    def current_call_record(self) -> dict[str, object] | None:
        record = self._current_call_record.get()
        return dict(record) if record is not None else None

    def clear_current_call_record(self) -> None:
        self._current_call_record.set(None)

    def start_call_capture(self) -> Token:
        return self._captured_call_records.set([])

    def finish_call_capture(self, token: Token) -> list[dict[str, object]]:
        records = list(self._captured_call_records.get() or [])
        self._captured_call_records.reset(token)
        return records

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def generate(
        self,
        profile_name: str,
        messages: list[ChatMessage],
        *,
        json_object: bool = False,
    ) -> str:
        profile = self.profiles[profile_name]
        try:
            if profile.provider == "openai_compatible":
                return await self._openai_text(profile, messages, json_object=json_object)
            raise ValueError(f"不支持的 model provider: {profile.provider}")
        except ServiceFailure:
            raise
        except httpx.TimeoutException as exc:
            raise ServiceFailure(
                "provider_timeout",
                f"model profile {profile_name} timed out",
                retryable=True,
                status_code=504,
            ) from exc
        except httpx.HTTPError as exc:
            raise ServiceFailure(
                "provider_transport_error",
                f"model profile {profile_name} transport failed",
                retryable=True,
            ) from exc
        except RuntimeError as exc:
            raise ServiceFailure(
                "provider_http_error",
                f"model profile {profile_name} returned an HTTP error",
                retryable=True,
                status_code=502,
            ) from exc
        except (KeyError, IndexError, TypeError) as exc:
            raise ServiceFailure(
                "provider_invalid_response",
                f"model profile {profile_name} returned an invalid response",
                retryable=True,
                status_code=502,
            ) from exc

    async def generate_json(
        self,
        profile_name: str,
        messages: list[ChatMessage],
        schema: type[StructuredResult],
    ) -> StructuredResult:
        result, _ = await self.generate_json_with_status(
            profile_name, messages, schema
        )
        return result

    async def generate_json_with_status(
        self,
        profile_name: str,
        messages: list[ChatMessage],
        schema: type[StructuredResult],
    ) -> tuple[StructuredResult, list[str]]:
        profile = self.profiles[profile_name]
        return await self._generate_json_once(profile, messages, schema), []

    async def _generate_json_once(
        self,
        profile: ModelProfileConfig,
        messages: list[ChatMessage],
        schema: type[StructuredResult],
    ) -> StructuredResult:
        if profile.provider == "openai_compatible":
            return await self._openai_json(profile, messages, schema)
        raise ValueError(f"不支持的 model provider: {profile.provider}")

    async def _openai_json(
        self,
        profile: ModelProfileConfig,
        messages: list[ChatMessage],
        schema: type[StructuredResult],
    ) -> StructuredResult:
        payload = {
            "model": profile.model,
            "messages": [message.model_dump() for message in messages],
            "temperature": profile.temperature,
            "top_p": profile.top_p,
            "max_tokens": profile.max_tokens,
            "response_format": {"type": "json_object"},
        }
        self._apply_provider_capabilities(profile, payload)
        started = time.perf_counter()
        body: dict[str, object] | None = None
        try:
            response = await self._client.post(
                f"{profile.endpoint}/chat/completions",
                headers={"Authorization": f"Bearer {profile.api_key}"} if profile.api_key else {},
                json=payload,
                timeout=profile.request_timeout_seconds,
            )
            if response.is_error:
                raise RuntimeError(
                    "OpenAI-compatible 调用失败"
                    f"（{response.status_code}）：{response.text}"
                )
            body = response.json()
            content = str(body["choices"][0]["message"]["content"] or "")
            result = schema.model_validate(parse_json(content))
        except Exception as exc:
            self._record_failure(
                profile, "json", time.perf_counter() - started, exc, body=body
            )
            raise
        self._record_call(profile, "json", body, time.perf_counter() - started)
        return result

    async def _openai_text(
        self,
        profile: ModelProfileConfig,
        messages: list[ChatMessage],
        *,
        json_object: bool = False,
    ) -> str:
        payload = {
            "model": profile.model,
            "messages": [message.model_dump() for message in messages],
            "temperature": profile.temperature,
            "top_p": profile.top_p,
            "max_tokens": profile.max_tokens,
        }
        if json_object:
            payload["response_format"] = {"type": "json_object"}
        self._apply_provider_capabilities(profile, payload)
        started = time.perf_counter()
        body: dict[str, object] | None = None
        try:
            response = await self._client.post(
                f"{profile.endpoint}/chat/completions",
                headers={"Authorization": f"Bearer {profile.api_key}"} if profile.api_key else {},
                json=payload,
                timeout=profile.request_timeout_seconds,
            )
            if response.is_error:
                raise RuntimeError(
                    "OpenAI-compatible 调用失败"
                    f"（{response.status_code}）：{response.text}"
                )
            body = response.json()
            result = str(body["choices"][0]["message"]["content"] or "").strip()
        except Exception as exc:
            self._record_failure(
                profile, "text", time.perf_counter() - started, exc, body=body
            )
            raise
        self._record_call(profile, "text", body, time.perf_counter() - started)
        return result

    def _record_call(
        self,
        profile: ModelProfileConfig,
        kind: str,
        body: dict[str, object],
        latency_seconds: float,
        *,
        status: str = "success",
        error_type: str | None = None,
    ) -> None:
        usage = body.get("usage")
        if not isinstance(usage, dict):
            usage = {
                "prompt_tokens": body.get("prompt_eval_count"),
                "completion_tokens": body.get("eval_count"),
            }
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")
        prompt_details = usage.get("prompt_tokens_details")
        cached_tokens = (
            prompt_details.get("cached_tokens")
            if isinstance(prompt_details, dict)
            else None
        )
        cache_hit_tokens = usage.get("prompt_cache_hit_tokens", cached_tokens)
        cache_miss_tokens = usage.get("prompt_cache_miss_tokens")
        if total_tokens is None and isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
            total_tokens = prompt_tokens + completion_tokens
        choices = body.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else {}
        if not isinstance(choice, dict):
            choice = {}
        message = choice.get("message")
        if not isinstance(message, dict):
            message = {}
        content = message.get("content")
        reasoning_content = message.get("reasoning_content")
        record = {
            "provider": profile.provider,
            "model": profile.model,
            "kind": kind,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "input_cache_hit_tokens": cache_hit_tokens,
            "input_cache_miss_tokens": cache_miss_tokens,
            "latency_seconds": round(latency_seconds, 6),
            "status": status,
            "error_type": error_type,
            "finish_reason": choice.get("finish_reason"),
            "content_chars": len(content) if isinstance(content, str) else 0,
            "reasoning_chars": (
                len(reasoning_content) if isinstance(reasoning_content, str) else 0
            ),
        }
        self.call_records.append(record)
        if self.record_limit is not None and len(self.call_records) > self.record_limit:
            del self.call_records[: len(self.call_records) - self.record_limit]
        captured = self._captured_call_records.get()
        if captured is not None:
            captured.append(record)
        self._current_call_record.set(record)

    def _record_failure(
        self,
        profile: ModelProfileConfig,
        kind: str,
        latency_seconds: float,
        exc: Exception,
        *,
        body: dict[str, object] | None = None,
    ) -> None:
        self._record_call(
            profile,
            kind,
            body or {},
            latency_seconds,
            status="failed",
            error_type=type(exc).__name__,
        )

    @staticmethod
    def _apply_provider_capabilities(
        profile: ModelProfileConfig,
        payload: dict[str, object],
    ) -> None:
        hostname = (urlsplit(profile.endpoint).hostname or "").casefold()
        if ollama_origin(profile.endpoint) and not profile.think:
            payload["reasoning_effort"] = "none"
        if hostname == "deepseek.com" or hostname.endswith(".deepseek.com"):
            payload["thinking"] = {
                "type": "enabled" if profile.think else "disabled"
            }


def build_model_gateway(settings: Settings, *, record_limit: int | None = 256) -> ModelGateway:
    profiles = {"planner": settings.planner}
    if settings.responder is not None:
        profiles["responder"] = settings.responder
    return ModelGateway(profiles, record_limit=record_limit)
