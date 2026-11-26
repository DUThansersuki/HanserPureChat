import asyncio
import io
import json
import wave
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from hanser_qq.engine import SendRejected
from hanser_qq.headless import install_backend
from hanser_qq.store import Store
from hanser_qq.voice import join_audio, onebot_message, amplify_audio
from test_engine import Backend, make_gateway, settled
from test_events import incoming
from test_headless import app_with_models


def wav(frames=4800, *, tail_frames=0, sample=1000):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        audio.writeframes(sample.to_bytes(2, 'little', signed=True) * frames + b'\0\0' * tail_frames)
    return buffer.getvalue()


def test_probability_is_per_frozen_round(config, monkeypatch):
    draws = iter([0.19, 0.20])
    monkeypatch.setattr('hanser_qq.store.random.random', lambda: next(draws))
    store = Store(config.storage_path)
    message = incoming()
    store.accept(message)
    store.accept(incoming('2'))
    batch = store.freeze(message.conversation_id, max_messages=12, max_chars=8000, speech_probability=0.2)
    assert json.loads(batch['payload_json'])['output_preferences']['speech'] is True
    store.recover()
    assert Store(config.storage_path).next_batch(message.conversation_id)['payload_json'] == batch['payload_json']
    store.accept(incoming('3'))
    next_round = store.freeze(message.conversation_id, max_messages=12, max_chars=8000, speech_probability=0.2)
    assert json.loads(next_round['payload_json'])['output_preferences']['speech'] is False


@pytest.mark.asyncio
async def test_voice_retry_reuses_one_record_and_does_not_resample(config, monkeypatch, tmp_path):
    config.gateway.voice_reply.enabled = True
    monkeypatch.setattr('hanser_qq.store.random.random', lambda: 0.1)
    class VoiceBackend(Backend):
        async def chat(self, payload):
            return {**await super().chat(payload), 'segments': ['第一段', '第二段'],
                    'voice': {'file': str(tmp_path / 'voice_clips/audio.wav'), 'job_id': 'job', 'duration_seconds': 1}}
    sent = []
    async def send(batch, text):
        sent.append((batch, text))
        if len(sent) == 1:
            raise SendRejected()
        return {'message_id': 22}
    backend = VoiceBackend()
    gateway, _ = make_gateway(config, backend=backend, sender=send)
    gateway.receive(incoming())
    await settled(gateway)
    request_id = sent[0][0]['request_id']
    await gateway.close()
    monkeypatch.setattr('hanser_qq.store.random.random', lambda: pytest.fail('retry must not resample'))
    restarted, _ = make_gateway(config, backend=backend, sender=send)
    restarted.retry(request_id)
    await settled(restarted)
    assert len(backend.calls) == 1 and len(sent) == 2
    assert all(batch['delivery_kind'] == 'record' for batch, _ in sent)
    assert sent[0][1] == sent[1][1]
    await restarted.close()


@pytest.mark.asyncio
async def test_group_voice_addresses_member_once_and_unknown_is_not_repeated(config, tmp_path):
    class VoiceBackend(Backend):
        async def chat(self, payload):
            return {**await super().chat(payload), 'voice': {'file': str(tmp_path / 'voice_clips/a.wav')}}
    sent = []
    async def send(batch, text):
        sent.append((batch, text))
        if batch['delivery_kind'] == 'record':
            raise TimeoutError('unknown ack')
        return {'message_id': 11}
    gateway, _ = make_gateway(config, backend=VoiceBackend(), sender=send)
    gateway.receive(incoming(kind='group', addressed=True))
    await settled(gateway)
    assert [batch['delivery_kind'] for batch, _ in sent] == ['text', 'record']
    assert sent[0][0]['sender_id'] == '200'
    assert gateway.store.get(sent[0][0]['request_id'])['status'] == 'send_unknown'
    gateway.connection('100', connected=True, online=True)
    await settled(gateway)
    assert len(sent) == 2
    await gateway.close()


def test_record_is_trusted_file_only_and_text_does_not_execute_cq(tmp_path):
    root = tmp_path / 'voice_clips'
    root.mkdir()
    path = root / 'audio.wav'
    path.write_bytes(wav())
    batch = {'kind': 'group', 'sender_id': '200', 'part_index': 1, 'delivery_kind': 'record'}
    record = onebot_message(batch, str(path), root)
    assert len(record) == 1 and record[0].type == 'record'
    assert record[0].data['file'] == path.as_uri()
    with pytest.raises(ValueError):
        onebot_message(batch, str(tmp_path / 'outside.wav'), root)
    text = onebot_message({**batch, 'part_index': 0, 'delivery_kind': 'text'}, '[CQ:record,file=untrusted]', root)
    assert [part.type for part in text] == ['at', 'text', 'text']
    assert text[0].data['qq'] == '200'


def test_audio_keeps_segment_order_and_pause():
    result = join_audio([wav(4800), wav(9600)], [2400, 1200], min_pause_ms=0)
    with wave.open(io.BytesIO(result), 'rb') as audio:
        assert audio.getnframes() == 4800 + 2400 + 9600
        assert audio.getframerate() == 48000 and audio.getnchannels() == 1


@pytest.mark.asyncio
async def test_different_group_members_are_separate_rounds(config):
    gateway, sends = make_gateway(config)
    gateway.receive(incoming('1', kind='group', sender='200', addressed=True, text='我是A'))
    gateway.receive(incoming('2', kind='group', sender='201', addressed=True, text='我是B'))
    gateway.receive(incoming('3', kind='group', sender='200', addressed=True, text='再补一句'))
    await settled(gateway)
    assert len(gateway.backend.calls) == len(sends) == 2
    calls = {payload['user_id']: payload for payload in gateway.backend.calls}
    assert '再补一句' in calls['qq:100:group:300:member:200']['message']
    assert '我是B' not in calls['qq:100:group:300:member:200']['message']
    assert '再补一句' not in calls['qq:100:group:300:member:201']['message']
    assert {batch['sender_id'] for batch, _ in sends} == {'200', '201'}
    await gateway.close()


def voice_app(config, tmp_path, monkeypatch, *, failed=False, segment_count=1):
    install_backend(config.backend.package_path)
    from hanser_agent import render_bridge
    original = render_bridge.RenderBridge
    requests = []
    def runtime(request):
        requests.append(request)
        if request.url.path == '/internal/v1/jobs':
            if failed:
                return httpx.Response(503, json={'detail': 'voice runtime is not ready'})
            return httpx.Response(202, json={'job_id': 'voice-test', 'status': 'completed', 'partial': False,
                'ready_segments': [{'index': index, 'audio': {'artifact_id': f'artifact-{index}', 'qa_status': 'passed'},
                                    'timeline': {'pause_after_samples': 14400}} for index in range(segment_count)]})
        if request.url.path.startswith('/internal/v1/artifacts/artifact-'):
            return httpx.Response(200, content=wav(), headers={'content-type': 'audio/wav'})
        return httpx.Response(200, json={'voice_ready': True})
    transport_client = httpx.AsyncClient(base_url='http://voice.invalid', transport=httpx.MockTransport(runtime))
    monkeypatch.setattr(render_bridge, 'RenderBridge', lambda conversations, performance:
        original(conversations, performance, client=transport_client))
    config.gateway.voice_reply.enabled = True
    app, calls = app_with_models(config, tmp_path, reply_text='先歇一下，等会儿再来。')
    return app, calls, requests, transport_client


@pytest.mark.parametrize('failed', [False, True])
def test_headless_voice_uses_committed_semantic_reply_and_text_fallback(config, tmp_path, monkeypatch, failed):
    app, calls, requests, transport_client = voice_app(config, tmp_path, monkeypatch, failed=failed)
    headers = {'X-Hanser-QQ-Token': config.backend.token}
    message = incoming()
    payload = {'user_id': message.user_id, 'conversation_id': message.conversation_id,
               'request_id': 'qq-voice-test', 'message': '有点累了',
               'output_preferences': {'text': True, 'speech': True}}
    with TestClient(app) as client:
        reply = client.post('/v1/chat', json=payload, headers=headers)
        assert reply.status_code == 200, reply.text
        body = reply.json()
        assert body['voice_status'] == ('unavailable' if failed else 'ready')
        if failed:
            assert not body.get('voice') and body['text']
        else:
            assert Path(body['voice']['file']).parent == tmp_path / 'voice_clips'
            assert body['voice']['duration_seconds'] == 0.1
        submitted = json.loads(next(r.content for r in requests if r.url.path == '/internal/v1/jobs'))
        snapshot = app.state.agent.conversations.get_reply_snapshot(body['reply_id'], user_id=message.user_id)
        assert submitted['snapshot']['semantic_text'] == snapshot.semantic_text
        assert submitted['owner'] == snapshot.user_id and submitted['mode'] == 'offline'
        assert 'persona_trace' not in submitted['snapshot']
        before = len(calls)
        replay = client.post('/v1/chat', json=payload, headers=headers)
        assert replay.json() == body and len(calls) == before
        from hanser_agent.agent.conversation import ConversationOwnershipError
        with pytest.raises(ConversationOwnershipError):
            app.state.agent.conversations.get_reply_snapshot(body['reply_id'], user_id='qq:100:201')
    asyncio.run(transport_client.aclose())


def test_headless_joins_validated_segments_into_one_voice_with_pauses(config, tmp_path, monkeypatch):
    app, _, requests, transport = voice_app(config, tmp_path, monkeypatch, segment_count=3)
    with TestClient(app) as client:
        reply = client.post('/v1/chat', headers={'X-Hanser-QQ-Token': config.backend.token}, json={
            'user_id': 'qq:100:200', 'conversation_id': 'qq:dm:100:200', 'request_id': 'multi-voice',
            'message': '想聊两句', 'output_preferences': {'text': True, 'speech': True}, 'speech_required': True})
        assert reply.status_code == 200, reply.text
        clips = reply.json()['voices']
        assert len(clips) == 1 and clips[0] == reply.json()['voice']
        assert clips[0]['source_segment_count'] == 3
        assert clips[0]['min_pause_ms'] == 160
        assert clips[0]['duration_seconds'] == 0.9
        assert clips[0]['level_adjustment']['applied_gain_db'] == pytest.approx(6.0, abs=.01)
        with wave.open(clips[0]['file'], 'rb') as audio:
            assert audio.getnframes() == 3 * 4800 + 2 * 14400
            data = audio.readframes(audio.getnframes())
        assert data[4800 * 2:(4800 + 14400) * 2] == b'\0\0' * 14400
        assert len([r for r in requests if r.url.path == '/internal/v1/jobs']) == 1
    asyncio.run(transport.aclose())


def test_join_fills_only_missing_silence_and_preserves_audio_order():
    # First clip has 200 ms of natural silence, so add only another 250 ms.
    result = join_audio([wav(4800, tail_frames=9600, sample=1000), wav(4800, sample=-2000)], [4800, 0])
    with wave.open(io.BytesIO(result), 'rb') as audio:
        data = audio.readframes(audio.getnframes())
        assert audio.getnframes() == 4800 + 21600 + 4800
    assert data[:9600] == (1000).to_bytes(2, 'little', signed=True) * 4800
    assert data[9600:52800] == b'\0\0' * 21600
    assert data[52800:] == (-2000).to_bytes(2, 'little', signed=True) * 4800


@pytest.mark.parametrize('pause_ms,expected_frames', [(0, 14400), (160, 17280), (220, 20160), (700, 43200)])
def test_join_respects_runtime_pause_and_configured_minimum(pause_ms, expected_frames):
    result = join_audio([wav(), wav()], [4800, 0], min_pause_ms=pause_ms)
    with wave.open(io.BytesIO(result), 'rb') as audio:
        assert audio.getnframes() == expected_frames


def test_join_rejects_incompatible_pcm_and_missing_pause_plan():
    bad = io.BytesIO()
    with wave.open(bad, 'wb') as audio:
        audio.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\0\0\0\0' * 100)
    with pytest.raises(ValueError, match='voice_pcm_format_mismatch'):
        join_audio([wav(), bad.getvalue()], [0, 0])
    with pytest.raises(ValueError):
        join_audio([wav(), wav()], [0])


def test_send_gain_is_uniform_preserves_pauses_and_sample_count():
    from array import array
    source = join_audio([wav(sample=1000), wav(sample=-2000)], [0, 0], min_pause_ms=180)
    boosted, info = amplify_audio(source, 6)
    with wave.open(io.BytesIO(boosted), 'rb') as audio:
        samples = array('h', audio.readframes(audio.getnframes()))
        assert audio.getnframes() == 4800 * 2 + 8640
    assert samples[0] == round(1000 * 10 ** .3)
    assert samples[-1] == round(-2000 * 10 ** .3)
    assert set(samples[4800:13440]) == {0}
    assert info['applied_gain_db'] == pytest.approx(6, abs=.01)


def test_send_gain_limits_peak_instead_of_clipping():
    from array import array
    boosted, info = amplify_audio(wav(sample=20000), 6)
    with wave.open(io.BytesIO(boosted), 'rb') as audio:
        samples = array('h', audio.readframes(audio.getnframes()))
    assert max(samples) < 30000
    assert -1.01 <= info['peak_after_dbfs'] <= -1
    assert 0 < info['applied_gain_db'] < 6


def test_silent_audio_stays_silent_and_zero_gain_keeps_pcm():
    for sample in (0, 1000):
        blob = wav(sample=sample)
        output, _ = amplify_audio(blob, 0)
        with wave.open(io.BytesIO(blob), 'rb') as source, wave.open(io.BytesIO(output), 'rb') as target:
            assert source.readframes(source.getnframes()) == target.readframes(target.getnframes())


def test_isolated_decoder_startup_pulse_is_removed_without_touching_speech():
    import math
    from array import array
    from hanser_qq.voice import suppress_start_transient
    samples = array('h', [0]) * 24000
    for i in range(960):
        samples[i] = round(4250 * math.exp(-i / 100))
    samples[:4] = array('h', [4250, 4077, 3918, 3814])
    for i in range(9600, 22000):
        samples[i] = round(4000 * math.sin(i * .2))
    speech = samples[9600:]
    before = len(samples)
    suppressed = suppress_start_transient(samples)
    assert suppressed and max(abs(x) for x in samples[:960]) == 0
    assert samples[9600:] == speech and len(samples) == before


def test_uninterrupted_voice_onset_is_not_removed():
    from array import array
    from hanser_qq.voice import suppress_start_transient
    samples = array('h', [4000]) * 24000
    samples[:4] = array('h', [4250, 4077, 3918, 3814])
    before = samples[:]
    assert suppress_start_transient(samples) == 0
    assert samples == before


def test_join_counts_next_clip_leading_silence_in_gap():
    source = io.BytesIO()
    with wave.open(source, 'wb') as audio:
        audio.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\0\0' * 4800 + (1000).to_bytes(2, 'little', signed=True) * 4800)
    joined = join_audio([wav(), source.getvalue()], [14400, 0], min_pause_ms=180)
    with wave.open(io.BytesIO(joined), 'rb') as audio:
        assert audio.getnframes() == 4800 + 14400 + 4800


def test_limiter_can_raise_average_level_despite_one_tall_peak():
    import math
    from array import array
    samples = array('h', (round(1000 * math.sin(i * .05)) for i in range(48000)))
    samples[24000] = 20000
    source = io.BytesIO()
    with wave.open(source, 'wb') as audio:
        audio.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        audio.writeframes(samples.tobytes())
    boosted, info = amplify_audio(source.getvalue(), 12)
    assert info['applied_gain_db'] > 8
    assert info['gain_mode'] == 'lookahead_peak_limiter'
    with wave.open(io.BytesIO(boosted), 'rb') as audio:
        assert audio.getnframes() == 48000
        output = array('h', audio.readframes(48000))
    assert max(abs(x) for x in output) < 30000
