from __future__ import annotations

import httpx
from typing import Annotated, Literal
from pydantic import BaseModel, Field, ValidationError


class BackendFailure(Exception):
    def __init__(self, code: str, *, retryable: bool):
        self.code, self.retryable = code, retryable
        super().__init__(code)


class VoiceClip(BaseModel):
    file: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    duration_seconds: float = Field(gt=0)


class Reply(BaseModel):
    text: str = Field(min_length=1)
    request_id: str
    reply_id: str = Field(min_length=1)
    segments: list[Annotated[str, Field(min_length=1)]] | None = Field(default=None, min_length=1)
    voice: VoiceClip | None = None
    voice_status: Literal['ready', 'text_only', 'unavailable'] | None = None


class HanserClient:
    def __init__(self, url: str, token: str, timeout: float, *, transport=None):
        self.client = httpx.AsyncClient(base_url=url, timeout=timeout, transport=transport,
                                        headers={"X-Hanser-QQ-Token": token}, trust_env=False)

    async def chat(self, payload: dict) -> dict:
        return await self._reply('/v1/chat', payload)

    async def proactive(self, payload: dict) -> dict:
        return await self._reply('/v1/proactive/generate', payload)

    async def proactive_valid(self, payload: dict) -> bool:
        return (await self._proactive_control('/v1/proactive/validate', payload))['valid']

    async def proactive_ack(self, payload: dict):
        return await self._proactive_control('/v1/proactive/ack', payload)

    async def _proactive_control(self, path, payload):
        try:
            response = await self.client.post(path, json=payload)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BackendFailure('proactive_receipt_or_validation_unavailable', retryable=True) from exc

    async def _reply(self, path, payload: dict) -> dict:
        try:
            response = await self.client.post(path, json=payload)
        except httpx.RequestError as exc:
            raise BackendFailure("backend_transport_error", retryable=True) from exc
        if response.is_error:
            try:
                detail = response.json().get("detail", {})
            except ValueError:
                detail = {}
            code = detail.get("code", f"backend_http_{response.status_code}") if isinstance(detail, dict) else f"backend_http_{response.status_code}"
            retryable = detail.get("retryable", response.status_code in (429, 502, 503, 504)) if isinstance(detail, dict) else False
            raise BackendFailure(str(code), retryable=bool(retryable))
        try:
            data = response.json()
            reply = Reply.model_validate(data)
        except (ValueError, ValidationError) as exc:
            raise BackendFailure("backend_invalid_response", retryable=False) from exc
        if reply.request_id != payload["request_id"]:
            raise BackendFailure("backend_request_mismatch", retryable=False)
        return data

    async def health(self) -> dict:
        try:
            response = await self.client.get("/health", timeout=3)
            response.raise_for_status()
            body = response.json()
            return {"reachable": True, "chat_ready": bool(body.get("chat_ready")),
                    "mode": body.get("mode"), "style_generation": body.get("loaded_style_generation"),
                    "persona_package": body.get("loaded_persona_package_id"),
                    "production": body.get("production"), "corpus": body.get("corpus"),
                    "speech_habits": body.get("speech_habits"),
                    "voice": body.get("voice"),
                    "model_routes": body.get("model_routes"),
                    "runtime_corpus": body.get("runtime_corpus")}
        except (httpx.HTTPError, ValueError):
            return {"reachable": False, "chat_ready": False}

    async def close(self):
        await self.client.aclose()
