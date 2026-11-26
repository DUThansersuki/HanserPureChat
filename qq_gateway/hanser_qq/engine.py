from __future__ import annotations

import asyncio
import json
import logging
import time

from .client import BackendFailure
from .config import GatewaySettings
from .events import IncomingMessage, compose_batch, should_accept
from .store import Store
from .segmentation import send_delays

logger = logging.getLogger(__name__)


class SendRejected(Exception):
    """OneBot returned an explicit action failure (no accepted message)."""


class Gateway:
    def __init__(self, settings: GatewaySettings, store: Store, backend, sender):
        self.settings, self.store, self.backend, self.sender = settings, store, backend, sender
        self.connections: dict[str, dict] = {}
        self.workers: dict[str, asyncio.Task] = {}
        self.wakes: dict[str, asyncio.Event] = {}
        self.closing = False
        self.proactive = None
        store.recover()

    def receive(self, message: IncomingMessage) -> str:
        if not should_accept(message, self.settings):
            return "ignored"
        if len(compose_batch([message])) > self.settings.max_message_chars:
            return "too_large"
        if not self.store.accept(message):
            return "duplicate"
        if self.proactive:
            self.proactive.on_message(message)
        self.kick(message.conversation_id)
        return "accepted"

    def connection(self, bot_id: str, *, connected: bool, online: bool | None = None):
        previous = self.connections.get(bot_id, {})
        self.connections[bot_id] = {"connected": connected,
                                    "online": online if connected else False,
                                    "last_event_at": previous.get("last_event_at"),
                                    "updated_at": time.time()}
        if connected and online is not False:
            for conversation_id in self.store.conversations(bot_id):
                self.kick(conversation_id)

    def activity(self, bot_id: str):
        if bot_id in self.connections:
            self.connections[bot_id]["last_event_at"] = time.time()

    def available(self, bot_id: str) -> bool:
        state = self.connections.get(bot_id, {})
        return bool(state.get("connected")) and state.get("online") is not False

    def batch_allowed(self, batch: dict) -> bool:
        if self.settings.bot_ids and batch["bot_id"] not in self.settings.bot_ids:
            return False
        if batch["kind"] == "group":
            return batch["target_id"] in self.settings.groups
        return self.settings.private_enabled and (not self.settings.private_users or batch["sender_id"] in self.settings.private_users)

    def kick(self, conversation_id: str):
        if self.closing:
            return
        wake = self.wakes.setdefault(conversation_id, asyncio.Event())
        wake.set()
        task = self.workers.get(conversation_id)
        if task is None or task.done():
            self.workers[conversation_id] = asyncio.create_task(self._work(conversation_id))

    async def _pause(self, conversation_id: str, seconds: float):
        wake = self.wakes[conversation_id]
        wake.clear()
        try:
            await asyncio.wait_for(wake.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def _work(self, conversation_id: str):
        try:
            while not self.closing:
                if self.proactive:
                    try:
                        await self.proactive.sync_history(conversation_id)
                    except BackendFailure:
                        return
                if self.store.conversation_blocked(conversation_id):
                    return
                batch = self.store.next_batch(conversation_id)
                if batch is None:
                    window = self.store.pending_window(conversation_id)
                    if not window["n"]:
                        return
                    deadline = min(window["last"] + self.settings.debounce_seconds,
                                   window["first"] + self.settings.max_wait_seconds)
                    wait = deadline - time.time()
                    if wait > 0 and window["n"] < self.settings.max_batch_messages:
                        await self._pause(conversation_id, wait)
                        continue
                    batch = self.store.freeze(conversation_id, max_messages=self.settings.max_batch_messages,
                                               max_chars=self.settings.max_message_chars,
                                               speech_probability=self.settings.voice_reply.probability if self.settings.voice_reply.enabled else 0,
                                               voice_enabled=self.settings.voice_reply.enabled)
                initiative = self.proactive and self.proactive.job(batch['request_id'])
                if initiative and not self.proactive.allowed(batch):
                    self.proactive.cancel(batch['request_id'], 'proactive_no_longer_due')
                    continue
                if not self.batch_allowed(batch):
                    self.store.update(batch["request_id"], "policy_blocked", error_code="current_channel_policy_disabled")
                    continue
                if not self.available(batch["bot_id"]):
                    return
                if initiative and self.connections[batch['bot_id']].get('online') is not True:
                    return
                if batch["status"] == "queued":
                    self.store.update(batch["request_id"], "generating")
                    for attempt in range(self.settings.backend_attempts):
                        try:
                            payload = json.loads(batch["payload_json"])
                            response = await (self.proactive.generate(payload) if initiative else self.backend.chat(payload))
                            if initiative and not self.proactive.allowed(batch):
                                self.proactive.cancel(batch['request_id'], 'proactive_new_input_or_expired')
                                break
                            self.store.update(batch["request_id"], "ready", response=response)
                            break
                        except asyncio.CancelledError:
                            if initiative and not self.closing and self.proactive.job(batch['request_id'])['status'] == 'cancelled':
                                break
                            raise
                        except BackendFailure as exc:
                            if exc.retryable and attempt + 1 < self.settings.backend_attempts:
                                await asyncio.sleep(2 ** attempt)
                            else:
                                self.store.update(batch["request_id"], "backend_failed", error_code=exc.code)
                                logger.warning("QQ backend failed request=%s code=%s", batch["request_id"], exc.code)
                                break
                        except Exception as exc:
                            self.store.update(batch["request_id"], "backend_failed", error_code="backend_unexpected_error")
                            logger.error("QQ backend error request=%s exception=%s", batch["request_id"], type(exc).__name__)
                            break
                    batch = self.store.get(batch["request_id"])
                    if initiative and batch['status'] == 'backend_failed':
                        self.proactive.cancel(batch['request_id'], 'proactive_generation_failed')
                        continue
                if batch["status"] == "ready":
                    if not self.batch_allowed(batch):
                        self.store.update(batch["request_id"], "policy_blocked", error_code="current_channel_policy_disabled")
                        continue
                    if not self.available(batch["bot_id"]):
                        return
                    await self._send(batch)
        finally:
            self.workers.pop(conversation_id, None)
            self.wakes.pop(conversation_id, None)

    async def _send(self, batch: dict):
        request_id = batch["request_id"]
        response = json.loads(batch["response_json"])
        initiative = self.proactive and self.proactive.job(request_id)
        texts = (response.get("segments") if self.settings.segmented_reply.enabled or initiative else None) or [response["text"]]
        kinds = None
        clips = response.get("voices") or ([response["voice"]] if response.get("voice") else [])
        if clips:
            # OneBot voice messages stand alone; group addressing is a separate durable part.
            texts = ([" "] if batch["kind"] == "group" else []) + [clip["file"] for clip in clips]
            kinds = (["text"] if batch["kind"] == "group" else []) + ["record"] * len(clips)
        parts = self.store.prepare_delivery(request_id, texts,
            ([0.0] + [0.3] * (len(texts) - 1)) if kinds else send_delays(texts, self.settings.segmented_reply), kinds=kinds)
        previous_sent_at = None
        for part in parts:
            index = part["part_index"]
            if part["status"] == "sent":
                previous_sent_at = part["sent_at"]
                continue
            if part["status"] != "ready":
                return
            if previous_sent_at is not None:
                await asyncio.sleep(max(0, previous_sent_at + part["delay_before"] - time.time()))
            if initiative:
                if not self.proactive.allowed(batch):
                    self.proactive.cancel(request_id, 'proactive_new_input_or_expired')
                    return
                try:
                    valid = await self.backend.proactive_valid(json.loads(batch['payload_json']))
                except BackendFailure:
                    return
                # Recheck after the awaited HTTP boundary: the user may now be typing.
                if not valid or not self.proactive.allowed(batch):
                    self.proactive.cancel(request_id, 'proactive_source_changed')
                    return
            if self.closing or not self.available(batch["bot_id"]):
                return
            if initiative and self.connections[batch['bot_id']].get('online') is not True:
                return
            if not self.batch_allowed(batch):
                self.store.update(request_id, "policy_blocked", error_code="current_channel_policy_disabled")
                return
            self.store.begin_part(request_id, index)
            try:
                result = await self.sender({**batch, "part_index": index, "part_count": len(parts),
                                           "delivery_kind": part["kind"]}, part["text"])
                message_id = result.get("message_id")
                if not isinstance(message_id, (int, str)) or isinstance(message_id, bool):
                    self.store.finish_part(request_id, index, "send_unknown", error_code="onebot_missing_message_id")
                    return
                previous_sent_at = self.store.finish_part(request_id, index, "sent", message_id=str(message_id))
            except SendRejected:
                self.store.finish_part(request_id, index, "send_failed", error_code="onebot_action_rejected")
                return
            except Exception as exc:
                # An unknown acknowledgement applies only to this part. Never
                # resend the already acknowledged prefix automatically.
                self.store.finish_part(request_id, index, "send_unknown", error_code="onebot_send_result_unknown")
                logger.warning("QQ send outcome unknown request=%s part=%s exception=%s", request_id, index, type(exc).__name__)
                return
            if initiative:
                try:
                    await self.proactive.sync_history(batch['conversation_id'])
                except BackendFailure:
                    return

    def retry(self, request_id: str, *, allow_unknown: bool = False):
        bot_id, conversation_id = self.store.retry(request_id, allow_unknown=allow_unknown)
        if self.available(bot_id):
            self.kick(conversation_id)

    async def close(self):
        self.closing = True
        if self.proactive:
            await self.proactive.close()
        tasks = list(self.workers.values())
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=5)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.backend.close()
