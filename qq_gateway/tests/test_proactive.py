import asyncio
from datetime import datetime, timedelta
import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from hanser_qq.client import BackendFailure, HanserClient
from hanser_qq.config import ProactiveSettings
from hanser_qq.proactive import Proactive, TZ, awake, controls
from test_engine import Backend, make_gateway, settled
from test_events import incoming
from test_headless import app_with_models


class InitiativeBackend(Backend):
    def __init__(self):
        super().__init__()
        self.initiatives, self.acks = [], []
        self.initiative_started = asyncio.Event()
        self.initiative_block = None
        self.ack_fails = False

    async def proactive(self, payload):
        self.initiatives.append(payload)
        self.initiative_started.set()
        if self.initiative_block:
            await self.initiative_block.wait()
        return {'request_id': payload['request_id'], 'reply_id': 'p-' + payload['request_id'],
            'text': '甲 乙 丙', 'segments': ['甲', '乙', '丙']}

    async def proactive_valid(self, payload):
        return True

    async def proactive_ack(self, payload):
        if self.ack_fails:
            raise BackendFailure('receipt_unavailable', retryable=True)
        self.acks.append(payload)


def initiative_gateway(config, *, backend=None, sender=None):
    config.gateway.private_users = ['200']
    config.gateway.proactive = ProactiveSettings(enabled=True, idle_seconds=1, jitter_seconds=0)
    config.gateway.segmented_reply.min_interval_seconds = 0
    config.gateway.segmented_reply.max_interval_seconds = 0
    gateway, sends = make_gateway(config, backend=backend or InitiativeBackend(), sender=sender)
    clock = [datetime(2026, 10, 19, 12, tzinfo=TZ).timestamp()]
    gateway.proactive = Proactive(gateway, clock=lambda: clock[0])
    return gateway, sends, clock


def test_direct_opt_out_rest_and_local_hours():
    now = datetime(2026, 10, 19, 21, tzinfo=TZ).timestamp()
    assert controls('不要再主动找我了', now) == {'disabled': 1}
    assert controls('可以主动找我', now)['disabled'] == 0
    assert not controls('朋友说不要主动找我', now)
    assert not controls('他说“不要主动找我”，这是引用', now)
    assert datetime.fromtimestamp(controls('晚安，我先睡了', now)['paused_until'], TZ).hour == 9
    assert controls('我现在忙，晚点再聊', now)['paused_until'] > now
    settings = ProactiveSettings()
    assert awake(settings, now) and not awake(settings, now + 2 * 3600)


async def test_idle_delivery_daily_limit_unanswered_pause_and_restart(config):
    g, sends, clock = initiative_gateway(config)
    g.receive(incoming(text='我准备写一个小游戏'))
    await settled(g)
    clock[0] += 2
    await g.proactive.tick()
    await settled(g)
    assert len(g.backend.initiatives) == 1 and len(sends) == 4
    assert [text for _, text in sends[1:]] == ['甲', '乙', '丙']
    assert [ack['delivered_parts'] for ack in g.backend.acks] == [1, 2, 3]
    assert g.proactive.summary()['unanswered'] == 1
    await g.close()
    restarted, _, _ = initiative_gateway(config)
    clock[0] += 86400
    restarted.proactive.clock = lambda: clock[0]
    await restarted.proactive.tick()
    await settled(restarted)
    assert not restarted.backend.initiatives
    restarted.receive(incoming('2', text='回来啦'))
    await settled(restarted)
    clock[0] += 2
    await restarted.proactive.tick()
    await settled(restarted)
    assert len(restarted.backend.initiatives) == 1
    restarted.receive(incoming('3', text='再聊聊'))
    await settled(restarted)
    clock[0] += 2
    await restarted.proactive.tick()
    await settled(restarted)
    assert len(restarted.backend.initiatives) == 1  # same-day quota
    await restarted.close()


async def test_new_input_during_generation_cancels_initiative(config):
    backend = InitiativeBackend()
    backend.initiative_block = asyncio.Event()
    g, sends, clock = initiative_gateway(config, backend=backend)
    g.receive(incoming())
    await settled(g)
    clock[0] += 2
    await g.proactive.tick()
    await asyncio.wait_for(backend.initiative_started.wait(), 1)
    g.receive(incoming('2', text='刚才那个已经完成了'))
    backend.initiative_block.set()
    await settled(g)
    assert all(not b['request_id'].startswith('qq-proactive-') for b, _ in sends)
    assert g.proactive.summary()['jobs'] == {'cancelled': 1}
    await g.close()


async def test_new_input_between_parts_records_only_sent_prefix(config):
    sent = []
    async def sender(batch, text):
        sent.append((batch, text))
        if batch['request_id'].startswith('qq-proactive-'):
            g.receive(incoming('2', text='我来了，现在聊点别的'))
        return {'message_id': len(sent)}
    g, _, clock = initiative_gateway(config, sender=sender)
    g.receive(incoming())
    await settled(g)
    clock[0] += 2
    await g.proactive.tick()
    await settled(g)
    assert [text for b, text in sent if b['request_id'].startswith('qq-proactive-')] == ['甲']
    assert g.backend.acks[-1]['delivered_parts'] == 1
    assert len(g.backend.calls) == 2
    await g.close()


async def test_offline_expiry_optout_and_unknown_online_do_not_send(config):
    g, sends, clock = initiative_gateway(config)
    g.receive(incoming())
    await settled(g)
    g.connection('100', connected=True, online=None)
    clock[0] += 2
    await g.proactive.tick()
    assert not g.backend.initiatives
    clock[0] += 1000
    g.connection('100', connected=True, online=True)
    await g.proactive.tick()
    assert not g.backend.initiatives  # no catch-up burst
    g.receive(incoming('2', text='不要主动找我'))
    await settled(g)
    clock[0] += 2
    await g.proactive.tick()
    assert not g.backend.initiatives and g.proactive.summary()['opted_out'] == 1
    await g.close()


async def test_failed_history_receipt_resumes_without_resending_prefix(config):
    backend = InitiativeBackend()
    g, sends, clock = initiative_gateway(config, backend=backend)
    g.receive(incoming())
    await settled(g)
    backend.ack_fails = True
    clock[0] += 2
    await g.proactive.tick()
    await settled(g)
    assert len(sends) == 2 and sends[-1][1] == '甲'
    backend.ack_fails = False
    await g.proactive.tick()
    await settled(g)
    assert [text for _, text in sends[1:]] == ['甲', '乙', '丙']
    assert len(backend.initiatives) == 1
    await g.close()


async def test_unknown_send_is_not_repeated_and_new_input_can_resume_normal_chat(config):
    attempts = []
    async def sender(batch, text):
        attempts.append(batch['request_id'])
        if batch['request_id'].startswith('qq-proactive-'):
            raise TimeoutError('uncertain QQ acknowledgement')
        return {'message_id': len(attempts)}
    g, _, clock = initiative_gateway(config, sender=sender)
    g.receive(incoming())
    await settled(g)
    clock[0] += 2
    await g.proactive.tick()
    await settled(g)
    request_id = g.backend.initiatives[0]['request_id']
    assert g.store.get(request_id)['status'] == 'send_unknown'
    g.connection('100', connected=True, online=True)
    await g.proactive.tick()
    await settled(g)
    assert attempts.count(request_id) == 1
    g.receive(incoming('2', text='现在有空了'))
    await settled(g)
    assert len(g.backend.calls) == 2 and not g.backend.acks
    with g.store.connect() as c:
        assert c.execute('SELECT status FROM delivery_parts WHERE request_id=?', (request_id,)).fetchone()[0] == 'send_unknown'
    await g.close()


async def test_only_explicit_private_targets_can_receive_initiatives(config):
    g, _, clock = initiative_gateway(config)
    config.gateway.bot_ids = ['100']
    assert g.receive(incoming().model_copy(update={'sender_id': '201'})) == 'ignored'
    assert g.receive(incoming().model_copy(update={'bot_id': '101'})) == 'ignored'
    g.receive(incoming(kind='group', addressed=True))
    await settled(g)
    clock[0] += 2
    await g.proactive.tick()
    assert not g.backend.initiatives and g.proactive.summary()['sessions'] == 0
    await g.close()


def test_backend_system_trigger_uses_persona_and_only_acknowledged_assistant_history(config, tmp_path):
    config.gateway.private_users = ['200']
    config.gateway.proactive.enabled = True
    config.gateway.segmented_reply.enabled = True
    # Keep the proactive generation longer than one bubble to exercise prefix receipts.
    text = '诶，前面提的那个小游戏现在做到哪一步啦？不用赶着回，等你有空的时候再跟我说也行。'
    app, calls = app_with_models(config, tmp_path, reply_text=text)
    from hanser_qq.proactive_backend import choose_topic
    headers = {'X-Hanser-QQ-Token': config.backend.token}
    body = {'user_id': 'qq:100:200', 'conversation_id': 'qq:dm:100:200', 'request_id': 'qq-proactive-test'}
    with TestClient(app) as client:
        assert client.post('/v1/chat', headers=headers, json={**body, 'request_id': 'normal-seed', 'message': '我准备写一个小游戏'}).status_code == 200
        a = app.state.agent
        before = a.conversations.message_count(body['conversation_id'], user_id=body['user_id'])
        memory_before = len(a.memory_store.list_memories(user_id=body['user_id']))
        model_before = len(calls)
        result = client.post('/v1/proactive/generate', headers=headers, json=body)
        assert result.status_code == 200, result.text
        assert len(calls) == model_before + 1
        response = result.json()
        assert a.conversations.message_count(body['conversation_id'], user_id=body['user_id']) == before
        assert calls[-1]['messages'][-1]['role'] == 'system'
        assert '[PROACTIVE GROUNDING]' in calls[-1]['messages'][0]['content']
        assert 'Persona' in calls[-1]['messages'][0]['content']
        count = len(calls)
        assert client.post('/v1/proactive/generate', headers=headers, json=body).json() == response
        assert len(calls) == count
        assert client.post('/v1/proactive/validate', headers=headers, json=body).json()['valid']
        ack = {**body, 'delivered_parts': 1, 'message_ids': ['900']}
        assert client.post('/v1/proactive/ack', headers=headers, json=ack).status_code == 200
        assert client.post('/v1/proactive/ack', headers=headers, json=ack).status_code == 200
        history = a.conversations.get_recent(body['conversation_id'], user_id=body['user_id'])
        assert history[-1].role == 'assistant' and history[-1].content == response['segments'][0]
        assert a.conversations.message_count(body['conversation_id'], user_id=body['user_id']) == before + 1
        assert len(a.memory_store.list_memories(user_id=body['user_id'])) == memory_before
        final = {**body, 'delivered_parts': len(response['segments']), 'message_ids': [str(900+i) for i in range(len(response['segments']))]}
        assert client.post('/v1/proactive/ack', headers=headers, json=final).status_code == 200
        assert a.conversations.message_count(body['conversation_id'], user_id=body['user_id']) == before + 1
        assert client.post('/v1/proactive/generate', headers=headers, json={**body, 'user_id': 'qq:100:201'}).status_code == 403
        preview = client.post('/v1/proactive/preview', headers=headers, json={**body, 'request_id': 'qq-proactive-preview'}).json()
        assert client.post('/v1/proactive/ack', headers=headers, json={**body, 'request_id': preview['request_id'], 'delivered_parts': 1, 'message_ids': ['x']}).status_code == 409
        assert client.post('/v1/chat', headers=headers, json={**body, 'request_id': 'new-input', 'message': '小游戏已经写完了，别再问了'}).status_code == 200
        assert not client.post('/v1/proactive/validate', headers=headers, json=body).json()['valid']
        history = a.conversations.get_recent(body['conversation_id'], user_id=body['user_id'])
        assert choose_topic(history, [], datetime.now(TZ), 72)['kind'] != 'progress'


async def test_actual_http_client_proactive_protocol(config, tmp_path):
    config.gateway.private_users = ['200']
    config.gateway.proactive.enabled = True
    app, calls = app_with_models(config, tmp_path, reply_text='最近在忙什么呀？')
    with TestClient(app) as c:
        c.post('/v1/chat', headers={'X-Hanser-QQ-Token': config.backend.token}, json={
            'user_id': 'qq:100:200', 'conversation_id': 'qq:dm:100:200', 'request_id': 'http-seed', 'message': '你好'})
    client = HanserClient('http://test', config.backend.token, 10, transport=httpx.ASGITransport(app=app))
    body = {'user_id': 'qq:100:200', 'conversation_id': 'qq:dm:100:200', 'request_id': 'qq-proactive-client'}
    reply = await client.proactive(body)
    assert reply['text'] and await client.proactive_valid(body)
    await client.proactive_ack({**body, 'delivered_parts': 1, 'message_ids': ['1000']})
    await client.close()


def test_topic_sources_are_recent_first_party_and_do_not_repeat_or_revive_finished_tasks(config, tmp_path):
    app_with_models(config, tmp_path)
    from hanser_qq.proactive_backend import choose_topic
    from hanser_agent.models import ChatMessage
    now = datetime.now(TZ)
    task = ChatMessage(role='user', content='我准备写一个小游戏', message_id='task', created_at=now)
    topic = choose_topic([task], [], now, 72)
    assert topic['kind'] == 'progress' and topic['source_message_ids'] == ['task']
    assert choose_topic([task], [], now, 72, [topic])['kind'] != 'progress'
    for text in ['小游戏已经写完了', '我不想再写这个了', '项目取消了', '别再问这个项目了']:
        update = ChatMessage(role='user', content=text, message_id='update', created_at=now)
        assert choose_topic([task, update], [], now, 72)['kind'] != 'progress'
    assert choose_topic([task.model_copy(update={'created_at': now - timedelta(hours=80)})], [], now, 72)['kind'] != 'progress'
    for text in ['他说“我准备写一个小游戏”', '去年我在做一个小游戏', '如果我准备做个游戏，你会玩吗？']:
        assert choose_topic([task.model_copy(update={'content': text})], [], now, 72)['kind'] != 'progress'


def test_interest_memory_is_revalidated_after_deletion(config, tmp_path):
    config.gateway.private_users = ['200']
    config.gateway.proactive.enabled = True
    app, _ = app_with_models(config, tmp_path, reply_text='最近有没有玩到什么有意思的游戏呀？')
    body = {'user_id': 'qq:100:200', 'conversation_id': 'qq:dm:100:200', 'request_id': 'qq-proactive-interest'}
    headers = {'X-Hanser-QQ-Token': config.backend.token}
    with TestClient(app) as c:
        assert c.post('/v1/chat', headers=headers, json={**body, 'request_id': 'preference', 'message': '我喜欢独立游戏'}).status_code == 200
        reply = c.post('/v1/proactive/generate', headers=headers, json=body)
        assert reply.status_code == 200, reply.text
        with sqlite3.connect(tmp_path / 'hanser.db') as db:
            record = json.loads(db.execute('SELECT record_json FROM qq_proactive_drafts WHERE request_id=?', (body['request_id'],)).fetchone()[0])
        assert record['topic']['kind'] == 'interest' and record['topic']['source_memory_ids']
        assert c.post('/v1/proactive/validate', headers=headers, json=body).json()['valid']
        app.state.agent.memory_store.delete_memory(record['topic']['source_memory_ids'][0])
        assert not c.post('/v1/proactive/validate', headers=headers, json=body).json()['valid']


async def test_normal_input_interrupts_backend_initiative_without_holding_user_lock(config, tmp_path, monkeypatch):
    config.gateway.private_users = ['200']
    config.gateway.proactive.enabled = True
    app, _ = app_with_models(config, tmp_path, reply_text='好呀。')
    body = {'user_id': 'qq:100:200', 'conversation_id': 'qq:dm:100:200'}
    headers = {'X-Hanser-QQ-Token': config.backend.token}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', headers=headers) as c:
        assert (await c.post('/v1/chat', json={**body, 'request_id': 'seed', 'message': '你好'})).status_code == 200
        started = asyncio.Event()
        original = app.state.agent.responder.respond
        async def slow_initiative(context, **kwargs):
            if context.messages[-1].role == 'system':
                started.set()
                await asyncio.Event().wait()
            return await original(context, **kwargs)
        monkeypatch.setattr(app.state.agent.responder, 'respond', slow_initiative)
        proactive = asyncio.create_task(c.post('/v1/proactive/generate', json={**body, 'request_id': 'qq-proactive-slow'}))
        await asyncio.wait_for(started.wait(), 1)
        reply = await asyncio.wait_for(c.post('/v1/chat', json={**body, 'request_id': 'interrupt', 'message': '我来了'}), 2)
        assert reply.status_code == 200 and (await proactive).status_code == 409
        with sqlite3.connect(tmp_path / 'hanser.db') as db:
            assert db.execute('SELECT COUNT(*) FROM qq_proactive_drafts').fetchone()[0] == 0


def test_disabled_delivery_still_allows_non_sending_persona_preview(config, tmp_path):
    config.gateway.private_users = ['200']
    app, _ = app_with_models(config, tmp_path, reply_text='最近在忙什么呀？')
    body = {'user_id': 'qq:100:200', 'conversation_id': 'qq:dm:100:200', 'request_id': 'qq-proactive-off'}
    headers = {'X-Hanser-QQ-Token': config.backend.token}
    with TestClient(app) as c:
        c.post('/v1/chat', headers=headers, json={**body, 'request_id': 'seed-off', 'message': '你好'})
        before = app.state.agent.conversations.message_count(body['conversation_id'], user_id=body['user_id'])
        assert c.post('/v1/proactive/generate', headers=headers, json=body).status_code == 403
        preview = c.post('/v1/proactive/preview', headers=headers, json=body)
        assert preview.status_code == 200 and preview.json()['initiative']['trigger_role'] == 'system'
        assert app.state.agent.conversations.message_count(body['conversation_id'], user_id=body['user_id']) == before


def test_proactive_control_reply_confirms_real_gateway_state(config, tmp_path):
    from hanser_qq.engine import Gateway
    from hanser_qq.store import Store
    from hanser_qq.headless import create_app
    config.gateway.private_users = ['200']
    config.gateway.proactive.enabled = True
    original, calls = app_with_models(config, tmp_path)
    from hanser_qq.text_core import configure_text_core
    settings = original.state.agent.style_tool.settings
    agent = configure_text_core(original.state.agent, settings, user_trial=True)
    app = create_app(config, agent=agent, settings=settings)
    store = Store(config.storage_path)
    gateway = SimpleNamespace(store=store, settings=config.gateway)
    controller = Proactive(gateway)
    with TestClient(app) as c:
        for i, (message, expected, disabled) in enumerate([
            ('不要主动找我', '先不主动打扰', 1), ('可以主动找我', '会偶尔来找你', 0)]):
            event = incoming(str(i), text=message)
            assert store.accept(event)
            controller.on_message(event)
            batch = store.freeze(event.conversation_id, max_messages=12, max_chars=8000)
            before = len(calls)
            reply = c.post('/v1/chat', headers={'X-Hanser-QQ-Token': config.backend.token}, json=json.loads(batch['payload_json']))
            assert reply.status_code == 200 and expected in reply.json()['text']
            assert all(call.get('response_format') for call in calls[before:])  # verified receipt, no Responder call
            assert controller.summary()['opted_out'] == disabled


def test_unsupported_current_self_state_is_rewritten_or_blocked_not_sent(config, tmp_path):
    config.gateway.private_users = ['200']
    config.gateway.proactive.enabled = True
    responses = []
    def model(payload):
        if payload['messages'][-1]['role'] != 'system':
            return '好呀。'
        responses.append(payload)
        return '最近有没有听到什么好听的歌，我这边歌单有点循环到腻了。' if len(responses) != 2 else '诶，最近有没有听到什么好听的歌呀？'
    app, _ = app_with_models(config, tmp_path, reply_text=model)
    from hanser_qq.proactive_backend import ungrounded_current_self_state
    assert ungrounded_current_self_state('我这边歌单有点循环到腻了')
    assert not ungrounded_current_self_state('我想听听你说，最近在忙什么呀？')
    assert not ungrounded_current_self_state('我现在有点好奇，你提的小项目进度怎么样？')
    headers = {'X-Hanser-QQ-Token': config.backend.token}
    body = {'user_id': 'qq:100:200', 'conversation_id': 'qq:dm:100:200', 'request_id': 'qq-proactive-rewrite'}
    with TestClient(app) as c:
        c.post('/v1/chat', headers=headers, json={**body, 'request_id': 'seed-rewrite', 'message': '你好'})
        reply = c.post('/v1/proactive/generate', headers=headers, json=body)
        assert reply.status_code == 200, reply.text
        assert reply.json()['initiative']['rewrites'] == 1 and '歌单' not in reply.json()['text']
        assert len(responses) == 2
        blocked = c.post('/v1/proactive/generate', headers=headers, json={**body, 'request_id': 'qq-proactive-blocked'})
        assert blocked.status_code == 502 and blocked.json()['detail']['retryable'] is False
        with sqlite3.connect(tmp_path / 'hanser.db') as db:
            assert db.execute('SELECT COUNT(*) FROM qq_proactive_generation_failures').fetchone()[0] == 1
            assert dict(db.execute('SELECT role,COUNT(*) FROM messages GROUP BY role')) == {'user': 1, 'assistant': 1}
