from __future__ import annotations

import logging
import time
from contextvars import ContextVar
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .config import ModelProfileConfig, Settings
from .failures import ServiceFailure
from .llm import parse_json
from .models import ChatMessage


StructuredResult = TypeVar("StructuredResult", bound=BaseModel)


class ModelGateway:
    """Provider-neutral structured generation for named model profiles."""

    def __init__(
        self,
        profiles: dict[str, ModelProfileConfig],
        *,
        client: httpx.AsyncClient | None = None,
    ):
        self.profiles = profiles
        self._client = client or httpx.AsyncClient(timeout=600.0)
        self._owns_client = client is None
        self.call_records: list[dict[str, object]] = []
        self._current_call_record: ContextVar[dict[str, object] | None] = ContextVar(
            "model_gateway_current_call_record",
            default=None,
        )

    def current_call_record(self) -> dict[str, object] | None:
        record = self._current_call_record.get()
        return dict(record) if record is not None else None

    def clear_current_call_record(self) -> None:
        self._current_call_record.set(None)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def unload(self, profile_name: str) -> None:
        profile = self.profiles[profile_name]
        if profile.provider != "ollama":
            return
        response = await self._client.post(
            f"{profile.endpoint}/api/generate",
            json={"model": profile.model, "keep_alive": 0},
            timeout=profile.request_timeout_seconds,
        )
        if response.is_error:
            raise RuntimeError(
                f"Ollama 卸载失败（{response.status_code}）：{response.text}"
            )

    async def generate(
        self,
        profile_name: str,
        messages: list[ChatMessage],
    ) -> str:
        profile = self.profiles[profile_name]
        try:
            if profile.provider == "ollama":
                return await self._ollama_text(profile, messages)
            if profile.provider in {"openai_compatible", "llama_cpp", "vllm"}:
                return await self._openai_text(profile, messages)
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
        try:
            return await self._generate_json_once(profile, messages, schema), []
        except (httpx.HTTPError, RuntimeError, ValueError, ValidationError) as exc:
            fallback_name = profile.fallback_profile
            if fallback_name is None:
                raise
            logging.getLogger(__name__).warning(
                "model profile failed; using configured fallback",
                extra={
                    "profile": profile_name,
                    "provider": profile.provider,
                    "fallback_profile": fallback_name,
                    "error": str(exc),
                },
            )
            fallback = self.profiles[fallback_name]
            return (
                await self._generate_json_once(
                    fallback,
                    messages,
                    schema,
                ),
                ["planner_primary_provider_fallback"],
            )

    async def _generate_json_once(
        self,
        profile: ModelProfileConfig,
        messages: list[ChatMessage],
        schema: type[StructuredResult],
    ) -> StructuredResult:
        if profile.provider == "ollama":
            return await self._ollama_json(profile, messages, schema)
        if profile.provider in {
            "openai_compatible",
            "llama_cpp",
            "vllm",
        }:
            return await self._openai_json(profile, messages, schema)
        raise ValueError(f"不支持的 model provider: {profile.provider}")

    async def _ollama_json(
        self,
        profile: ModelProfileConfig,
        messages: list[ChatMessage],
        schema: type[StructuredResult],
    ) -> StructuredResult:
        started = time.perf_counter()
        body: dict[str, object] | None = None
        try:
            response = await self._client.post(
                f"{profile.endpoint}/api/chat",
                json={
                    "model": profile.model,
                    "messages": [message.model_dump() for message in messages],
                    "stream": False,
                    "format": schema.model_json_schema(),
                    "think": profile.think,
                    "keep_alive": profile.keep_alive,
                    "options": {
                        "temperature": profile.temperature,
                        "top_p": profile.top_p,
                        "num_predict": profile.max_tokens,
                        "num_ctx": profile.context_window,
                    },
                },
                timeout=profile.request_timeout_seconds,
            )
            if response.is_error:
                raise RuntimeError(
                    f"Ollama 调用失败（{response.status_code}）：{response.text}"
                )
            body = response.json()
            content = str(body["message"]["content"])
            result = schema.model_validate_json(content)
        except Exception as exc:
            self._record_failure(
                profile, "json", time.perf_counter() - started, exc, body=body
            )
            raise
        self._record_call(profile, "json", body, time.perf_counter() - started)
        return result

    async def _ollama_text(
        self,
        profile: ModelProfileConfig,
        messages: list[ChatMessage],
    ) -> str:
        started = time.perf_counter()
        body: dict[str, object] | None = None
        try:
            response = await self._client.post(
                f"{profile.endpoint}/api/chat",
                json={
                    "model": profile.model,
                    "messages": [message.model_dump() for message in messages],
                    "stream": False,
                    "think": profile.think,
                    "keep_alive": profile.keep_alive,
                    "options": {
                        "temperature": profile.temperature,
                        "top_p": profile.top_p,
                        "num_predict": profile.max_tokens,
                        "num_ctx": profile.context_window,
                    },
                },
                timeout=profile.request_timeout_seconds,
            )
            if response.is_error:
                raise RuntimeError(
                    f"Ollama 调用失败（{response.status_code}）：{response.text}"
                )
            body = response.json()
            result = str(body["message"]["content"] or "").strip()
        except Exception as exc:
            self._record_failure(
                profile, "text", time.perf_counter() - started, exc, body=body
            )
            raise
        self._record_call(profile, "text", body, time.perf_counter() - started)
        return result

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
                headers={
                    "Authorization": f"Bearer {profile.api_key}",
                },
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
    ) -> str:
        payload = {
            "model": profile.model,
            "messages": [message.model_dump() for message in messages],
            "temperature": profile.temperature,
            "top_p": profile.top_p,
            "max_tokens": profile.max_tokens,
        }
        self._apply_provider_capabilities(profile, payload)
        started = time.perf_counter()
        body: dict[str, object] | None = None
        try:
            response = await self._client.post(
                f"{profile.endpoint}/chat/completions",
                headers={"Authorization": f"Bearer {profile.api_key}"},
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
        if total_tokens is None and isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
            total_tokens = prompt_tokens + completion_tokens
        record = {
            "provider": profile.provider,
            "model": profile.model,
            "kind": kind,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "latency_seconds": round(latency_seconds, 6),
            "status": status,
            "error_type": error_type,
        }
        self.call_records.append(record)
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
        if profile.model.startswith("deepseek-v4"):
            payload["thinking"] = {
                "type": "enabled" if profile.think else "disabled"
            }


def build_model_gateway(settings: Settings) -> ModelGateway:
    profiles = {"planner": settings.planner}
    if settings.planner_external is not None:
        profiles["planner_external"] = settings.planner_external
    if settings.responder is not None:
        profiles["responder"] = settings.responder
    profiles.update(settings.responder_benchmarks)
    return ModelGateway(profiles)
