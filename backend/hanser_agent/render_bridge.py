from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Literal

import httpx

from .agent.conversation import ConversationStore
from .config import PerformanceConfig
from .responder.performance import ReplySnapshot


@dataclass(frozen=True, slots=True)
class RenderBridgeFailure(Exception):
    status_code: int
    message: str


@dataclass(slots=True)
class ProxiedStream:
    status_code: int
    headers: dict[str, str]
    body: AsyncIterator[bytes]


class RenderBridge:
    """Owner-checking boundary between public reply IDs and the trusted runtime."""

    def __init__(
        self,
        conversations: ConversationStore,
        config: PerformanceConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ):
        self.conversations = conversations
        self.config = config
        self.client = client or httpx.AsyncClient(
            base_url=config.voice_runtime_base_url,
            timeout=httpx.Timeout(config.request_timeout_seconds),
        )
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def create_voice_job(
        self,
        *,
        reply_id: str,
        user_id: str,
        mode: Literal["interactive", "offline"],
        rendition_id: str,
        variant_salt: str | None = None,
    ) -> dict[str, object]:
        snapshot = self._snapshot(reply_id, user_id)
        if not snapshot.allow_tts or not snapshot.output_preferences.speech:
            raise RenderBridgeFailure(409, "reply snapshot is not eligible for speech")
        response = await self._request(
            "POST",
            "/internal/v1/jobs",
            owner=user_id,
            json={
                "owner": user_id,
                "snapshot": snapshot.model_dump(mode="json"),
                "mode": mode,
                "rendition_id": rendition_id,
                "variant_salt": variant_salt,
            },
        )
        return response.json()

    async def create_visual_plan(
        self, *, reply_id: str, user_id: str, epoch: int
    ) -> dict[str, object]:
        snapshot = self._snapshot(reply_id, user_id)
        if not (
            snapshot.output_preferences.dynamic_live2d
            or snapshot.output_preferences.offline_performance
        ):
            raise RenderBridgeFailure(409, "reply snapshot has no visual consumer")
        response = await self._request(
            "POST",
            "/internal/v1/visual-plans",
            owner=user_id,
            json={
                "owner": user_id,
                "snapshot": snapshot.model_dump(mode="json"),
                "epoch": epoch,
            },
        )
        return response.json()

    async def get_job(self, job_id: str, user_id: str) -> dict[str, object]:
        response = await self._request(
            "GET", f"/internal/v1/jobs/{job_id}", owner=user_id
        )
        return response.json()

    async def cancel_job(self, job_id: str, user_id: str) -> dict[str, object]:
        response = await self._request(
            "POST", f"/internal/v1/jobs/{job_id}/cancel", owner=user_id
        )
        return response.json()

    async def record_playback(
        self, job_id: str, user_id: str, payload: dict[str, object]
    ) -> dict[str, object]:
        response = await self._request(
            "POST",
            f"/internal/v1/jobs/{job_id}/playback",
            owner=user_id,
            json=payload,
        )
        return response.json()

    async def stream_events(
        self,
        job_id: str,
        user_id: str,
        *,
        after: int,
        last_event_id: str | None,
    ) -> ProxiedStream:
        headers = self._headers(user_id)
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id
        return await self._stream(
            f"/internal/v1/jobs/{job_id}/events?after={after}", headers
        )

    async def stream_artifact(
        self,
        artifact_id: str,
        user_id: str,
        *,
        range_header: str | None,
    ) -> ProxiedStream:
        headers = self._headers(user_id)
        if range_header:
            headers["Range"] = range_header
        return await self._stream(
            f"/internal/v1/artifacts/{artifact_id}", headers
        )

    def _snapshot(self, reply_id: str, user_id: str) -> ReplySnapshot:
        snapshot = self.conversations.get_reply_snapshot(reply_id, user_id=user_id)
        if snapshot is None:
            raise RenderBridgeFailure(404, "reply snapshot not found")
        return snapshot

    async def _request(
        self,
        method: str,
        path: str,
        *,
        owner: str,
        json: dict[str, object] | None = None,
    ) -> httpx.Response:
        try:
            response = await self.client.request(
                method, path, headers=self._headers(owner), json=json
            )
        except httpx.RequestError as exc:
            raise RenderBridgeFailure(503, "voice runtime is unavailable") from exc
        if response.is_error:
            raise RenderBridgeFailure(
                response.status_code,
                self._error_message(response),
            )
        return response

    async def _stream(self, path: str, headers: dict[str, str]) -> ProxiedStream:
        request = self.client.build_request("GET", path, headers=headers)
        # SSE is heartbeat-driven and may legitimately be idle longer than the
        # ordinary request timeout. Keep connect/write/pool bounded, but do not
        # impose that timeout on reads from an established event stream.
        timeout = self.config.request_timeout_seconds
        request.extensions["timeout"] = {
            "connect": timeout,
            "read": None,
            "write": timeout,
            "pool": timeout,
        }
        try:
            response = await self.client.send(request, stream=True)
        except httpx.RequestError as exc:
            raise RenderBridgeFailure(503, "voice runtime is unavailable") from exc
        if response.is_error:
            await response.aread()
            message = self._error_message(response)
            await response.aclose()
            raise RenderBridgeFailure(response.status_code, message)

        async def body():
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await response.aclose()

        forwarded = {
            key: value
            for key, value in response.headers.items()
            if key.lower()
            in {
                "content-type",
                "content-length",
                "content-range",
                "accept-ranges",
                "cache-control",
            }
        }
        return ProxiedStream(response.status_code, forwarded, body())

    def _headers(self, owner: str) -> dict[str, str]:
        headers = {"X-Hanser-Owner": owner}
        if self.config.internal_token:
            headers["Authorization"] = f"Bearer {self.config.internal_token}"
        return headers

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            detail = response.json().get("detail")
            if isinstance(detail, str):
                return detail
        except (ValueError, AttributeError):
            pass
        return f"voice runtime request failed ({response.status_code})"
