"""No inference: deterministic delivery and HTTP fixture transports only."""
import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hanser_qq.config import SegmentedReplySettings
from hanser_qq.segmentation import split_semantic
from hanser_qq.store import Store
from test_engine import Backend, make_gateway, settled
from test_events import incoming
from test_voice import voice_app


@pytest.mark.parametrize("text,expected", [
    ("好的。知道了。明白了。", ["好的。知道了。", "明白了。"]),
    ("好呀，先试试这个版本，效果不对再慢慢调。", ["好呀，先试试这个版本，", "效果不对再慢慢调。"]),
    ("甲" * 15 + "，乙。", ["甲" * 15 + "，", "乙。"]),
    ("甲" * 16 + "。乙。", ["甲" * 16 + "。", "乙。"]),
    ("好……知道了……再聊。", ["好……知道了……", "再聊。"]),
    ("好呀 先试试这个版本 效果不对再慢慢调", ["好呀 先试试这个版本 ", "效果不对再慢慢调"]),
    ("\n好的。知道了。明白了。", ["\n好的。知道了。", "明白了。"]),
])
def test_strict_bubbles(text, expected):
    assert split_semantic(text, SegmentedReplySettings()) == expected
    assert "".join(expected) == text


def test_strict_limit_never_merges_tail_to_meet_segment_cap():
    parts = split_semantic("好。" * 30, SegmentedReplySettings(max_segments=1))
    assert len(parts) == 15 and all(part == "好。好。" for part in parts)
    text = '保留 `x = 1, y = 2`。还有“这一整句话需要保留原样，不可以从中间切开”。再聊。'
    parts = split_semantic(text, SegmentedReplySettings())
    assert ''.join(parts) == text
    assert any('`x = 1, y = 2`' in part for part in parts)
    assert any('“这一整句话需要保留原样，不可以从中间切开”' in part for part in parts)


def test_commands_freeze_in_event_order_and_persist_per_identity(config, monkeypatch):
    store = Store(config.storage_path)
    for index, text in enumerate(("之前", "/voice", "之后", "/voice off", "最后"), 1):
        store.accept(incoming(str(index), text=text))
    monkeypatch.setattr("hanser_qq.store.random.random", lambda: .9)
    def freeze():
        return store.freeze(incoming().conversation_id, max_messages=12, max_chars=8000, speech_probability=.2)
    before, command, after = freeze(), freeze(), freeze()
    assert json.loads(before['payload_json'])['message'] == '之前'
    assert command['status'] == 'ready'
    assert json.loads(command['response_json'])['text'] == '已开启纯语音回复'
    assert json.loads(after['payload_json'])['speech_required'] is True
    assert json.loads(after['payload_json'])['output_preferences']['speech'] is True
    off, last = freeze(), freeze()
    assert off['status'] == 'ready' and not json.loads(last['payload_json'])['speech_required']
    store.accept(incoming('6', text='/voice on'))
    freeze()
    store = Store(config.storage_path)
    store.accept(incoming('7', text='重启后'))
    assert json.loads(freeze()['payload_json'])['speech_required']
    group = incoming('8', kind='group', addressed=True, text='群里')
    store.accept(group)
    assert not json.loads(store.freeze(group.conversation_id, max_messages=12, max_chars=8000)['payload_json'])['speech_required']


async def test_voice_commands_never_reach_backend_and_records_keep_order(config, monkeypatch):
    config.gateway.voice_reply.enabled = True
    monkeypatch.setattr('hanser_qq.store.random.random', lambda: .9)
    class MultiVoice(Backend):
        async def chat(self, payload):
            result = await super().chat(payload)
            if payload['output_preferences']['speech']:
                result['voices'] = [{'file': 'first.wav'}, {'file': 'second.wav'}]
            return result
    gateway, sends = make_gateway(config, backend=MultiVoice())
    for index, text in enumerate(('/voice', '第一轮', '/voice', '第二轮'), 1):
        gateway.receive(incoming(str(index), text=text))
    await settled(gateway)
    assert [call['message'] for call in gateway.backend.calls] == ['第一轮', '第二轮']
    assert [text for _, text in sends] == ['已开启纯语音回复', 'first.wav', 'second.wav', '已恢复20%概率语音', '收到']
    assert [batch['delivery_kind'] for batch, _ in sends] == ['text', 'record', 'record', 'text', 'text']
    await gateway.close()


def test_voice_failure_is_held_and_retry_reuses_snapshot(config, tmp_path, monkeypatch):
    app, calls, requests, transport = voice_app(config, tmp_path, monkeypatch, failed=True)
    payload = {'user_id': 'qq:100:200', 'conversation_id': 'qq:dm:100:200',
               'request_id': 'required-voice', 'message': '想聊一会儿', 'speech_required': True,
               'output_preferences': {'text': True, 'speech': True}}
    with TestClient(app) as client:
        headers = {'X-Hanser-QQ-Token': config.backend.token}
        first = client.post('/v1/chat', headers=headers, json=payload)
        assert first.status_code == 503 and first.json()['detail']['code'] == 'voice_required_unavailable'
        before = len(calls)
        assert client.post('/v1/chat', headers=headers, json=payload).status_code == 503
        assert len(calls) == before
    records = [p for p in (tmp_path / 'retrieval_logs').glob('*.json') if p.name != 'latest.json']
    assert len(records) == 1
    audit = json.loads(records[0].read_text(encoding='utf-8'))
    assert audit['user_message'] == payload['message']
    assert audit['semantic_reply'] and 'searches' in audit and 'injected' in audit
    assert audit == json.loads((tmp_path / 'retrieval_logs/latest.json').read_text(encoding='utf-8'))
    asyncio.run(transport.aclose())


def test_qq_speech_planner_preserves_punctuation_and_tracks_sparse_replacements(config, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'chat_voice_desktop'))
    from voice_runtime.speech import SpeechPlanner
    from voice_runtime.contracts import AllowedPerformance
    from hanser_qq.voice_entry import short_bubble_planner
    planner = short_bubble_planner(SpeechPlanner)()
    text = '第一句已经足够长了需要单独发送给你。第二句已经足够长了也应该单独发送给你。好呀。知道了。再聊。'
    plan = planner.plan('reply', text, AllowedPerformance(constraints_ref='test'))
    assert plan.semantic_text == text and plan.speech_text == text
    assert [s.speech_text for s in plan.segments] == ['第一句已经足够长了需要单独发送给你。', '第二句已经足够长了也应该单独发送给你。', '好呀。知道了。再聊。']
    sparse = planner.plan('sparse', '好呀 我们慢慢聊 别着急', AllowedPerformance(constraints_ref='test'))
    assert sparse.speech_text == '好呀，我们慢慢聊，别着急'
    assert [m.semantic_span for m in sparse.source_map if m.rule_id == 'qq_sparse_clause_pause'] == [(2, 3), (8, 9)]
    assert all(segment.mapping_revision == 'qq_speech_comma_pause_v5' for segment in sparse.segments)


def test_qq_speech_comma_gap_is_explicit_and_sentence_gap_unchanged(config, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'chat_voice_desktop'))
    from voice_runtime.speech import SpeechPlanner
    from voice_runtime.contracts import AllowedPerformance
    from hanser_qq.voice_entry import short_bubble_planner
    draws = []
    def draw(low, high):
        draws.append((low, high))
        return 160 if len(draws) % 2 else 220
    monkeypatch.setattr('hanser_qq.voice_entry.random.randint', draw)
    planner = short_bubble_planner(SpeechPlanner)()
    text = '要是能把无聊的时间存起来，等早上起床时拿出来多睡一会儿就好了。那你现在可是在攒巨款。'
    plan = planner.plan('reply', text, AllowedPerformance(constraints_ref='test'))
    assert [s.speech_text for s in plan.segments] == ['要是能把无聊的时间存起来，', '等早上起床时拿出来多睡一会儿就好了。', '那你现在可是在攒巨款。']
    assert ''.join(s.speech_text for s in plan.segments) == text
    assert [s.target_gap_ms for s in plan.segments] == [160, 300, 0]
    assert draws == [(160, 220)]
    multiple = planner.plan('multiple', '先坐下，喝点水,再慢慢说。', AllowedPerformance(constraints_ref='test'))
    assert [s.target_gap_ms for s in multiple.segments] == [220, 160, 0]
    assert draws == [(160, 220)] * 3
    restored = type(multiple).model_validate_json(multiple.model_dump_json())
    assert [s.target_gap_ms for s in restored.segments] == [220, 160, 0]
    assert len(draws) == 3
