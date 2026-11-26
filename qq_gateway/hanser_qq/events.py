from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .config import GatewaySettings


class IncomingMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bot_id: str = Field(pattern=r"^\d+$")
    message_id: str = Field(pattern=r"^-?\d+$")
    sender_id: str = Field(pattern=r"^\d+$")
    kind: Literal["private", "group"]
    group_id: str | None = Field(default=None, pattern=r"^\d+$")
    event_time: float
    text: str
    segments: list[dict]
    addressed: bool = False
    reply_to: str | None = None
    quote: str = ""

    @property
    def event_key(self) -> str:
        return f"{self.bot_id}:{self.kind}:{self.group_id or self.sender_id}:{self.message_id}"

    @property
    def user_id(self) -> str:
        if self.kind == "group":
            return f"qq:{self.bot_id}:group:{self.group_id}:member:{self.sender_id}"
        return f"qq:{self.bot_id}:{self.sender_id}"

    @property
    def conversation_id(self) -> str:
        if self.kind == "group":
            return f"qq:group:{self.bot_id}:{self.group_id}:member:{self.sender_id}"
        return f"qq:dm:{self.bot_id}:{self.sender_id}"


def render_segments(segments: list[dict], *, bot_id: str) -> str:
    parts = []
    for segment in segments:
        kind, data = segment["type"], segment.get("data", {})
        if kind == "text":
            parts.append(str(data.get("text", "")))
        elif kind == "at" and str(data.get("qq", "")) != bot_id:
            parts.append(f"[@QQ:{data.get('qq', '')}]")
        elif kind == "face":
            # 优先使用协议端提供的名称；不猜未知 ID 的含义。
            label = data.get("name") or data.get("text")
            parts.append(f"[QQ表情：{label}]" if label else f"[QQ表情ID：{data.get('id', '')}]")
        elif kind not in ("at", "reply"):
            labels = {"image": "图片", "record": "语音", "video": "视频", "file": "文件",
                      "forward": "合并转发", "json": "卡片", "xml": "卡片"}
            parts.append(f"[收到{labels.get(kind, kind)}，当前文字接入未解析其内容]")
    return "".join(parts).strip()


def normalize_event(event, bot_id: str) -> IncomingMessage:
    # OneBot adapter 会移除 reply/@ 自己等段；original_message 保留原始事件。
    message = event.original_message
    segments = [{"type": seg.type, "data": dict(seg.data)} for seg in message]
    reply = event.reply
    quoted = ""
    if reply is not None:
        quoted = render_segments([{ "type": seg.type, "data": dict(seg.data)}
                                  for seg in reply.message], bot_id=bot_id)
    kind = event.message_type
    at_self = any(s["type"] == "at" and str(s["data"].get("qq")) == bot_id for s in segments)
    return IncomingMessage(
        bot_id=bot_id, message_id=str(event.message_id), sender_id=str(event.user_id),
        kind=kind, group_id=str(event.group_id) if kind == "group" else None,
        event_time=event.time, text=render_segments(segments, bot_id=bot_id), segments=segments,
        addressed=at_self or (reply is not None and str(reply.sender.user_id) == bot_id),
        reply_to=str(reply.message_id) if reply is not None else None, quote=quoted,
    )


def should_accept(message: IncomingMessage, settings: GatewaySettings) -> bool:
    if message.sender_id == message.bot_id:
        return False
    if settings.bot_ids and message.bot_id not in settings.bot_ids:
        return False
    if message.kind == "group":
        return message.group_id in settings.groups and message.addressed
    return settings.private_enabled and (not settings.private_users or message.sender_id in settings.private_users)


def compose_batch(messages: list[IncomingMessage]) -> str:
    parts = []
    if messages[0].kind == "group":
        parts.append(f"[QQ群内定向对话；当前发言者QQ={messages[0].sender_id}；当前群={messages[0].group_id}]")
    for message in messages:
        if message.quote:
            # 引述明确不归属当前发言者，且落入现有 Memory/Persona 引用范围。
            quote = message.quote.replace("“", "「").replace("”", "」")
            parts.append(f"引用消息（ID={message.reply_to}，非当前用户陈述）：\n“{quote}”")
        parts.append(message.text or "[@机器人]")
    return "\n".join(parts)
