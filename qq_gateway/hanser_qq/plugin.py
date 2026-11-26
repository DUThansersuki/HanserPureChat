from __future__ import annotations

import logging
import secrets

import nonebot
from fastapi import HTTPException, Request
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, NoticeEvent, HeartbeatMetaEvent
from nonebot.adapters.onebot.v11.exception import ActionFailed
from pydantic import BaseModel

from .client import HanserClient
from .config import load_settings
from .engine import Gateway, SendRejected
from .events import normalize_event
from .store import Store
from .voice import onebot_message

driver = nonebot.get_driver()
settings = load_settings(driver.config.hanser_qq_config)
backend = HanserClient(settings.backend.url, settings.backend.token, settings.gateway.backend_timeout_seconds)


async def send(batch: dict, text: str):
    bot = nonebot.get_bot(batch["bot_id"])
    # Text never executes model-generated CQ markup; only trusted artifacts become records.
    try:
        message = onebot_message(batch, text, settings.storage_path.parent / 'voice_clips')
    except (ValueError, OSError) as exc:
        raise SendRejected() from exc
    try:
        if batch["kind"] == "group":
            return await bot.send_group_msg(group_id=int(batch["target_id"]), message=message)
        return await bot.send_private_msg(user_id=int(batch["target_id"]), message=message)
    except ActionFailed as exc:
        raise SendRejected() from exc


gateway = Gateway(settings.gateway, Store(settings.storage_path), backend, send)
from .proactive import Proactive
gateway.proactive = Proactive(gateway)


@driver.on_startup
async def startup():
    gateway.proactive.start()


@driver.on_bot_connect
async def connected(bot: Bot):
    gateway.connection(bot.self_id, connected=True)


@driver.on_bot_disconnect
async def disconnected(bot: Bot):
    gateway.connection(bot.self_id, connected=False)


@driver.on_shutdown
async def shutdown():
    await gateway.close()


chat = nonebot.on_type(MessageEvent, priority=10, block=False)


@chat.handle()
async def message(bot: Bot, event: MessageEvent):
    gateway.activity(bot.self_id)
    result = gateway.receive(normalize_event(event, bot.self_id))
    if result == "too_large":
        logging.getLogger(__name__).warning("QQ input exceeds budget bot=%s message_id=%s", bot.self_id, event.message_id)


notices = nonebot.on_type((NoticeEvent, HeartbeatMetaEvent), priority=1, block=False)


@notices.handle()
async def notice(bot: Bot, event):
    gateway.activity(bot.self_id)
    if isinstance(event, HeartbeatMetaEvent):
        gateway.connection(bot.self_id, connected=True, online=event.status.online)
    elif event.notice_type in ("bot_online", "bot_offline"):
        gateway.connection(bot.self_id, connected=True, online=event.notice_type == "bot_online")


app = driver.server_app


def authorize(request: Request):
    if not secrets.compare_digest(request.headers.get("X-Hanser-QQ-Admin-Token", ""), settings.gateway.admin_token):
        raise HTTPException(401, "QQ admin token required")


@app.get("/health")
async def health(request: Request):
    authorize(request)
    return {"ok": True, "mode": "qq-gateway", "qq": gateway.connections,
            "backend": await backend.health(),
            "proactive": gateway.proactive.summary(),
            "delivery": {"segmented_reply": settings.gateway.segmented_reply.enabled,
                         "voice_reply": settings.gateway.voice_reply.model_dump(),
                         "max_segments": settings.gateway.segmented_reply.max_segments,
                         "interval_seconds": [settings.gateway.segmented_reply.min_interval_seconds,
                                              settings.gateway.segmented_reply.max_interval_seconds]},
            **gateway.store.summary()}


class RetryRequest(BaseModel):
    allow_unknown: bool = False


@app.post("/v1/batches/{request_id}/retry")
async def retry(request_id: str, request: Request, body: RetryRequest):
    authorize(request)
    try:
        gateway.retry(request_id, allow_unknown=body.allow_unknown)
    except KeyError as exc:
        raise HTTPException(404, "batch not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "request_id": request_id}
