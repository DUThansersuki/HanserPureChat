"""Private-chat initiative: persisted scheduling, explicit controls and delivery receipts."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import random
import re
import time
from uuid import uuid4

from .client import BackendFailure

TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
log = logging.getLogger(__name__)


def day(now):
    return datetime.fromtimestamp(now, TZ).date().isoformat()


def awake(settings, now):
    clock = datetime.fromtimestamp(now, TZ)
    return settings.start_hour <= clock.hour + clock.minute / 60 < settings.end_hour


def controls(text, now):
    # Only direct, unquoted first-party controls alter the proactive session.
    plain = re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"|\[引用[^\]]*\]', '', text)
    if re.search(r'他说|她说|朋友说|例如|比如|假如|如果', plain):
        return {}
    if re.search(r'(?:别|不要|停止|关闭|不用|不许)(?:再)?(?:主动(?:找我|联系我|(?:给我)?发(?:消息|信息)|聊天)|主动话题)', plain):
        return {'disabled': 1}
    if re.search(r'(?:开启|恢复|可以|允许)(?:你)?主动(?:找我|联系我|发消息|聊天|话题)', plain):
        return {'disabled': 0, 'paused_until': 0}
    if re.search(r'晚安|我(?:先|要|去)?睡(?:了|觉)|明天再聊', plain):
        clock = datetime.fromtimestamp(now, TZ)
        wake = (clock + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        return {'paused_until': wake.timestamp()}
    if re.search(r'我(?:现在|正在|先)?(?:忙|开会)|有空再聊|晚点再聊|先别打扰', plain):
        return {'paused_until': now + 6 * 3600}
    return {}


class Proactive:
    def __init__(self, gateway, *, clock=time.time):
        self.gateway, self.store, self.clock = gateway, gateway.store, clock
        self.settings = gateway.settings.proactive
        self.task = None
        self.generating = {}
        with self.store.connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS proactive_sessions (
                    conversation_id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, sender_id TEXT NOT NULL,
                    last_event INTEGER NOT NULL, last_received REAL NOT NULL, due_at REAL NOT NULL,
                    disabled INTEGER NOT NULL DEFAULT 0, paused_until REAL NOT NULL DEFAULT 0,
                    unanswered INTEGER NOT NULL DEFAULT 0, sent_day TEXT, sent_count INTEGER NOT NULL DEFAULT 0,
                    attempt_day TEXT, attempts INTEGER NOT NULL DEFAULT 0, reason TEXT
                );
                CREATE TABLE IF NOT EXISTS proactive_jobs (
                    request_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, anchor_event INTEGER NOT NULL,
                    expires_at REAL NOT NULL, status TEXT NOT NULL, history_count INTEGER NOT NULL DEFAULT 0,
                    counted INTEGER NOT NULL DEFAULT 0
                );
            ''')
        # Existing conversations start a fresh idle window on first enable.
        with self.store.connect() as c:
            rows = c.execute('SELECT e.* FROM events e JOIN (SELECT conversation_id,MAX(id) id FROM events GROUP BY conversation_id) x USING(id)').fetchall()
        for row in rows:
            data = json.loads(row['message_json'])
            if data['kind'] == 'private':
                self._remember(row['id'], row['conversation_id'], data['bot_id'], data['sender_id'], self.clock(), data['text'], seed=True, received_at=row['received_at'])

    def delay(self):
        return self.settings.idle_seconds + random.uniform(0, self.settings.jitter_seconds)

    def _remember(self, event_id, conversation_id, bot_id, sender_id, now, text, *, seed=False, received_at=None):
        received_at = now if received_at is None else received_at
        patch = controls(text, received_at)
        with self.store.connect() as c:
            if seed and c.execute('SELECT 1 FROM proactive_sessions WHERE conversation_id=?', (conversation_id,)).fetchone():
                return
            c.execute('''INSERT INTO proactive_sessions(conversation_id,bot_id,sender_id,last_event,last_received,due_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET
                last_event=excluded.last_event,last_received=excluded.last_received,due_at=excluded.due_at,
                unanswered=0,reason=NULL''', (conversation_id, bot_id, sender_id, event_id, received_at, now + self.delay()))
            for key, value in patch.items():
                c.execute(f'UPDATE proactive_sessions SET {key}=? WHERE conversation_id=?', (value, conversation_id))
            c.execute("UPDATE batches SET status='cancelled',error_code='proactive_new_input' WHERE request_id IN (SELECT request_id FROM proactive_jobs WHERE conversation_id=? AND status='active') AND status IN ('queued','generating','ready','send_failed','send_unknown')", (conversation_id,))
            c.execute("UPDATE proactive_jobs SET status='cancelled' WHERE conversation_id=? AND status='active'", (conversation_id,))

    def on_message(self, message):
        if message.kind != 'private':
            return
        with self.store.connect() as c:
            event_id = c.execute('SELECT id FROM events WHERE event_key=?', (message.event_key,)).fetchone()[0]
        self._remember(event_id, message.conversation_id, message.bot_id, message.sender_id, self.clock(), message.text)
        running = self.generating.get(message.conversation_id)
        if running:
            running.cancel()

    async def generate(self, payload):
        task = asyncio.create_task(self.gateway.backend.proactive(payload))
        self.generating[payload['conversation_id']] = task
        try:
            return await task
        finally:
            self.generating.pop(payload['conversation_id'], None)

    def job(self, request_id):
        with self.store.connect() as c:
            row = c.execute('SELECT * FROM proactive_jobs WHERE request_id=?', (request_id,)).fetchone()
        return dict(row) if row else None

    def cancel(self, request_id, reason):
        with self.store.connect() as c:
            c.execute("UPDATE proactive_jobs SET status='cancelled' WHERE request_id=?", (request_id,))
            c.execute("UPDATE batches SET status='cancelled',error_code=? WHERE request_id=?", (reason, request_id))

    def allowed(self, batch):
        now = self.clock()
        job = self.job(batch['request_id'])
        with self.store.connect() as c:
            s = c.execute('SELECT * FROM proactive_sessions WHERE conversation_id=?', (batch['conversation_id'],)).fetchone()
        return bool(job and job['status'] == 'active' and s and self.settings.enabled
            and batch['sender_id'] in self.gateway.settings.private_users
            and self.gateway.batch_allowed(batch) and awake(self.settings, now)
            and not s['disabled'] and s['paused_until'] <= now
            and s['last_event'] == job['anchor_event'] and now <= job['expires_at'])

    async def sync_history(self, conversation_id=None):
        with self.store.connect() as c:
            rows = c.execute('''SELECT j.*,b.status batch_status FROM proactive_jobs j JOIN batches b USING(request_id)
                WHERE (? IS NULL OR j.conversation_id=?) AND EXISTS(SELECT 1 FROM delivery_parts p
                    WHERE p.request_id=j.request_id AND p.status='sent' AND p.part_index>=j.history_count)''',
                (conversation_id, conversation_id)).fetchall()
        for row in rows:
            with self.store.connect() as c:
                parts = c.execute("SELECT * FROM delivery_parts WHERE request_id=? AND status='sent' ORDER BY part_index", (row['request_id'],)).fetchall()
                batch = dict(c.execute('SELECT * FROM batches WHERE request_id=?', (row['request_id'],)).fetchone())
                if not row['counted']:
                    today = day(self.clock())
                    c.execute('''UPDATE proactive_sessions SET sent_count=CASE WHEN sent_day=? THEN sent_count+1 ELSE 1 END,
                        sent_day=?,unanswered=CASE WHEN last_event=? THEN 1 ELSE unanswered END
                        WHERE conversation_id=?''', (today, today, row['anchor_event'], row['conversation_id']))
                    c.execute('UPDATE proactive_jobs SET counted=1 WHERE request_id=?', (row['request_id'],))
            await self.gateway.backend.proactive_ack({'request_id': row['request_id'],
                'user_id': json.loads(batch['payload_json'])['user_id'], 'conversation_id': row['conversation_id'],
                'delivered_parts': len(parts), 'message_ids': [p['qq_message_id'] for p in parts]})
            with self.store.connect() as c:
                c.execute('UPDATE proactive_jobs SET history_count=?,status=CASE WHEN ?=\'sent\' THEN \'done\' ELSE status END WHERE request_id=?',
                    (len(parts), row['batch_status'], row['request_id']))

    async def tick(self):
        try:
            await self.sync_history()
        except BackendFailure:
            return
        now = self.clock()
        with self.store.connect() as c:
            rows = c.execute('SELECT * FROM proactive_sessions WHERE due_at<=?', (now,)).fetchall()
        for s in rows:
            bot = self.gateway.connections.get(s['bot_id'], {})
            if not self.settings.enabled or s['sender_id'] not in self.gateway.settings.private_users:
                continue
            if not bot.get('connected') or bot.get('online') is not True:
                continue
            if self.store.conversation_blocked(s['conversation_id']):
                continue
            today = day(now)
            if (not awake(self.settings, now) or s['disabled'] or s['paused_until'] > now or s['unanswered']
                or (s['sent_day'] == today and s['sent_count'] >= self.settings.daily_limit)
                or (s['attempt_day'] == today and s['attempts'] >= self.settings.daily_limit)):
                continue
            if now - s['due_at'] > self.settings.expiry_seconds:
                with self.store.connect() as c:
                    c.execute("UPDATE proactive_sessions SET due_at=?,reason='stale_schedule_skipped' WHERE conversation_id=?", (now + self.delay(), s['conversation_id']))
                continue
            with self.store.connect() as c:
                busy = c.execute("SELECT 1 FROM batches WHERE conversation_id=? AND status IN ('queued','generating','ready','sending') UNION ALL SELECT 1 FROM events WHERE conversation_id=? AND batch_id IS NULL LIMIT 1", (s['conversation_id'], s['conversation_id'])).fetchone()
            if busy or s['conversation_id'] in self.gateway.workers:
                continue
            request_id = 'qq-proactive-' + uuid4().hex
            payload = {'request_id': request_id, 'user_id': f"qq:{s['bot_id']}:{s['sender_id']}",
                'conversation_id': s['conversation_id'], 'anchor_received_at': s['last_received']}
            batch = {'request_id': request_id, 'bot_id': s['bot_id'], 'sender_id': s['sender_id'], 'kind': 'private'}
            if not self.gateway.batch_allowed(batch):
                continue
            with self.store.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                c.execute('''INSERT INTO batches(request_id,conversation_id,bot_id,kind,target_id,sender_id,payload_json,status,created_at,updated_at)
                    VALUES(?,?,?,'private',?,?,?,'queued',?,?)''', (request_id, s['conversation_id'], s['bot_id'], s['sender_id'], s['sender_id'], json.dumps(payload), now, now))
                c.execute("INSERT INTO proactive_jobs(request_id,conversation_id,anchor_event,expires_at,status) VALUES(?,?,?,?,'active')", (request_id, s['conversation_id'], s['last_event'], now + self.settings.expiry_seconds))
                c.execute('''UPDATE proactive_sessions SET attempt_day=?,attempts=CASE WHEN attempt_day=? THEN attempts+1 ELSE 1 END,
                    due_at=? WHERE conversation_id=?''', (today, today, now + self.delay(), s['conversation_id']))
            self.gateway.kick(s['conversation_id'])
        # Also resume durable ready jobs and pending normal input after a sync failure.
        for bot_id in self.gateway.connections:
            if self.gateway.available(bot_id):
                for conversation_id in self.store.conversations(bot_id):
                    self.gateway.kick(conversation_id)

    async def run(self):
        while not self.gateway.closing:
            try:
                await self.tick()
            except Exception:
                log.exception('proactive scheduler tick failed')
            await asyncio.sleep(self.settings.poll_seconds)

    def start(self):
        self.task = asyncio.create_task(self.run())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    def summary(self):
        with self.store.connect() as c:
            jobs = {r[0]: r[1] for r in c.execute('SELECT status,COUNT(*) FROM proactive_jobs GROUP BY status')}
            sessions = c.execute('SELECT COUNT(*),SUM(disabled),SUM(unanswered) FROM proactive_sessions').fetchone()
            pending = c.execute("SELECT COUNT(*) FROM proactive_jobs j WHERE EXISTS(SELECT 1 FROM delivery_parts p WHERE p.request_id=j.request_id AND p.status='sent' AND p.part_index>=j.history_count)").fetchone()[0]
        return {'enabled': self.settings.enabled, 'private_only': True, 'idle_seconds': self.settings.idle_seconds,
            'daily_limit': self.settings.daily_limit, 'hours': [self.settings.start_hour, self.settings.end_hour],
            'sessions': sessions[0], 'opted_out': sessions[1] or 0, 'unanswered': sessions[2] or 0,
            'pending_history_receipts': pending, 'jobs': jobs}
