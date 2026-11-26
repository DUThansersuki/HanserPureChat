import asyncio
import json
import sqlite3
import time
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from hanser_qq.config import SegmentedReplySettings
from hanser_qq.engine import SendRejected
from hanser_qq.segmentation import split_semantic
from test_engine import Backend, make_gateway, settled
from test_events import incoming
from test_headless import app_with_models


LONG_REPLY = "可以先保留现在已经跑通的配置，不用一下子把所有参数都改掉。先连续聊上两三轮，看看哪一句的表达让你觉得不舒服。把这些具体例子记下来，我们再一点点调整，会比较容易看出差别。"


def test_semantic_slices_preserve_protected_content_and_do_not_cut_each_space():
    settings = SegmentedReplySettings(enabled=True, strict_short_bubbles=False, min_reply_chars=25, target_chars=25, max_chars=45)
    text = '先看这个地址 https://example.com/a?x=1&y=2，再保留代码 `x = (a != b) and (n <= 3)`。他说“这里的内容比较长，但是这一整个引用应当保持完整，不要拆开”。最后我们再继续讨论其他的事情。'
    parts = split_semantic(text, settings)
    assert 2 <= len(parts) <= 4 and ''.join(parts) == text
    for literal in ('https://example.com/a?x=1&y=2', '`x = (a != b) and (n <= 3)`', '“这里的内容比较长，但是这一整个引用应当保持完整，不要拆开”'):
        assert any(literal in part for part in parts)
    sparse = '我觉得可以先试试看 不用一下子把所有的参数都改掉 先聊两三轮看看效果 再决定下一步要调整哪些地方'
    chunks = split_semantic(sparse, settings)
    assert 1 < len(chunks) <= 4 and ''.join(chunks) == sparse
    assert all(len(part) >= settings.min_segment_chars for part in chunks)
    assert split_semantic('好呀 先试试看', settings) == ['好呀 先试试看']
    for text in (LONG_REPLY * 10, '```python\nx = 1\ny = 2\nprint(x + y)\n```' * 3):
        chunks = split_semantic(text, settings)
        assert ''.join(chunks) == text and len(chunks) <= settings.max_segments


@pytest.mark.parametrize('text,expected', [
    ('好呀 先试试这个版本 效果不对再慢慢调', ['好呀 ', '先试试这个版本 效果不对再慢慢调']),
    ('好呀，先试试这个版本，效果不对再慢慢调。', ['好呀，', '先试试这个版本，效果不对再慢慢调。']),
    ('你先忙吧 我这边不急 等你忙完再说', ['你先忙吧 ', '我这边不急 等你忙完再说']),
    ('我觉得这个办法 可能还要再试一试 才知道合不合适', None),
    ('如果你今天有空 就先试试这个版本 看看实际效果', None),
    ('好的 先试一下', None),
    ('keep this phrase and these words together', None),
    ('他说“好呀 先试试这个版本 效果不对再慢慢调”', None),
])
def test_short_clauses_keep_complete_lead_and_group_the_explanation(text, expected):
    settings = SegmentedReplySettings(enabled=True, strict_short_bubbles=False)
    assert split_semantic(text, settings) == (expected or [text])
    assert split_semantic(text, settings.model_copy(update={'split_short_clauses': False})) == [text]
    assert split_semantic(text, settings.model_copy(update={'max_segments': 1})) == [text]


def test_short_http_reply_splits_semantics_before_sparse_display(config, tmp_path):
    config.gateway.segmented_reply.enabled = True
    config.gateway.segmented_reply.strict_short_bubbles = False
    app, _ = app_with_models(config, tmp_path, reply_text='好呀，先试试这个版本，效果不对再慢慢调。')
    with TestClient(app) as client:
        reply = client.post('/v1/chat', headers={'X-Hanser-QQ-Token': config.backend.token}, json={
            'user_id': 'qq:100:200', 'conversation_id': 'qq:dm:100:200',
            'request_id': 'short-segments', 'message': '想先试试这个版本'}).json()
    assert reply['segments'] == ['好呀', '先试试这个版本 效果不对再慢慢调']


def test_http_uses_semantic_boundaries_and_replay_keeps_one_turn(config, tmp_path):
    config.gateway.segmented_reply = SegmentedReplySettings(enabled=True)
    app, calls = app_with_models(config, tmp_path, reply_text=LONG_REPLY)
    body = {'user_id': 'qq:100:200', 'conversation_id': 'qq:dm:100:200',
            'request_id': 'segmented-http', 'message': '想和你随便聊两句'}
    with TestClient(app) as client:
        headers = {'X-Hanser-QQ-Token': config.backend.token}
        reply = client.post('/v1/chat', headers=headers, json=body)
        assert reply.status_code == 200, reply.text
        data = reply.json()
        assert 2 <= len(data['segments'])
        assert '。' not in data['text'] and all('。' not in part for part in data['segments'])
        before = len(calls)
        assert client.post('/v1/chat', headers=headers, json=body).json() == data
        assert len(calls) == before
    with closing(sqlite3.connect(tmp_path / 'hanser.db')) as conn:
        snapshot = json.loads(conn.execute("SELECT snapshot_json FROM reply_snapshots WHERE request_id='segmented-http'").fetchone()[0])
        assert snapshot['semantic_text'] == LONG_REPLY
        assert conn.execute('SELECT COUNT(*) FROM reply_snapshots').fetchone()[0] == 1
    # Exact-output tasks must remain a single unmodified bubble.
    from hanser_agent.persona.output_contract import extract_exact_output
    from hanser_qq.segmentation import render_reply_segments
    command = 'pip install -r requirements.txt --no-cache-dir'
    request = '只输出 ' + command
    assert extract_exact_output(request) is not None
    snapshot = app.state.agent.conversations.get_reply_snapshot(data['reply_id'], user_id=body['user_id'])
    assert render_reply_segments(snapshot, request, command, config.gateway.segmented_reply,
        app.state.agent.responder.display_adapter) == [command]


class SegmentedBackend(Backend):
    async def chat(self, payload):
        result = await super().chat(payload)
        return {**result, 'text': '甲段 乙段 丙段', 'segments': ['甲段', '乙段', '丙段']}


def enable_segments(config, interval=0):
    config.gateway.segmented_reply = SegmentedReplySettings(enabled=True,
        min_interval_seconds=interval, max_interval_seconds=interval)


async def test_partial_rejection_retries_only_unsent_suffix_without_regeneration(config):
    enable_segments(config)
    attempts = []
    async def sender(batch, text):
        attempts.append(text)
        if attempts == ['甲段', '乙段']:
            raise SendRejected()
        return {'message_id': len(attempts)}
    gateway, _ = make_gateway(config, backend=SegmentedBackend(), sender=sender)
    gateway.receive(incoming())
    await settled(gateway)
    request_id = gateway.backend.calls[0]['request_id']
    assert gateway.store.get(request_id)['status'] == 'send_failed'
    gateway.retry(request_id)
    await settled(gateway)
    assert attempts == ['甲段', '乙段', '乙段', '丙段']
    assert len(gateway.backend.calls) == 1
    with gateway.store.connect() as conn:
        rows = conn.execute('SELECT status,qq_message_id FROM delivery_parts ORDER BY part_index').fetchall()
        assert [(r['status'], r['qq_message_id']) for r in rows] == [('sent', '1'), ('sent', '3'), ('sent', '4')]
    await gateway.close()


async def test_disconnect_between_parts_resumes_persisted_suffix(config):
    enable_segments(config)
    first = []
    async def disconnect(batch, text):
        first.append(text)
        gateway.connection('100', connected=False)
        return {'message_id': 10}
    gateway, _ = make_gateway(config, backend=SegmentedBackend(), sender=disconnect)
    gateway.receive(incoming())
    await settled(gateway)
    await gateway.close()
    restarted, suffix = make_gateway(config, backend=SegmentedBackend())
    await settled(restarted)
    assert first == ['甲段'] and [text for _, text in suffix] == ['乙段', '丙段']
    assert not restarted.backend.calls
    await restarted.close()


async def test_unknown_part_blocks_following_turn_until_explicit_retry(config):
    enable_segments(config)
    attempts = []
    async def sender(batch, text):
        attempts.append((batch['request_id'], text))
        if len(attempts) == 2:
            raise TimeoutError('unknown acknowledgement')
        return {'message_id': len(attempts)}
    gateway, _ = make_gateway(config, backend=SegmentedBackend(), sender=sender)
    gateway.receive(incoming())
    await settled(gateway)
    request_id = attempts[0][0]
    gateway.receive(incoming('2', text='下一轮'))
    gateway.connection('100', connected=True, online=True)
    await settled(gateway)
    assert len(attempts) == 2 and len(gateway.backend.calls) == 1
    with pytest.raises(ValueError):
        gateway.retry(request_id)
    gateway.retry(request_id, allow_unknown=True)
    await settled(gateway)
    assert [text for _, text in attempts[:4]] == ['甲段', '乙段', '乙段', '丙段']
    assert len(attempts) == 7 and len(gateway.backend.calls) == 2
    await gateway.close()


async def test_crash_during_a_part_preserves_acknowledged_prefix(config):
    enable_segments(config)
    gateway, _ = make_gateway(config, backend=SegmentedBackend())
    store = gateway.store
    store.accept(incoming())
    batch = store.freeze(incoming().conversation_id, max_messages=10, max_chars=100)
    response = {'text': '甲段 乙段 丙段', 'segments': ['甲段', '乙段', '丙段']}
    store.update(batch['request_id'], 'ready', response=response)
    store.prepare_delivery(batch['request_id'], response['segments'], [0, 0, 0])
    store.begin_part(batch['request_id'], 0)
    store.finish_part(batch['request_id'], 0, 'sent', message_id='101')
    store.begin_part(batch['request_id'], 1)
    await gateway.close()
    restarted, sends = make_gateway(config, backend=SegmentedBackend())
    await settled(restarted)
    assert not sends and store.get(batch['request_id'])['status'] == 'send_unknown'
    restarted.retry(batch['request_id'], allow_unknown=True)
    await settled(restarted)
    assert [text for _, text in sends] == ['乙段', '丙段'] and not restarted.backend.calls
    await restarted.close()


async def test_delays_and_new_turns_do_not_interleave_bubbles(config):
    enable_segments(config, interval=.03)
    sent = []
    async def sender(batch, text):
        sent.append((batch['request_id'], time.monotonic(), text))
        if len(sent) == 1:
            gateway.receive(incoming('2', text='下一轮'))
        return {'message_id': len(sent)}
    gateway, _ = make_gateway(config, backend=SegmentedBackend(), sender=sender)
    gateway.receive(incoming())
    await settled(gateway)
    assert len(sent) == 6
    assert len({item[0] for item in sent[:3]}) == len({item[0] for item in sent[3:]}) == 1
    assert sent[0][0] != sent[3][0]
    assert sent[1][1] - sent[0][1] >= .025 and sent[2][1] - sent[1][1] >= .025
    await gateway.close()


async def test_revoked_policy_stops_unsent_suffix_and_retry_keeps_prefix(config):
    enable_segments(config)
    sent = []
    async def sender(batch, text):
        sent.append(text)
        if len(sent) == 1:
            config.gateway.private_enabled = False
        return {'message_id': len(sent)}
    gateway, _ = make_gateway(config, backend=SegmentedBackend(), sender=sender)
    gateway.receive(incoming())
    await settled(gateway)
    request_id = gateway.backend.calls[0]['request_id']
    assert sent == ['甲段'] and gateway.store.get(request_id)['status'] == 'policy_blocked'
    config.gateway.private_enabled = True
    gateway.retry(request_id)
    await settled(gateway)
    assert sent == ['甲段', '乙段', '丙段'] and len(gateway.backend.calls) == 1
    await gateway.close()
