from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .. import db
from ..models import (
    ConversationSummary,
    MemoryCandidate,
    MemoryItem,
    MemoryPatch,
    RelationshipState,
    SceneState,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryStore:
    """SQLite source of truth for long-term memory and conversational state."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def upsert_candidate(
        self,
        candidate: MemoryCandidate,
    ) -> tuple[MemoryItem, bool]:
        with db.connect(self.db_path) as conn:
            self._validate_candidate_sources(conn, candidate)
            existing = conn.execute(
                """
                SELECT * FROM memories
                WHERE user_id = ? AND type = ? AND memory_key = ?
                  AND memory_scope = ?
                  AND (? = 'global' OR conversation_id = ?)
                  AND status = 'active'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (
                    candidate.user_id,
                    candidate.type,
                    candidate.memory_key,
                    candidate.memory_scope,
                    candidate.memory_scope,
                    candidate.conversation_id,
                ),
            ).fetchone()
            if existing and str(existing["content"]) == candidate.content:
                return self._memory_from_row(existing), False
            if existing:
                conn.execute(
                    "UPDATE memories SET status = 'superseded' WHERE id = ?",
                    (str(existing["id"]),),
                )

            memory_id = str(uuid4())
            created_at = utc_now()
            conn.execute(
                """
                INSERT INTO memories
                    (id, user_id, conversation_id, type, memory_key, content,
                     importance, confidence, status, created_at,
                     last_accessed_at, source_message_ids_json, embedding_ref,
                     assertion_type, polarity, validity, subject, predicate,
                     object_value, revision_of_id, source_kind, memory_scope,
                     address_kind, context_tags_json, address_priority)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, NULL, ?, NULL,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    candidate.user_id,
                    candidate.conversation_id,
                    candidate.type,
                    candidate.memory_key,
                    candidate.content,
                    candidate.importance,
                    candidate.confidence,
                    created_at.isoformat(),
                    json.dumps(candidate.source_message_ids),
                    candidate.assertion_type,
                    candidate.polarity,
                    candidate.validity,
                    candidate.subject,
                    candidate.predicate,
                    candidate.object_value,
                    str(existing["id"]) if existing else None,
                    candidate.source_kind,
                    candidate.memory_scope,
                    candidate.address_kind,
                    json.dumps(candidate.context_tags, ensure_ascii=False),
                    candidate.address_priority,
                ),
            )
            conn.commit()
        return MemoryItem(
            id=memory_id,
            user_id=candidate.user_id,
            conversation_id=candidate.conversation_id,
            type=candidate.type,
            memory_key=candidate.memory_key,
            content=candidate.content,
            importance=candidate.importance,
            confidence=candidate.confidence,
            source_message_ids=candidate.source_message_ids,
            assertion_type=candidate.assertion_type,
            polarity=candidate.polarity,
            validity=candidate.validity,
            subject=candidate.subject,
            predicate=candidate.predicate,
            object_value=candidate.object_value,
            revision_of_id=str(existing["id"]) if existing else None,
            source_kind=candidate.source_kind,
            memory_scope=candidate.memory_scope,
            address_kind=candidate.address_kind,
            context_tags=candidate.context_tags,
            address_priority=candidate.address_priority,
            created_at=created_at,
        ), True

    def set_embedding_ref(self, memory_id: str) -> None:
        with db.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE memories SET embedding_ref = ? WHERE id = ?",
                (f"memories:{memory_id}", memory_id),
            )
            conn.commit()

    def needs_embedding(self, memory_id: str) -> bool:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT embedding_ref FROM memories WHERE id=?", (memory_id,)
            ).fetchone()
        return row is not None and row["embedding_ref"] is None

    def memories_by_source_message(self, source_message_id: str) -> list[MemoryItem]:
        with db.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT m.* FROM memories m
                JOIN json_each(m.source_message_ids_json) source
                  ON source.value = ?
                WHERE m.status='active'
                """,
                (source_message_id,),
            ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def is_post_turn_committed(self, user_message_id: str) -> bool:
        with db.connect(self.db_path) as conn:
            return conn.execute(
                "SELECT 1 FROM post_turn_commits WHERE user_message_id=?",
                (user_message_id,),
            ).fetchone() is not None

    def mark_post_turn_committed(
        self, *, user_message_id: str, conversation_id: str
    ) -> None:
        with db.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO post_turn_commits "
                "(user_message_id,conversation_id,committed_at) "
                "VALUES (?,?,?)",
                (user_message_id, conversation_id, utc_now().isoformat()),
            )
            conn.commit()

    def save_permission_events(
        self,
        *,
        user_id: str,
        conversation_id: str,
        events: list[object],
    ) -> None:
        durable = [
            item for item in events
            if getattr(item, "scope", None) in {"conversation", "user"}
        ]
        if not durable:
            return
        with db.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO persona_permission_events
                    (source_message_id,event_index,user_id,conversation_id,scope,
                     event_json,created_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                [
                    (
                        item.source_message_id,
                        index,
                        user_id,
                        conversation_id,
                        item.scope,
                        item.model_dump_json(),
                        item.created_at.isoformat(),
                    )
                    for index, item in enumerate(durable)
                ],
            )
            conn.commit()

    def list_permission_events(self, *, user_id: str, conversation_id: str):
        from ..persona.schemas import ExplicitPreferenceEvent

        with db.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT event_json FROM persona_permission_events
                WHERE user_id=? AND (scope='user' OR conversation_id=?)
                ORDER BY created_at, source_message_id, event_index
                """,
                (user_id, conversation_id),
            ).fetchall()
        return [
            ExplicitPreferenceEvent.model_validate_json(str(row["event_json"]))
            for row in rows
        ]

    def list_memories(
        self,
        *,
        user_id: str,
        status: str = "active",
        conversation_id: str | None = None,
    ) -> list[MemoryItem]:
        sql = "SELECT * FROM memories WHERE user_id = ? AND status = ?"
        params: list[object] = [user_id, status]
        if conversation_id is not None:
            sql += " AND conversation_id = ?"
            params.append(conversation_id)
        sql += " ORDER BY created_at DESC"
        with db.connect(self.db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def active_memories_by_ids(
        self,
        *,
        user_id: str,
        memory_ids: list[str],
    ) -> list[MemoryItem]:
        if not memory_ids:
            return []
        placeholders = ",".join("?" for _ in memory_ids)
        with db.connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM memories
                WHERE user_id = ? AND status = 'active'
                  AND validity IN ('asserted', 'verified')
                  AND id IN ({placeholders})
                """,
                [user_id, *memory_ids],
            ).fetchall()
        by_id = {
            str(row["id"]): self._memory_from_row(row)
            for row in rows
        }
        return [by_id[value] for value in memory_ids if value in by_id]

    def eligible_memory_ids(
        self, *, user_id: str, conversation_id: str | None = None
    ) -> list[str]:
        scope_clause = ""
        params: list[object] = [user_id]
        if conversation_id is not None:
            scope_clause = (
                "AND (memory_scope='global' OR "
                "(memory_scope='conversation' AND conversation_id=?))"
            )
            params.append(conversation_id)
        with db.connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT id FROM memories
                WHERE user_id = ? AND status = 'active'
                  AND validity IN ('asserted', 'verified')
                  AND embedding_ref IS NOT NULL
                  {scope_clause}
                ORDER BY created_at DESC
                """,
                params,
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def has_active_memories(self, user_id: str) -> bool:
        with db.connect(self.db_path) as conn:
            return bool(
                conn.execute(
                    """
                    SELECT 1 FROM memories
                    WHERE user_id = ? AND status = 'active'
                    LIMIT 1
                    """,
                    (user_id,),
                ).fetchone()
            )

    def list_address_options(
        self,
        *,
        user_id: str,
        limit: int = 6,
    ) -> list[MemoryItem]:
        with db.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM memories
                WHERE user_id = ? AND status = 'active'
                  AND validity IN ('asserted', 'verified')
                  AND predicate = 'preferred_address'
                  AND address_kind IS NOT NULL
                  AND memory_scope = 'global'
                ORDER BY address_priority DESC, created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def update_memory(
        self,
        memory_id: str,
        patch: MemoryPatch,
    ) -> MemoryItem:
        updates = patch.model_dump(exclude_none=True)
        with db.connect(self.db_path) as conn:
            original = conn.execute(
                "SELECT * FROM memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
            if original is None:
                raise KeyError(memory_id)
            conn.execute(
                "UPDATE memories SET status = 'superseded' WHERE id = ?",
                (memory_id,),
            )
            new_id = str(uuid4())
            created_at = utc_now().isoformat()
            content_changed = "content" in updates
            conn.execute(
                """
                INSERT INTO memories
                    (id, user_id, conversation_id, type, memory_key, content,
                     importance, confidence, status, created_at,
                     last_accessed_at, source_message_ids_json, embedding_ref,
                     assertion_type, polarity, validity, subject, predicate,
                     object_value, revision_of_id, source_kind, memory_scope,
                     address_kind, context_tags_json, address_priority)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, NULL, '[]', NULL,
                        'correction', ?, 'asserted', ?, ?, ?, ?, 'human_edit',
                        ?, ?, ?, ?)
                """,
                (
                    new_id,
                    original["user_id"],
                    original["conversation_id"],
                    original["type"],
                    original["memory_key"],
                    updates.get("content", original["content"]),
                    updates.get("importance", original["importance"]),
                    updates.get("confidence", original["confidence"]),
                    created_at,
                    "neutral" if content_changed else original["polarity"],
                    None if content_changed else original["subject"],
                    None if content_changed else original["predicate"],
                    None if content_changed else original["object_value"],
                    memory_id,
                    original["memory_scope"],
                    None if content_changed else original["address_kind"],
                    "[]" if content_changed else original["context_tags_json"],
                    0 if content_changed else original["address_priority"],
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM memories WHERE id = ?",
                (new_id,),
            ).fetchone()
        return self._memory_from_row(row)

    def close_unresolved(
        self,
        *,
        user_id: str,
        conversation_id: str,
        memory_key: str,
        source_message_id: str,
        content: str,
    ) -> list[MemoryItem]:
        with db.connect(self.db_path) as conn:
            source = conn.execute(
                "SELECT role, conversation_id FROM messages WHERE id = ?",
                (source_message_id,),
            ).fetchone()
            if source is None or str(source["role"]) != "user":
                raise ValueError("memory source must reference an existing user message")
            rows = conn.execute(
                """
                SELECT * FROM memories
                WHERE user_id = ? AND type = 'unresolved_thread'
                  AND memory_key = ? AND status = 'active'
                """,
                (user_id, memory_key),
            ).fetchall()
            closed: list[MemoryItem] = []
            for original in rows:
                conn.execute(
                    "UPDATE memories SET status = 'superseded' WHERE id = ?",
                    (original["id"],),
                )
                closed_id = str(uuid4())
                conn.execute(
                    """
                    INSERT INTO memories
                        (id, user_id, conversation_id, type, memory_key, content,
                         importance, confidence, status, created_at,
                         last_accessed_at, source_message_ids_json, embedding_ref,
                         assertion_type, polarity, validity, subject, predicate,
                         object_value, revision_of_id, source_kind)
                    VALUES (?, ?, ?, 'unresolved_thread', ?, ?, ?, ?, 'closed', ?,
                            NULL, ?, NULL, 'correction', 'negative', 'asserted',
                            'user', 'has_upcoming_event', ?, ?, 'user_message')
                    """,
                    (
                        closed_id, user_id, conversation_id, memory_key, content,
                        original["importance"], original["confidence"],
                        utc_now().isoformat(), json.dumps([source_message_id]),
                        original["object_value"], original["id"],
                    ),
                )
                closed.append(self._memory_from_row(conn.execute(
                    "SELECT * FROM memories WHERE id = ?", (closed_id,)
                ).fetchone()))
            conn.commit()
        return closed

    def retire_address(
        self,
        *,
        user_id: str,
        conversation_id: str,
        object_value: str,
        source_message_id: str,
        content: str,
    ) -> list[MemoryItem]:
        with db.connect(self.db_path) as conn:
            source = conn.execute(
                "SELECT role, conversation_id FROM messages WHERE id = ?",
                (source_message_id,),
            ).fetchone()
            if source is None or str(source["role"]) != "user":
                raise ValueError("memory source must reference an existing user message")
            if str(source["conversation_id"]) != conversation_id:
                raise ValueError("memory source belongs to another conversation")
            rows = conn.execute(
                """
                SELECT * FROM memories
                WHERE user_id = ? AND predicate = 'preferred_address'
                  AND object_value = ? AND status = 'active'
                """,
                (user_id, object_value),
            ).fetchall()
            retired: list[MemoryItem] = []
            for original in rows:
                conn.execute(
                    "UPDATE memories SET status = 'superseded' WHERE id = ?",
                    (original["id"],),
                )
                retired_id = str(uuid4())
                conn.execute(
                    """
                    INSERT INTO memories
                        (id, user_id, conversation_id, type, memory_key, content,
                         importance, confidence, status, created_at,
                         last_accessed_at, source_message_ids_json, embedding_ref,
                         assertion_type, polarity, validity, subject, predicate,
                         object_value, revision_of_id, source_kind, memory_scope,
                         address_kind, context_tags_json, address_priority)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'closed', ?, NULL, ?, NULL,
                            'correction', 'negative', 'asserted', 'user',
                            'preferred_address', ?, ?, 'user_message', ?, ?, ?, ?)
                    """,
                    (
                        retired_id, user_id, conversation_id, original["type"],
                        original["memory_key"], content, original["importance"],
                        original["confidence"], utc_now().isoformat(),
                        json.dumps([source_message_id]), object_value, original["id"],
                        original["memory_scope"], original["address_kind"],
                        original["context_tags_json"], original["address_priority"],
                    ),
                )
                retired.append(self._memory_from_row(conn.execute(
                    "SELECT * FROM memories WHERE id = ?", (retired_id,)
                ).fetchone()))
            conn.commit()
        return retired

    def delete_memory(self, memory_id: str) -> None:
        with db.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                WITH RECURSIVE lineage(id) AS (
                    SELECT id FROM memories WHERE id = ?
                    UNION ALL
                    SELECT memories.id FROM memories
                    JOIN lineage ON memories.revision_of_id = lineage.id
                )
                UPDATE memories SET status = 'deleted'
                WHERE id IN (SELECT id FROM lineage) AND status = 'active'
                """,
                (memory_id,),
            )
            conn.commit()
            exists = conn.execute(
                "SELECT 1 FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        if exists is None:
            raise KeyError(memory_id)

    def mark_accessed(self, memory_ids: list[str]) -> None:
        if not memory_ids:
            return
        placeholders = ",".join("?" for _ in memory_ids)
        with db.connect(self.db_path) as conn:
            conn.execute(
                f"""
                UPDATE memories SET last_accessed_at = ?
                WHERE id IN ({placeholders})
                """,
                [utc_now().isoformat(), *memory_ids],
            )
            conn.commit()

    def get_summary(self, conversation_id: str) -> ConversationSummary | None:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM conversation_summaries WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        if row is None:
            return None
        return ConversationSummary(
            conversation_id=str(row["conversation_id"]),
            content=str(row["content"]),
            through_message_index=int(row["through_message_index"]),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    def save_summary(self, summary: ConversationSummary) -> None:
        with db.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO conversation_summaries
                    (conversation_id, content, through_message_index, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    content = excluded.content,
                    through_message_index = excluded.through_message_index,
                    updated_at = excluded.updated_at
                """,
                (
                    summary.conversation_id,
                    summary.content,
                    summary.through_message_index,
                    summary.updated_at.isoformat(),
                ),
            )
            conn.commit()

    def memory_annotations_by_source(self, conversation_id: str) -> dict[str, set[str]]:
        annotations: dict[str, set[str]] = {}
        with db.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT status, source_message_ids_json FROM memories
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchall()
        labels = {
            "active": "current_assertion",
            "superseded": "superseded_assertion",
            "deleted": "deleted_from_long_term_memory",
            "closed": "closed_event",
        }
        for row in rows:
            label = labels.get(str(row["status"]), str(row["status"]))
            for source_id in json.loads(str(row["source_message_ids_json"])):
                annotations.setdefault(str(source_id), set()).add(label)
        return annotations

    def get_relationship(self, user_id: str) -> RelationshipState:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM relationship_states WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return (
            RelationshipState()
            if row is None
            else RelationshipState(
                familiarity=float(row["familiarity"]),
                warmth=float(row["warmth"]),
                trust=float(row["trust"]),
                teasing_permission=float(row["teasing_permission"]),
                shared_context_density=float(row["shared_context_density"]),
                recent_tension=float(row["recent_tension"]),
            )
        )

    def save_relationship(
        self,
        user_id: str,
        state: RelationshipState,
    ) -> None:
        values = state.model_dump()
        with db.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO relationship_states
                    (user_id, familiarity, warmth, trust, teasing_permission,
                     shared_context_density, recent_tension, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    familiarity = excluded.familiarity,
                    warmth = excluded.warmth,
                    trust = excluded.trust,
                    teasing_permission = excluded.teasing_permission,
                    shared_context_density = excluded.shared_context_density,
                    recent_tension = excluded.recent_tension,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    values["familiarity"],
                    values["warmth"],
                    values["trust"],
                    values["teasing_permission"],
                    values["shared_context_density"],
                    values["recent_tension"],
                    utc_now().isoformat(),
                ),
            )
            conn.commit()

    def get_scene(self, conversation_id: str) -> SceneState:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM scene_states WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        return (
            SceneState()
            if row is None
            else SceneState(
                current_topic=row["current_topic"],
                mood=str(row["mood"]),
                energy=float(row["energy"]),
                response_tempo=str(row["response_tempo"]),
                emotional_context=row["emotional_context"],
                unresolved_threads=json.loads(str(row["unresolved_json"])),
            )
        )

    def save_scene(self, conversation_id: str, state: SceneState) -> None:
        with db.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO scene_states
                    (conversation_id, current_topic, mood, energy,
                     response_tempo, emotional_context, unresolved_json,
                     updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    current_topic = excluded.current_topic,
                    mood = excluded.mood,
                    energy = excluded.energy,
                    response_tempo = excluded.response_tempo,
                    emotional_context = excluded.emotional_context,
                    unresolved_json = excluded.unresolved_json,
                    updated_at = excluded.updated_at
                """,
                (
                    conversation_id,
                    state.current_topic,
                    state.mood,
                    state.energy,
                    state.response_tempo,
                    state.emotional_context,
                    json.dumps(state.unresolved_threads, ensure_ascii=False),
                    utc_now().isoformat(),
                ),
            )
            conn.commit()

    @staticmethod
    def _memory_from_row(row) -> MemoryItem:
        return MemoryItem(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            conversation_id=(
                str(row["conversation_id"])
                if row["conversation_id"] is not None
                else None
            ),
            type=str(row["type"]),
            memory_key=str(row["memory_key"]),
            content=str(row["content"]),
            importance=float(row["importance"]),
            confidence=float(row["confidence"]),
            status=str(row["status"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            last_accessed_at=(
                datetime.fromisoformat(str(row["last_accessed_at"]))
                if row["last_accessed_at"] is not None
                else None
            ),
            source_message_ids=json.loads(str(row["source_message_ids_json"])),
            assertion_type=str(row["assertion_type"]),
            polarity=str(row["polarity"]),
            validity=str(row["validity"]),
            subject=row["subject"],
            predicate=row["predicate"],
            object_value=row["object_value"],
            revision_of_id=row["revision_of_id"],
            source_kind=str(row["source_kind"]),
            memory_scope=str(row["memory_scope"]),
            address_kind=(
                str(row["address_kind"])
                if row["address_kind"] is not None
                else None
            ),
            context_tags=json.loads(str(row["context_tags_json"])),
            address_priority=float(row["address_priority"]),
        )

    @staticmethod
    def _validate_candidate_sources(conn, candidate: MemoryCandidate) -> None:
        placeholders = ",".join("?" for _ in candidate.source_message_ids)
        rows = conn.execute(
            f"SELECT id, role, conversation_id FROM messages WHERE id IN ({placeholders})",
            candidate.source_message_ids,
        ).fetchall()
        by_id = {str(row["id"]): row for row in rows}
        for source_id in candidate.source_message_ids:
            row = by_id.get(source_id)
            if row is None:
                raise ValueError(f"memory source message does not exist: {source_id}")
            if str(row["role"]) != "user":
                raise ValueError(f"memory source is not a user message: {source_id}")
            if candidate.conversation_id and str(row["conversation_id"]) != candidate.conversation_id:
                raise ValueError(f"memory source belongs to another conversation: {source_id}")
