import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.adapters.onebot.v11.bot import _check_at_me, _check_reply

from hanser_qq.events import IncomingMessage, compose_batch, normalize_event, should_accept


def incoming(mid="1", *, kind="private", sender="200", bot="100", group="300", text="你好", addressed=False):
    return IncomingMessage(bot_id=bot, message_id=mid, sender_id=sender, kind=kind,
                           group_id=group if kind == "group" else None, event_time=1,
                           text=text, segments=[{"type": "text", "data": {"text": text}}], addressed=addressed)


def group_event(message):
    return GroupMessageEvent.model_validate({"time": 1, "self_id": 100, "post_type": "message",
        "message_type": "group", "sub_type": "normal", "message_id": 1, "group_id": 300,
        "user_id": 200, "message": message, "raw_message": "", "font": 0, "sender": {"user_id": 200}})


def test_private_and_group_memory_and_accounts_are_separate():
    dm = incoming()
    group = incoming(kind="group", addressed=True)
    other_group = incoming(kind="group", group="301", addressed=True)
    assert len({dm.user_id, group.user_id, other_group.user_id}) == 3
    assert dm.event_key != group.event_key
    assert incoming(bot="101").event_key != dm.event_key
    assert incoming(sender="201", kind="group").conversation_id != group.conversation_id


def test_group_only_explicit_address_and_whitelist(config):
    assert not should_accept(incoming(kind="group", text="Hanser昨天直播了"), config.gateway)
    assert should_accept(incoming(kind="group", addressed=True), config.gateway)
    assert not should_accept(incoming(kind="group", group="301", addressed=True), config.gateway)
    assert not should_accept(incoming(sender="100"), config.gateway)
    config.gateway.private_users = ["201"]
    assert not should_accept(incoming(), config.gateway)


def test_at_is_preserved_after_adapter_removes_it():
    event = group_event([{"type": "at", "data": {"qq": "100"}}, {"type": "text", "data": {"text": " 你好"}}])
    _check_at_me(None, event)
    assert event.message[0].type == "text"
    message = normalize_event(event, "100")
    assert message.addressed
    assert message.text == "你好"
    assert message.segments[0]["type"] == "at"


@pytest.mark.asyncio
async def test_reply_to_self_and_quote_restoration():
    class Bot:
        async def get_msg(self, **kwargs):
            return {"time": 0, "message_type": "group", "message_id": 88, "real_id": 88,
                    "sender": {"user_id": 100}, "message": [{"type": "text", "data": {"text": "先保存文件"}}]}
    event = group_event([{"type": "reply", "data": {"id": "88"}}, {"type": "text", "data": {"text": "为什么"}}])
    await _check_reply(Bot(), event)
    result = normalize_event(event, "100")
    assert result.addressed and result.reply_to == "88" and result.quote == "先保存文件"
    assert "非当前用户陈述" in compose_batch([result])


def test_unknown_media_and_faces_are_visible_without_guessing():
    event = group_event([{"type": "image", "data": {"file": "x"}}, {"type": "face", "data": {"id": "999"}}])
    result = normalize_event(event, "100")
    assert "未解析" in result.text and "ID：999" in result.text
