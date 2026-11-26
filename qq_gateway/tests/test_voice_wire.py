"""Real NoneBot reverse WS record actions; local fixture audio, no real QQ send."""
import asyncio
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import httpx
import pytest
import uvicorn
import websockets
import yaml
from fastapi import FastAPI, Request

from test_onebot_integration import action, event, free_port, heartbeat, quiet
from test_voice import wav


@pytest.mark.asyncio
async def test_private_and_group_record_over_real_reverse_ws(config, tmp_path):
    config.gateway.port, config.backend.port = free_port(), free_port()
    config.gateway.voice_reply.enabled = True
    config.gateway.voice_reply.probability = 1
    audio = tmp_path / 'voice_clips/voice.wav'
    audio.parent.mkdir()
    audio.write_bytes(wav())
    backend = FastAPI()
    payloads = []

    @backend.get('/health')
    async def health():
        return {'chat_ready': True}

    @backend.post('/v1/chat')
    async def chat(request: Request):
        assert request.headers['X-Hanser-QQ-Token'] == config.backend.token
        payload = await request.json()
        payloads.append(payload)
        return {'request_id': payload['request_id'], 'reply_id': 'reply-' + payload['request_id'],
                'text': '语音测试', 'voice_status': 'ready', 'voice': {
                    'file': str(audio), 'job_id': 'test-job', 'duration_seconds': 0.1}}

    server = uvicorn.Server(uvicorn.Config(backend, host='127.0.0.1', port=config.backend.port,
                                         log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    path = tmp_path / 'config.yml'
    path.write_text(yaml.safe_dump(config.model_dump(mode='json')), encoding='utf-8')
    log_path = tmp_path / 'voice-gateway.log'
    with log_path.open('wb') as log:
        process = subprocess.Popen([sys.executable, '-m', 'hanser_qq', 'gateway', '--config', str(path)],
            cwd=Path(__file__).resolve().parents[1], stdout=log, stderr=subprocess.STDOUT,
            env=dict(os.environ, PYTHONUTF8='1'))
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                for _ in range(100):
                    if process.poll() is not None:
                        pytest.fail(log_path.read_text(encoding='utf-8'))
                    try:
                        ready = await client.get(f'http://127.0.0.1:{config.gateway.port}/health',
                            headers={'X-Hanser-QQ-Admin-Token': config.gateway.admin_token})
                        if ready.status_code == 200:
                            break
                    except httpx.RequestError:
                        pass
                    await asyncio.sleep(.05)
                else:
                    pytest.fail('voice gateway startup timed out')
            headers = {'X-Self-ID': '100', 'Authorization': 'Bearer ' + config.gateway.onebot_access_token}
            async with websockets.connect(f'ws://127.0.0.1:{config.gateway.port}/onebot/v11/ws', additional_headers=headers) as ws:
                await heartbeat(ws, True)
                await ws.send(json.dumps(event(1, '私聊语音测试')))
                private = await action(ws, 500)
                assert private['action'] == 'send_private_msg' and private['params']['user_id'] == 200
                segments = private['params']['message']
                assert len(segments) == 1 and segments[0]['type'] == 'record'
                assert segments[0]['data']['file'] == audio.as_uri() and segments[0]['data']['cache'] == 'false'
                await ws.send(json.dumps(event(2, '群聊语音测试', group=True, at=True)))
                addressed = await action(ws, 501)
                assert addressed['action'] == 'send_group_msg' and addressed['params']['group_id'] == 300
                assert addressed['params']['message'][0] == {'type': 'at', 'data': {'qq': '200'}}
                voice = await action(ws, 502)
                assert voice['action'] == 'send_group_msg' and voice['params']['group_id'] == 300
                assert voice['params']['message'] == private['params']['message']
                await quiet(ws)
            assert len(payloads) == 2 and all(p['output_preferences']['speech'] for p in payloads)
            assert payloads[0]['user_id'] != payloads[1]['user_id']
        finally:
            process.terminate()
            process.wait(timeout=10)
            server.should_exit = True
            thread.join(timeout=10)
