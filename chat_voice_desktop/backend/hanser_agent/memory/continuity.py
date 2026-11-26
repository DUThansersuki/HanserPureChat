from __future__ import annotations

import re

from ..tokenizer import STOP_WORDS, tokenize
from .store import MemoryStore


def continuity_tokens(text: str) -> list[str]:
    """Small lexical index shared by desktop and legacy backends."""
    text = text.casefold()
    terms = set(tokenize(text))
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        terms.update(run[index:index + 2] for index in range(len(run) - 1))
    terms.update(re.findall(r"[a-z0-9][a-z0-9._+-]*", text))
    return sorted(terms - STOP_WORDS)


class ConversationContinuity:
    """Index saved two-sided interactions, without asserting shared life events.

    Entries only reference immutable messages. Memory revisions are checked at
    read time, so deleted or superseded claims cannot re-enter through this index.
    """

    def __init__(self, memories: MemoryStore):
        self.memories = memories

    def index_new(self, *, user_id: str, conversation_id: str | None = None) -> int:
        indexed = 0
        with self.memories._connection() as conn:
            conversations = conn.execute(
                "SELECT id FROM conversations WHERE user_id=? AND (? IS NULL OR id=?)",
                (user_id, conversation_id, conversation_id),
            ).fetchall()
            for conversation in conversations:
                cid = str(conversation["id"])
                checkpoint = conn.execute(
                    "SELECT through_index FROM continuity_checkpoints WHERE conversation_id=?", (cid,),
                ).fetchone()
                after = int(checkpoint["through_index"]) if checkpoint else -1
                rows = conn.execute(
                    """SELECT u.id,u.content AS user_text,a.id AS assistant_id,
                              a.content AS assistant_text,a.turn_index
                       FROM messages u JOIN messages a
                         ON a.conversation_id=u.conversation_id AND a.turn_index=u.turn_index+1
                       WHERE u.conversation_id=? AND u.turn_index>? AND u.role='user'
                         AND a.role='assistant' ORDER BY u.turn_index""", (cid, after),
                ).fetchall()
                for row in rows:
                    conn.execute(
                        "INSERT INTO continuity_turns(id,user_id,conversation_id,assistant_message_id,through_index) VALUES(?,?,?,?,?)",
                        (row["id"], user_id, cid, row["assistant_id"], row["turn_index"]),
                    )
                    conn.executemany(
                        "INSERT INTO continuity_tokens(token,turn_id,user_id) VALUES(?,?,?)",
                        [(token, row["id"], user_id) for token in continuity_tokens(
                            row["user_text"] + " " + row["assistant_text"]
                        )],
                    )
                if rows:
                    conn.execute(
                        "INSERT INTO continuity_checkpoints(conversation_id,through_index) VALUES(?,?) "
                        "ON CONFLICT(conversation_id) DO UPDATE SET through_index=excluded.through_index",
                        (cid, rows[-1]["turn_index"]),
                    )
                indexed += len(rows)
        return indexed

    @staticmethod
    def _eligible_clause() -> str:
        # Blocking both halves prevents an assistant's old confirmation from
        # reviving the user's forgotten/corrected claim. Human edits retain the
        # original message IDs through the revision chain.
        return """NOT EXISTS (
            SELECT 1 FROM memories m, json_each(m.source_message_ids_json) source
            WHERE m.user_id=t.user_id AND source.value IN (t.id,t.assistant_message_id)
              AND (m.status IN ('deleted','superseded') OR m.source_kind='human_edit')
        )"""

    def turns(self, *, user_id: str, conversation_id: str, through_index: int, limit: int = 4) -> list[dict]:
        with self.memories._connection() as conn:
            rows = conn.execute(
                f"""SELECT t.*,u.content AS user_text,a.content AS assistant_text,u.created_at AS occurred_at
                    FROM continuity_turns t JOIN messages u ON u.id=t.id
                    JOIN messages a ON a.id=t.assistant_message_id
                    WHERE t.user_id=? AND t.conversation_id=? AND t.through_index<=?
                      AND {self._eligible_clause()}
                    ORDER BY t.through_index DESC LIMIT ?""",
                (user_id, conversation_id, through_index, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def search(self, *, user_id: str, conversation_id: str, through_index: int, query: str, limit: int = 4) -> list[dict]:
        terms = continuity_tokens(query)
        if not terms:
            return []
        placeholders = ",".join("?" for _ in terms)
        with self.memories._connection() as conn:
            rows = conn.execute(
                f"""SELECT t.*,u.content AS user_text,a.content AS assistant_text,u.created_at AS occurred_at,
                           COUNT(DISTINCT k.token) AS matches
                    FROM continuity_tokens k JOIN continuity_turns t ON t.id=k.turn_id
                    JOIN messages u ON u.id=t.id JOIN messages a ON a.id=t.assistant_message_id
                    WHERE k.user_id=? AND k.token IN ({placeholders})
                      AND (t.conversation_id<>? OR t.through_index<=?)
                      AND {self._eligible_clause()}
                    GROUP BY t.id ORDER BY matches DESC,u.created_at DESC,u.rowid DESC LIMIT ?""",
                (user_id, *terms, conversation_id, through_index, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def facts(self, *, user_id: str, conversation_id: str, limit: int = 8) -> list[dict]:
        with self.memories._connection() as conn:
            rows = conn.execute(
                """SELECT id,content,status,type,source_message_ids_json,created_at FROM memories
                   WHERE user_id=? AND validity IN ('asserted','verified')
                     AND status IN ('active','closed')
                     AND (memory_scope='global' OR conversation_id=?)
                   ORDER BY CASE WHEN type='unresolved_thread' AND status='active' THEN 0
                                 WHEN assertion_type='correction' OR source_kind='human_edit' THEN 1
                                 ELSE 2 END,created_at DESC LIMIT ?""",
                (user_id, conversation_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]
