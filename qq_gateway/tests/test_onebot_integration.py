"""Real reverse-WS adapter → HTTP → real Agent + local fake model → OneBot ack."""
import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
import websockets
import yaml

from test_headless import app_with_models


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def event(mid, text="", *, group=False, at=False, reply=None):
    segments = [{"type": "text", "data": {"text": text}}]
    if at:
        segments.insert(0, {"type": "at", "data": {"qq": "100"}})
    if reply:
        segments.insert(0, {"type": "reply", "data": {"id": str(reply)}})
    body = {"time": int(time.time()), "self_id": 100, "post_type": "message", "message_id": mid,
            "message_type": "group" if group else "private", "sub_type": "normal" if group else "friend",
            "user_id": 200, "message": segments, "raw_message": text, "font": 0,
            "sender": {"user_id": 200, "nickname": "测试用户"}}
    if group:
        body["group_id"] = 300
    return body


async def action(ws, message_id=900):
    while True:
        packet = json.loads(await asyncio.wait_for(ws.recv(), 5))
        if packet["action"] == "get_msg":
            data = {"time": 0, "message_type": "group", "message_id": 88, "real_id": 88,
                    "sender": {"user_id": 100}, "message": [{"type": "text", "data": {"text": "先保存文件"}}]}
            await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": data, "echo": packet["echo"]}))
            continue
        assert packet["action"] in ("send_private_msg", "send_group_msg"), packet
        await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": {"message_id": message_id}, "echo": packet["echo"]}))
        return packet


async def quiet(ws, seconds=0.15):
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(ws.recv(), seconds)


async def heartbeat(ws, online):
    await ws.send(json.dumps({"time": int(time.time()), "self_id": 100, "post_type": "meta_event",
                             "meta_event_type": "heartbeat", "status": {"online": online, "good": online}, "interval": 30000}))
    await asyncio.sleep(0.04)


async def test_reverse_ws_private_group_quote_dedup_and_reconnect(config, tmp_path):
    config.gateway.port = free_port()
    config.backend.port = free_port()
    config.gateway.debounce_seconds = 0.08
    config.gateway.max_wait_seconds = 0.2
    config.gateway.segmented_reply.enabled = True
    config.gateway.segmented_reply.strict_short_bubbles = False
    config.gateway.segmented_reply.min_interval_seconds = .03
    config.gateway.segmented_reply.max_interval_seconds = .03
    long_reply = "先保留现在已经跑通的配置，不用一下子把所有参数都改掉。接下来连续聊上两三轮，把觉得不自然的表达记录下来。最后我们再一点点调整，这样会比较容易看出差别。"
    app, model_calls = app_with_models(config, tmp_path, reply_text=lambda payload:
        long_reply if '分段测试' in payload['messages'][-1]['content'] else "好，我记住了。")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=config.backend.port,
                                         log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(config.model_dump(mode="json")), encoding="utf-8")
    log_path = tmp_path / "gateway.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen([sys.executable, "-m", "hanser_qq", "gateway", "--config", str(config_path)],
            cwd=Path(__file__).resolve().parents[1], stdout=log, stderr=subprocess.STDOUT,
            env=dict(os.environ, PYTHONUTF8="1"))
        try:
            admin_headers = {"X-Hanser-QQ-Admin-Token": config.gateway.admin_token}
            async with httpx.AsyncClient(trust_env=False) as client:
                for _ in range(100):
                    if process.poll() is not None:
                        pytest.fail(log_path.read_text(encoding="utf-8"))
                    try:
                        ready = await client.get(f"http://127.0.0.1:{config.gateway.port}/health", headers=admin_headers)
                        if ready.status_code == 200:
                            break
                    except httpx.RequestError:
                        pass
                    await asyncio.sleep(0.05)
                else:
                    pytest.fail("gateway startup timed out")
                url = f"ws://127.0.0.1:{config.gateway.port}/onebot/v11/ws"
                with pytest.raises(websockets.exceptions.InvalidStatus):
                    async with websockets.connect(url, additional_headers={"X-Self-ID": "100", "Authorization": "Bearer wrong"}):
                        pass
                headers = {"X-Self-ID": "100", "Authorization": "Bearer " + config.gateway.onebot_access_token}
                async with websockets.connect(url, additional_headers=headers) as ws:
                    await heartbeat(ws, True)
                    await ws.send(json.dumps(event(10, "Hanser昨天直播了", group=True)))
                    await quiet(ws)
                    await ws.send(json.dumps(event(1, "我喜欢私聊专属紫色星球")))
                    await ws.send(json.dumps(event(1, "我喜欢私聊专属紫色星球")))
                    await ws.send(json.dumps(event(2, "还喜欢音乐")))
                    private = await action(ws)
                    assert private["action"] == "send_private_msg"
                    assert private["params"]["user_id"] == 200
                    assert all(seg["type"] == "text" for seg in private["params"]["message"])
                    await quiet(ws)
                    await ws.send(json.dumps(event(3, "请接一下这句话", group=True, at=True)))
                    group = await action(ws)
                    assert group["action"] == "send_group_msg"
                    assert group["params"]["message"][0] == {"type": "at", "data": {"qq": "200"}}
                    group_prompts = [call for call in model_calls if "请接一下这句话" in call["messages"][-1]["content"]]
                    assert group_prompts
                    assert all("私聊专属紫色星球" not in json.dumps(call, ensure_ascii=False) for call in group_prompts)
                    await ws.send(json.dumps(event(4, "为什么", group=True, reply=88)))
                    quoted = await action(ws)
                    assert quoted["action"] == "send_group_msg"
                    await heartbeat(ws, False)
                    before = len(model_calls)
                    await ws.send(json.dumps(event(5, "离线时排队")))
                    await quiet(ws)
                    assert len(model_calls) == before
                    await heartbeat(ws, True)
                    await action(ws)
                    await quiet(ws)
                await asyncio.sleep(0.1)
                async with websockets.connect(url, additional_headers=headers) as ws:
                    await heartbeat(ws, True)
                    await ws.send(json.dumps(event(1, "我喜欢私聊专属紫色星球")))
                    await quiet(ws)
                    await ws.send(json.dumps(event(6, "来做一次分段测试")))
                    bubbles, received = [], []
                    for i in range(3):
                        bubbles.append(await action(ws, message_id=901+i))
                        received.append(time.monotonic())
                    assert all(packet['action'] == 'send_private_msg' for packet in bubbles)
                    texts = [''.join(seg['data']['text'] for seg in packet['params']['message']) for packet in bubbles]
                    assert texts[0].startswith('先保留') and texts[1].startswith('接下来') and texts[2].startswith('最后')
                    assert received[1] - received[0] >= .02 and received[2] - received[1] >= .02
                    await quiet(ws)
                    before = len(model_calls)
                    await ws.send(json.dumps(event(6, "来做一次分段测试")))
                    await quiet(ws)
                    assert len(model_calls) == before
                status = (await client.get(f"http://127.0.0.1:{config.gateway.port}/health", headers=admin_headers)).json()
                assert status["batch_counts"] == {"sent": 5}, status
                assert status['part_counts'] == {'sent': 7}, status
                assert status["backend"]["chat_ready"]
                assert status['backend']['speech_habits']['enabled'] is False
                assert not status["failures"]
                assert (await client.get(f"http://127.0.0.1:{config.gateway.port}/health")).status_code == 401
        finally:
            process.terminate()
            process.wait(timeout=10)
            server.should_exit = True
            thread.join(timeout=10)


async def test_reverse_ws_automatic_proactive_with_real_http_persona_and_receipt(config, tmp_path):
    from hanser_qq.config import ProactiveSettings
    config.gateway.port, config.backend.port = free_port(), free_port()
    config.gateway.private_users = ['200']
    config.gateway.bot_ids = ['100']
    config.gateway.proactive = ProactiveSettings(enabled=True, idle_seconds=1, jitter_seconds=0,
        start_hour=0, end_hour=24, poll_seconds=.1, expiry_seconds=30)
    app, calls = app_with_models(config, tmp_path, reply_text=lambda payload:
        '最近有没有循环听的歌呀？' if payload['messages'][-1]['role'] == 'system' else '好呀。')
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=config.backend.port,
        log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    config_path = tmp_path / 'config.yml'
    config_path.write_text(yaml.safe_dump(config.model_dump(mode='json')), encoding='utf-8')
    log_path = tmp_path / 'proactive-gateway.log'
    with log_path.open('wb') as log:
        process = subprocess.Popen([sys.executable, '-m', 'hanser_qq', 'gateway', '--config', str(config_path)],
            cwd=Path(__file__).resolve().parents[1], stdout=log, stderr=subprocess.STDOUT,
            env=dict(os.environ, PYTHONUTF8='1'))
        try:
            admin = {'X-Hanser-QQ-Admin-Token': config.gateway.admin_token}
            async with httpx.AsyncClient(trust_env=False) as client:
                for _ in range(100):
                    if process.poll() is not None:
                        pytest.fail(log_path.read_text(encoding='utf-8'))
                    try:
                        if (await client.get(f'http://127.0.0.1:{config.gateway.port}/health', headers=admin)).status_code == 200:
                            break
                    except httpx.RequestError:
                        pass
                    await asyncio.sleep(.05)
                else:
                    pytest.fail('proactive gateway startup timed out')
                headers = {'X-Self-ID': '100', 'Authorization': 'Bearer ' + config.gateway.onebot_access_token}
                async with websockets.connect(f'ws://127.0.0.1:{config.gateway.port}/onebot/v11/ws', additional_headers=headers) as ws:
                    await heartbeat(ws, True)
                    await ws.send(json.dumps(event(1, '你好呀')))
                    await action(ws, 700)
                    proactive = await action(ws, 701)
                    assert proactive['action'] == 'send_private_msg' and proactive['params']['user_id'] == 200
                    await quiet(ws)
                    status = (await client.get(f'http://127.0.0.1:{config.gateway.port}/health', headers=admin)).json()
                    assert status['proactive']['jobs'] == {'done': 1} and status['proactive']['unanswered'] == 1
                    assert status['batch_counts'] == {'sent': 2}
                with sqlite3.connect(tmp_path / 'hanser.db') as db:
                    assert dict(db.execute('SELECT role,COUNT(*) FROM messages GROUP BY role')) == {'user': 1, 'assistant': 2}
                    assert db.execute('SELECT ack_count FROM qq_proactive_drafts').fetchone()[0] == 1
                assert any(call['messages'][-1]['role'] == 'system' for call in calls)
        finally:
            process.terminate()
            process.wait(timeout=10)
            server.should_exit = True
            thread.join(timeout=10)
