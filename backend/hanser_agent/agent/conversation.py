from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .. import db
from ..models import ChatMessage, ChatResponse
from ..responder.performance import ReplySnapshot


@dataclass(frozen=True, slots=True)
class StoredMessage:
    id: str
    conversation_id: str
    role: str
    content: str
    turn_index: int

    def as_chat_message(self) -> ChatMessage:
        return ChatMessage(role=self.role, content=self.content)


@dataclass(frozen=True, slots=True)
class ConversationOverview:
    id: str
    user_id: str
    title: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    id: str
    conversation_id: str
    role: str
    content: str
    created_at: str
    turn_index: int


class ConversationOwnershipError(PermissionError):
    def __init__(self, conversation_id: str):
        super().__init__(f"conversation {conversation_id!r} belongs to another user")
        self.conversation_id = conversation_id


class ConversationStore:
    """Persistent conversation history backed by SQLite."""

    def __init__(self, db_path: str | Path, max_messages: int = 12):
        self.db_path = Path(db_path)
        self.max_messages = max_messages

    def get_recent(self, conversation_id: str, *, user_id: str) -> list[ChatMessage]:
        with db.connect(self.db_path) as conn:
            self._assert_owner(conn, conversation_id, user_id)
            rows = conn.execute(
                """
                SELECT id, role, content, created_at, style_example_ids_json FROM messages
                WHERE conversation_id = ?
                ORDER BY turn_index DESC
                LIMIT ?
                """,
                (conversation_id, self.max_messages),
            ).fetchall()
        return [
            ChatMessage(
                role=str(row["role"]),
                content=str(row["content"]),
                message_id=str(row["id"]),
                created_at=datetime.fromisoformat(str(row["created_at"])),
                style_example_ids=json.loads(str(row["style_example_ids_json"])),
            )
            for row in reversed(rows)
        ]

    def get_context_history(
        self,
        conversation_id: str,
        *,
        user_id: str,
        summary_through_index: int | None,
    ) -> list[ChatMessage]:
        """Return every message not covered by the summary, or the recent window."""
        if summary_through_index is None:
            return self.get_recent(conversation_id, user_id=user_id)
        with db.connect(self.db_path) as conn:
            self._assert_owner(conn, conversation_id, user_id)
            rows = conn.execute(
                """
                SELECT id, role, content, created_at, style_example_ids_json
                FROM messages
                WHERE conversation_id = ? AND turn_index > ?
                ORDER BY turn_index
                """,
                (conversation_id, summary_through_index),
            ).fetchall()
        return [
            ChatMessage(
                role=str(row["role"]),
                content=str(row["content"]),
                message_id=str(row["id"]),
                created_at=datetime.fromisoformat(str(row["created_at"])),
                style_example_ids=json.loads(str(row["style_example_ids_json"])),
            )
            for row in rows
        ]

    def list_conversations(
        self,
        *,
        user_id: str,
        limit: int,
        ending_before: str | None = None,
    ) -> tuple[list[ConversationOverview], bool]:
        with db.connect(self.db_path) as conn:
            parameters: list[object] = [user_id]
            cursor_clause = ""
            if ending_before is not None:
                cursor = conn.execute(
                    """
                    SELECT updated_at, id FROM conversations
                    WHERE id = ? AND user_id = ?
                    """,
                    (ending_before, user_id),
                ).fetchone()
                if cursor is None:
                    raise KeyError(ending_before)
                cursor_clause = """
                    AND (updated_at < ? OR (updated_at = ? AND id < ?))
                """
                parameters.extend(
                    [cursor["updated_at"], cursor["updated_at"], cursor["id"]]
                )
            parameters.append(limit + 1)
            rows = conn.execute(
                f"""
                SELECT id, user_id, title, created_at, updated_at
                FROM conversations
                WHERE user_id = ?
                {cursor_clause}
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return (
            [
                ConversationOverview(
                    id=str(row["id"]),
                    user_id=str(row["user_id"]),
                    title=str(row["title"]),
                    created_at=str(row["created_at"]),
                    updated_at=str(row["updated_at"]),
                )
                for row in rows[:limit]
            ],
            len(rows) > limit,
        )

    def get_conversation(
        self,
        conversation_id: str,
        *,
        user_id: str,
    ) -> ConversationOverview | None:
        with db.connect(self.db_path) as conn:
            self._assert_owner(conn, conversation_id, user_id)
            row = conn.execute(
                """
                SELECT id, user_id, title, created_at, updated_at
                FROM conversations WHERE id = ?
                """,
                (conversation_id,),
            ).fetchone()
        if row is None:
            return None
        return ConversationOverview(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            title=str(row["title"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def get_messages(
        self,
        conversation_id: str,
        *,
        user_id: str,
    ) -> list[ConversationMessage]:
        with db.connect(self.db_path) as conn:
            self._assert_owner(conn, conversation_id, user_id)
            rows = conn.execute(
                """
                SELECT id, conversation_id, role, content, created_at, turn_index
                FROM messages
                WHERE conversation_id = ?
                ORDER BY turn_index
                """,
                (conversation_id,),
            ).fetchall()
        return [
            ConversationMessage(
                id=str(row["id"]),
                conversation_id=str(row["conversation_id"]),
                role=str(row["role"]),
                content=str(row["content"]),
                created_at=str(row["created_at"]),
                turn_index=int(row["turn_index"]),
            )
            for row in rows
        ]

    def append_turn(
        self,
        *,
        conversation_id: str,
        user_id: str,
        user_text: str,
        assistant_text: str,
        model_name: str,
        trace_id: str,
        persona_version: str,
        user_message_id: str | None = None,
        assistant_message_id: str | None = None,
        reply_snapshot: ReplySnapshot | None = None,
        response: ChatResponse | None = None,
        request_hash: str | None = None,
        post_turn_payload: dict[str, object] | None = None,
        style_example_ids: list[str] | None = None,
    ) -> tuple[str, str]:
        now = datetime.now(timezone.utc).isoformat()
        user_message_id = user_message_id or str(uuid4())
        assistant_message_id = assistant_message_id or str(uuid4())
        if reply_snapshot is not None:
            if response is None or request_hash is None or post_turn_payload is None:
                raise ValueError("reply snapshot persistence requires response, hash, and post-turn payload")
            if reply_snapshot.reply_id != assistant_message_id:
                raise ValueError("reply snapshot reply_id must equal assistant message id")
            if reply_snapshot.conversation_id != conversation_id or reply_snapshot.user_id != user_id:
                raise ValueError("reply snapshot owner does not match conversation turn")
        with db.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_owner(conn, conversation_id, user_id)
            row = conn.execute(
                """
                SELECT COALESCE(MAX(turn_index), -1) AS last_index
                FROM messages WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
            first_index = int(row["last_index"]) + 1
            conn.execute(
                """
                INSERT INTO conversations
                    (id, user_id, title, created_at, updated_at,
                     persona_version, model_profile)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    persona_version = excluded.persona_version,
                    model_profile = excluded.model_profile
                """,
                (
                    conversation_id,
                    user_id,
                    user_text[:40],
                    now,
                    now,
                    persona_version,
                    model_name,
                ),
            )
            conn.executemany(
                """
                INSERT INTO messages
                    (id, conversation_id, role, content, created_at,
                     turn_index, model_name, trace_id, style_example_ids_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        user_message_id,
                        conversation_id,
                        "user",
                        user_text,
                        now,
                        first_index,
                        None,
                        trace_id,
                        "[]",
                    ),
                    (
                        assistant_message_id,
                        conversation_id,
                        "assistant",
                        assistant_text,
                        now,
                        first_index + 1,
                        model_name,
                        trace_id,
                        json.dumps(style_example_ids or []),
                    ),
                ],
            )
            if reply_snapshot is not None:
                conn.execute(
                    """
                    INSERT INTO reply_snapshots
                        (reply_id,request_id,request_hash,user_id,conversation_id,
                         snapshot_json,response_json,post_turn_payload_json,
                         post_turn_status,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,'pending',?,?)
                    """,
                    (
                        reply_snapshot.reply_id,
                        reply_snapshot.request_id,
                        request_hash,
                        user_id,
                        conversation_id,
                        reply_snapshot.model_dump_json(),
                        response.model_dump_json(),
                        json.dumps(post_turn_payload, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
            conn.commit()
        return user_message_id, assistant_message_id

    def get_reply_snapshot(
        self,
        reply_id: str,
        *,
        user_id: str,
    ) -> ReplySnapshot | None:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT user_id,snapshot_json FROM reply_snapshots WHERE reply_id=?",
                (reply_id,),
            ).fetchone()
        if row is None:
            return None
        if str(row["user_id"]) != user_id:
            raise ConversationOwnershipError(reply_id)
        return ReplySnapshot.model_validate_json(str(row["snapshot_json"]))

    def mark_post_turn_completed(self, request_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with db.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE reply_snapshots
                SET post_turn_status='completed',updated_at=?
                WHERE request_id=?
                """,
                (now, request_id),
            )
            conn.commit()

    @staticmethod
    def _assert_owner(conn, conversation_id: str, user_id: str) -> None:
        row = conn.execute(
            "SELECT user_id FROM conversations WHERE id=?", (conversation_id,)
        ).fetchone()
        if row is not None and str(row["user_id"]) != user_id:
            raise ConversationOwnershipError(conversation_id)

    def message_count(self, conversation_id: str, *, user_id: str) -> int:
        with db.connect(self.db_path) as conn:
            self._assert_owner(conn, conversation_id, user_id)
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchone()[0]
            )

    def messages_for_summary(
        self,
        conversation_id: str,
        *,
        user_id: str,
        after_index: int,
        through_index: int,
    ) -> list[StoredMessage]:
        with db.connect(self.db_path) as conn:
            self._assert_owner(conn, conversation_id, user_id)
            rows = conn.execute(
                """
                SELECT id, conversation_id, role, content, turn_index
                FROM messages
                WHERE conversation_id = ?
                  AND turn_index > ? AND turn_index <= ?
                ORDER BY turn_index
                """,
                (conversation_id, after_index, through_index),
            ).fetchall()
        return [
            StoredMessage(
                id=str(row["id"]),
                conversation_id=str(row["conversation_id"]),
                role=str(row["role"]),
                content=str(row["content"]),
                turn_index=int(row["turn_index"]),
            )
            for row in rows
        ]
