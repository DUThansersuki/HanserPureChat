from __future__ import annotations

import json
import random
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .events import IncomingMessage, compose_batch


class Store:
    """Durable accepted events, frozen requests and OneBot send outcomes."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, event_key TEXT NOT NULL UNIQUE,
                    conversation_id TEXT NOT NULL, bot_id TEXT NOT NULL,
                    received_at REAL NOT NULL, message_json TEXT NOT NULL, batch_id TEXT
                );
                CREATE INDEX IF NOT EXISTS pending_events ON events(conversation_id,batch_id,id);
                CREATE TABLE IF NOT EXISTS conversation_preferences (
                    conversation_id TEXT PRIMARY KEY, voice_only INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS batches (
                    id INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                    conversation_id TEXT NOT NULL, bot_id TEXT NOT NULL,
                    kind TEXT NOT NULL, target_id TEXT NOT NULL, sender_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL, status TEXT NOT NULL,
                    response_json TEXT, qq_message_id TEXT, error_code TEXT,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS delivery_parts (
                    request_id TEXT NOT NULL, part_index INTEGER NOT NULL,
                    text TEXT NOT NULL, delay_before REAL NOT NULL, status TEXT NOT NULL,
                    qq_message_id TEXT, error_code TEXT, sent_at REAL,
                    PRIMARY KEY(request_id,part_index)
                );
            """)
            if "kind" not in {row[1] for row in conn.execute("PRAGMA table_info(delivery_parts)")}:
                conn.execute("ALTER TABLE delivery_parts ADD COLUMN kind TEXT NOT NULL DEFAULT 'text'")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def accept(self, message: IncomingMessage) -> bool:
        with self.connect() as conn:
            return conn.execute(
                "INSERT OR IGNORE INTO events(event_key,conversation_id,bot_id,received_at,message_json) VALUES(?,?,?,?,?)",
                (message.event_key, message.conversation_id, message.bot_id, time.time(), message.model_dump_json()),
            ).rowcount == 1

    def recover(self):
        with self.connect() as conn:
            conn.execute("UPDATE batches SET status='queued' WHERE status='generating'")
            conn.execute("UPDATE delivery_parts SET status='send_unknown',error_code='gateway_restarted_during_send' WHERE status='sending'")
            conn.execute("UPDATE batches SET status='send_unknown',error_code='gateway_restarted_during_send' WHERE status='sending'")

    def conversation_blocked(self, conversation_id: str) -> bool:
        with self.connect() as conn:
            return conn.execute("""SELECT 1 FROM delivery_parts p JOIN batches b USING(request_id)
                WHERE b.conversation_id=? AND b.status<>'cancelled' AND p.status IN ('send_failed','send_unknown') LIMIT 1""",
                (conversation_id,)).fetchone() is not None

    def prepare_delivery(self, request_id: str, texts: list[str], delays: list[float], *, kinds: list[str] | None = None) -> list[dict]:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("SELECT 1 FROM delivery_parts WHERE request_id=? LIMIT 1", (request_id,)).fetchone():
                conn.executemany("""INSERT INTO delivery_parts(request_id,part_index,text,delay_before,kind,status)
                    VALUES(?,?,?,?,?,'ready')""", ((request_id, i, text, delay, kind) for i, (text, delay, kind)
                    in enumerate(zip(texts, delays, kinds or ['text'] * len(texts), strict=True))))
            return [dict(row) for row in conn.execute("SELECT * FROM delivery_parts WHERE request_id=? ORDER BY part_index", (request_id,))]

    def begin_part(self, request_id: str, index: int):
        with self.connect() as conn:
            conn.execute("UPDATE delivery_parts SET status='sending',error_code=NULL WHERE request_id=? AND part_index=?",
                         (request_id, index))
            conn.execute("UPDATE batches SET status='sending',error_code=NULL,updated_at=? WHERE request_id=?", (time.time(), request_id))

    def finish_part(self, request_id: str, index: int, status: str, *, message_id=None, error_code=None) -> float:
        now = time.time()
        with self.connect() as conn:
            conn.execute("""UPDATE delivery_parts SET status=?,qq_message_id=?,error_code=?,sent_at=?
                WHERE request_id=? AND part_index=?""", (status, message_id, error_code,
                now if status == 'sent' else None, request_id, index))
            batch_status = status
            if status == 'sent' and conn.execute("SELECT 1 FROM delivery_parts WHERE request_id=? AND status<>'sent' LIMIT 1", (request_id,)).fetchone():
                batch_status = 'ready'
            conn.execute("""UPDATE batches SET status=?,qq_message_id=COALESCE(?,qq_message_id),error_code=?,updated_at=?
                WHERE request_id=?""", (batch_status, message_id, error_code, now, request_id))
        return now

    def conversations(self, bot_id: str) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute("""SELECT conversation_id FROM events WHERE bot_id=? AND batch_id IS NULL
                UNION SELECT conversation_id FROM batches WHERE bot_id=? AND status IN ('queued','ready')""",
                (bot_id, bot_id)).fetchall()
        return [str(row[0]) for row in rows]

    def pending_window(self, conversation_id: str):
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) n,MIN(received_at) first,MAX(received_at) last FROM events WHERE conversation_id=? AND batch_id IS NULL", (conversation_id,)).fetchone()
        return dict(row)

    def freeze(self, conversation_id: str, *, max_messages: int, max_chars: int, speech_probability: float = 0,
               voice_enabled: bool = True):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT * FROM events WHERE conversation_id=? AND batch_id IS NULL ORDER BY id LIMIT ?", (conversation_id, max_messages)).fetchall()
            if not rows:
                return None
            selected, messages = [], []
            for row in rows:
                candidate = IncomingMessage.model_validate_json(row["message_json"])
                # Commands are standalone batches, in arrival order. A normal
                # message before /voice must keep the previous mode.
                if messages and (voice_command(candidate) is not None or voice_command(messages[0]) is not None):
                    break
                if messages and len(compose_batch([*messages, candidate])) > max_chars:
                    break
                selected.append(row)
                messages.append(candidate)
            first = messages[0]
            preference = conn.execute("SELECT voice_only FROM conversation_preferences WHERE conversation_id=?",
                                      (conversation_id,)).fetchone()
            voice_only = bool(preference[0]) if preference else False
            command = voice_command(first)
            receipt = None
            if command is not None:
                if not voice_enabled:
                    receipt = "语音服务目前没有开启"
                else:
                    voice_only = not voice_only if command == "toggle" else command == "on"
                    conn.execute("INSERT INTO conversation_preferences VALUES(?,?) ON CONFLICT(conversation_id) "
                                 "DO UPDATE SET voice_only=excluded.voice_only", (conversation_id, int(voice_only)))
                    receipt = "已开启纯语音回复" if voice_only else "已恢复20%概率语音"
            request_id = f"qq-{uuid4()}"
            payload = {"request_id": request_id, "user_id": first.user_id,
                       "conversation_id": first.conversation_id, "message": compose_batch(messages),
                       "persona_settings": {"adult_innuendo_opt_in": False},
                       # Draw once inside the freeze transaction. Retries and restarts reuse this payload.
                       "output_preferences": {"text": True, "speech": receipt is None and voice_enabled and (voice_only or
                           speech_probability > 0 and random.random() < speech_probability)},
                       "speech_required": receipt is None and voice_enabled and voice_only}
            now = time.time()
            conn.execute("""INSERT INTO batches(request_id,conversation_id,bot_id,kind,target_id,sender_id,
                payload_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'queued',?,?)""",
                (request_id, conversation_id, first.bot_id, first.kind, first.group_id or first.sender_id,
                 first.sender_id, json.dumps(payload, ensure_ascii=False), now, now))
            conn.executemany("UPDATE events SET batch_id=? WHERE id=?", [(request_id, row["id"]) for row in selected])
            if receipt:
                conn.execute("UPDATE batches SET status='ready',response_json=? WHERE request_id=?",
                             (json.dumps({"request_id": request_id, "text": receipt}, ensure_ascii=False), request_id))
            return dict(conn.execute("SELECT * FROM batches WHERE request_id=?", (request_id,)).fetchone())

    def next_batch(self, conversation_id: str):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM batches WHERE conversation_id=? AND status IN ('queued','ready') ORDER BY id LIMIT 1", (conversation_id,)).fetchone()
        return dict(row) if row else None

    def get(self, request_id: str):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM batches WHERE request_id=?", (request_id,)).fetchone()
        return dict(row) if row else None

    def update(self, request_id: str, status: str, *, response: dict | None = None,
               qq_message_id: str | None = None, error_code: str | None = None):
        with self.connect() as conn:
            conn.execute("""UPDATE batches SET status=?,response_json=COALESCE(?,response_json),
                qq_message_id=COALESCE(?,qq_message_id),error_code=?,updated_at=? WHERE request_id=?""",
                (status, json.dumps(response, ensure_ascii=False) if response is not None else None,
                 qq_message_id, error_code, time.time(), request_id))

    def retry(self, request_id: str, *, allow_unknown: bool = False):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM batches WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                raise KeyError(request_id)
            allowed = {"backend_failed", "send_failed", "policy_blocked"}
            if allow_unknown:
                allowed.add("send_unknown")
            if row["status"] not in allowed:
                raise ValueError("only failed batches can be retried; uncertain sends require allow_unknown=true")
            status = "ready" if row["response_json"] else "queued"
            conn.execute("""UPDATE delivery_parts SET status='ready',error_code=NULL
                WHERE request_id=? AND status IN ('send_failed','send_unknown')""", (request_id,))
            conn.execute("UPDATE batches SET status=?,error_code=NULL,updated_at=? WHERE request_id=?", (status, time.time(), request_id))
        return row["bot_id"], row["conversation_id"]

    def summary(self):
        with self.connect() as conn:
            counts = {row["status"]: row["n"] for row in conn.execute("SELECT status,COUNT(*) n FROM batches GROUP BY status")}
            part_counts = {row["status"]: row["n"] for row in conn.execute("SELECT status,COUNT(*) n FROM delivery_parts GROUP BY status")}
            pending = conn.execute("SELECT COUNT(*) FROM events WHERE batch_id IS NULL").fetchone()[0]
            failures = [dict(row) for row in conn.execute("""SELECT request_id,status,error_code,updated_at
                FROM batches WHERE status IN ('backend_failed','send_failed','send_unknown','policy_blocked') ORDER BY id DESC LIMIT 20""")]
        return {"batch_counts": counts, "part_counts": part_counts, "pending_events": pending, "failures": failures}


def voice_command(message):
    if any(segment["type"] not in {"text", "at", "reply"} for segment in message.segments):
        return None
    match = re.fullmatch(r"/voice(?:\s+(on|off))?", message.text.strip(), re.IGNORECASE)
    return (match[1].lower() if match[1] else "toggle") if match else None
