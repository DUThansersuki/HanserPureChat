import asyncio

import pytest

from hanser_qq.client import BackendFailure
from hanser_qq.engine import Gateway, SendRejected
from hanser_qq.store import Store
from test_events import incoming


class Backend:
    def __init__(self):
        self.calls = []
        self.block = None
        self.started = asyncio.Event()
        self.fail_once = False

    async def chat(self, payload):
        self.calls.append(payload)
        self.started.set()
        if self.fail_once:
            self.fail_once = False
            raise BackendFailure("temporary", retryable=True)
        if self.block:
            await self.block.wait()
        return {"request_id": payload["request_id"], "reply_id": "reply-" + payload["request_id"], "text": "收到"}

    async def close(self):
        pass


async def settled(gateway):
    for _ in range(200):
        if not gateway.workers:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("gateway workers did not settle")


def make_gateway(config, *, backend=None, sender=None):
    calls = []
    async def send(batch, text):
        calls.append((batch, text))
        return {"message_id": len(calls)}
    gateway = Gateway(config.gateway, Store(config.storage_path), backend or Backend(), sender or send)
    gateway.connection("100", connected=True, online=True)
    return gateway, calls


async def test_duplicate_and_restart_do_not_generate_or_send_again(config):
    gateway, sends = make_gateway(config)
    assert gateway.receive(incoming()) == "accepted"
    assert gateway.receive(incoming()) == "duplicate"
    gateway.receive(incoming("2", text="再补一句"))
    await settled(gateway)
    assert len(gateway.backend.calls) == len(sends) == 1
    assert gateway.backend.calls[0]["message"] == "你好\n再补一句"
    await gateway.close()
    restarted, new_sends = make_gateway(config)
    assert restarted.receive(incoming()) == "duplicate"
    await settled(restarted)
    assert new_sends == []
    await restarted.close()


async def test_new_message_does_not_cancel_running_generation(config):
    backend = Backend()
    backend.block = asyncio.Event()
    gateway, sends = make_gateway(config, backend=backend)
    gateway.receive(incoming())
    await asyncio.wait_for(backend.started.wait(), 1)
    gateway.receive(incoming("2", text="新的消息"))
    backend.block.set()
    await settled(gateway)
    assert len(backend.calls) == len(sends) == 2
    assert backend.calls[0]["message"] == "你好"
    assert backend.calls[1]["message"] == "新的消息"
    await gateway.close()


async def test_retry_uses_frozen_request_and_settings(config):
    backend = Backend()
    backend.fail_once = True
    gateway, sends = make_gateway(config, backend=backend)
    gateway.receive(incoming())
    await settled(gateway)
    assert len(backend.calls) == 2 and backend.calls[0] == backend.calls[1]
    assert len(sends) == 1
    await gateway.close()


async def test_send_unknown_is_not_automatically_repeated_on_reconnect(config):
    calls = []
    async def ambiguous(batch, text):
        calls.append(batch["request_id"])
        raise TimeoutError("ack missing")
    gateway, _ = make_gateway(config, sender=ambiguous)
    gateway.receive(incoming())
    await settled(gateway)
    request_id = calls[0]
    assert gateway.store.get(request_id)["status"] == "send_unknown"
    gateway.connection("100", connected=False)
    gateway.connection("100", connected=True, online=True)
    await settled(gateway)
    assert len(calls) == 1
    with pytest.raises(ValueError):
        gateway.retry(request_id)
    await gateway.close()


async def test_rejected_send_retry_reuses_reply_without_generation(config):
    sends = []
    async def send(batch, text):
        sends.append(batch["request_id"])
        if len(sends) == 1:
            raise SendRejected()
        return {"message_id": 99}
    gateway, _ = make_gateway(config, sender=send)
    gateway.receive(incoming())
    await settled(gateway)
    assert gateway.store.get(sends[0])["status"] == "send_failed"
    gateway.retry(sends[0])
    await settled(gateway)
    assert len(gateway.backend.calls) == 1 and len(sends) == 2
    assert gateway.store.get(sends[0])["qq_message_id"] == "99"
    await gateway.close()


async def test_offline_pending_survives_and_resumes(config):
    gateway, sends = make_gateway(config)
    gateway.connection("100", connected=False)
    gateway.receive(incoming())
    await settled(gateway)
    assert not sends and not gateway.backend.calls
    gateway.connection("100", connected=True, online=True)
    await settled(gateway)
    assert len(sends) == 1
    await gateway.close()


async def test_revoked_group_policy_blocks_pending_reply(config):
    gateway, sends = make_gateway(config)
    gateway.connection("100", connected=False)
    gateway.receive(incoming(kind="group", addressed=True))
    await settled(gateway)
    config.gateway.groups = []
    gateway.connection("100", connected=True, online=True)
    await settled(gateway)
    assert not sends and not gateway.backend.calls
    assert gateway.store.summary()["batch_counts"] == {"policy_blocked": 1}
    await gateway.close()


async def test_backend_failure_keeps_same_batch_for_admin_retry(config):
    class Failing(Backend):
        fail = True
        async def chat(self, payload):
            if self.fail:
                self.calls.append(payload)
                raise BackendFailure("not_configured", retryable=False)
            return await super().chat(payload)
    backend = Failing()
    gateway, sends = make_gateway(config, backend=backend)
    gateway.receive(incoming())
    await settled(gateway)
    request_id = backend.calls[0]["request_id"]
    assert gateway.store.get(request_id)["status"] == "backend_failed" and not sends
    backend.fail = False
    gateway.retry(request_id)
    await settled(gateway)
    assert backend.calls[0] == backend.calls[1] and len(sends) == 1
    await gateway.close()


async def test_continuous_input_has_maximum_wait(config):
    config.gateway.debounce_seconds = 0.04
    config.gateway.max_wait_seconds = 0.07
    gateway, sends = make_gateway(config)
    for index in range(10):
        gateway.receive(incoming(str(index)))
        await asyncio.sleep(0.012)
    assert sends  # Traffic never went quiet, but the first batch was flushed.
    await settled(gateway)
    with gateway.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM events WHERE batch_id IS NULL").fetchone()[0] == 0
    await gateway.close()


async def test_batch_size_budget_preserves_every_event(config):
    config.gateway.max_message_chars = 8
    gateway, _ = make_gateway(config)
    for index in range(5):
        gateway.receive(incoming(str(index), text="四个字符"))
    await settled(gateway)
    assert len(gateway.backend.calls) == 5
    assert all(len(call["message"]) <= 8 for call in gateway.backend.calls)
    assert gateway.receive(incoming("99", text="x" * 9)) == "too_large"
    await gateway.close()


def test_crash_recovers_generation_but_marks_send_unknown(config):
    store = Store(config.storage_path)
    store.accept(incoming())
    batch = store.freeze(incoming().conversation_id, max_messages=10, max_chars=100)
    store.update(batch["request_id"], "generating")
    store.recover()
    assert store.get(batch["request_id"])["status"] == "queued"
    store.update(batch["request_id"], "sending")
    store.recover()
    assert store.get(batch["request_id"])["status"] == "send_unknown"
