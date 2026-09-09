from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_DOCUMENTS = """
CREATE TABLE IF NOT EXISTS documents (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    filename   TEXT    NOT NULL,
    filepath   TEXT    NOT NULL UNIQUE,
    content    TEXT    NOT NULL,
    mtime      REAL    NOT NULL,
    size       INTEGER NOT NULL,
    indexed_at TEXT    NOT NULL
)
"""

SCHEMA_TOKENS = """
CREATE TABLE IF NOT EXISTS doc_tokens (
    doc_id INTEGER NOT NULL,
    token  TEXT    NOT NULL,
    tf     INTEGER NOT NULL,
    PRIMARY KEY (doc_id, token)
)
"""

SCHEMA_DOCUMENT_CHUNKS = """
CREATE TABLE IF NOT EXISTS document_chunks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id   INTEGER NOT NULL,
    chunk_index   INTEGER NOT NULL,
    text          TEXT    NOT NULL,
    start_char    INTEGER NOT NULL,
    end_char      INTEGER NOT NULL,
    token_count   INTEGER NOT NULL,
    metadata_json TEXT    NOT NULL,
    embedding_ref TEXT,
    UNIQUE (document_id, chunk_index)
)
"""

SCHEMA_CHUNK_TOKENS = """
CREATE TABLE IF NOT EXISTS chunk_tokens (
    chunk_id INTEGER NOT NULL,
    token    TEXT    NOT NULL,
    tf       INTEGER NOT NULL,
    PRIMARY KEY (chunk_id, token)
)
"""

SCHEMA_VECTOR_EMBEDDINGS = """
CREATE TABLE IF NOT EXISTS vector_embeddings (
    collection TEXT NOT NULL,
    item_id    TEXT NOT NULL,
    model      TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    vector     BLOB NOT NULL,
    PRIMARY KEY (collection, item_id)
)
"""

SCHEMA_VECTOR_EMBEDDINGS_V2 = """
CREATE TABLE IF NOT EXISTS vector_embeddings_v2 (
    collection TEXT NOT NULL,
    generation TEXT NOT NULL,
    item_id    TEXT NOT NULL,
    model      TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    vector     BLOB NOT NULL,
    PRIMARY KEY (collection, generation, item_id)
)
"""

SCHEMA_INDEX_GENERATIONS = """
CREATE TABLE IF NOT EXISTS index_generations (
    collection     TEXT NOT NULL,
    generation     TEXT NOT NULL,
    status         TEXT NOT NULL,
    model          TEXT NOT NULL,
    dimensions     INTEGER NOT NULL,
    source_revision TEXT NOT NULL,
    config_hash    TEXT NOT NULL,
    item_count     INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    published_at   TEXT,
    PRIMARY KEY (collection, generation)
)
"""

SCHEMA_ACTIVE_INDEX_GENERATIONS = """
CREATE TABLE IF NOT EXISTS active_index_generations (
    collection TEXT PRIMARY KEY,
    generation TEXT NOT NULL,
    revision   INTEGER NOT NULL,
    updated_at TEXT NOT NULL
)
"""

SCHEMA_STYLE_EXAMPLES = """
CREATE TABLE IF NOT EXISTS style_examples (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt             TEXT NOT NULL,
    response           TEXT NOT NULL,
    context_before     TEXT NOT NULL,
    context_after      TEXT NOT NULL,
    scene              TEXT NOT NULL,
    speech_act         TEXT NOT NULL,
    tone_json          TEXT NOT NULL,
    relationship_level TEXT,
    energy             REAL,
    teasing_level      REAL,
    answer_length      TEXT NOT NULL,
    response_mode      TEXT NOT NULL,
    source_type        TEXT NOT NULL,
    source_ref         TEXT NOT NULL,
    authenticity_score REAL NOT NULL,
    quality_score      REAL NOT NULL,
    metadata_json      TEXT NOT NULL,
    embedding_ref      TEXT,
    review_status      TEXT NOT NULL DEFAULT 'pending',
    source_tier        TEXT NOT NULL DEFAULT 'unknown',
    source_document_id INTEGER,
    source_start_line  INTEGER,
    source_end_line    INTEGER,
    source_start_char  INTEGER,
    source_end_char    INTEGER,
    source_speaker     TEXT,
    source_user_turn   TEXT,
    source_response_turn TEXT,
    source_raw_text    TEXT,
    cleaning_operations_json TEXT NOT NULL DEFAULT '[]',
    reviewer_id        TEXT,
    review_notes       TEXT,
    reviewed_at        TEXT,
    index_generation   TEXT
)
"""

STYLE_REVIEW_COLUMNS = {
    "review_status": "TEXT NOT NULL DEFAULT 'pending'",
    "source_tier": "TEXT NOT NULL DEFAULT 'unknown'",
    "source_document_id": "INTEGER",
    "source_start_line": "INTEGER",
    "source_end_line": "INTEGER",
    "source_start_char": "INTEGER",
    "source_end_char": "INTEGER",
    "source_speaker": "TEXT",
    "source_user_turn": "TEXT",
    "source_response_turn": "TEXT",
    "source_raw_text": "TEXT",
    "cleaning_operations_json": "TEXT NOT NULL DEFAULT '[]'",
    "reviewer_id": "TEXT",
    "review_notes": "TEXT",
    "reviewed_at": "TEXT",
    "index_generation": "TEXT",
}

CONFIRMED_STYLE_DEFECT_IDS = (919, 1151, 1189, 1308, 1394, 1417, 1506, 1513, 1518)

SCHEMA_CONVERSATIONS = """
CREATE TABLE IF NOT EXISTS conversations (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    title           TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    persona_version TEXT NOT NULL,
    model_profile   TEXT NOT NULL
)
"""

SCHEMA_MESSAGES = """
CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    turn_index      INTEGER NOT NULL,
    model_name      TEXT,
    trace_id        TEXT,
    style_example_ids_json TEXT NOT NULL DEFAULT '[]'
)
"""

SCHEMA_CONVERSATION_SUMMARIES = """
CREATE TABLE IF NOT EXISTS conversation_summaries (
    conversation_id       TEXT PRIMARY KEY,
    content               TEXT NOT NULL,
    through_message_index INTEGER NOT NULL,
    updated_at            TEXT NOT NULL
)
"""

SCHEMA_MEMORIES = """
CREATE TABLE IF NOT EXISTS memories (
    id                      TEXT PRIMARY KEY,
    user_id                 TEXT NOT NULL,
    conversation_id         TEXT,
    type                    TEXT NOT NULL,
    memory_key              TEXT NOT NULL,
    content                 TEXT NOT NULL,
    importance              REAL NOT NULL,
    confidence              REAL NOT NULL,
    status                  TEXT NOT NULL,
    created_at              TEXT NOT NULL,
    last_accessed_at        TEXT,
    source_message_ids_json TEXT NOT NULL,
    embedding_ref           TEXT,
    assertion_type          TEXT NOT NULL DEFAULT 'user_assertion',
    polarity                TEXT NOT NULL DEFAULT 'neutral',
    validity                TEXT NOT NULL DEFAULT 'asserted',
    subject                 TEXT,
    predicate               TEXT,
    object_value            TEXT,
    revision_of_id          TEXT,
    source_kind             TEXT NOT NULL DEFAULT 'user_message',
    memory_scope            TEXT NOT NULL DEFAULT 'global',
    address_kind            TEXT,
    context_tags_json       TEXT NOT NULL DEFAULT '[]',
    address_priority        REAL NOT NULL DEFAULT 0
)
"""

SCHEMA_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""

MEMORY_ASSERTION_COLUMNS = {
    "assertion_type": "TEXT NOT NULL DEFAULT 'user_assertion'",
    "polarity": "TEXT NOT NULL DEFAULT 'neutral'",
    "validity": "TEXT NOT NULL DEFAULT 'asserted'",
    "subject": "TEXT",
    "predicate": "TEXT",
    "object_value": "TEXT",
    "revision_of_id": "TEXT",
    "source_kind": "TEXT NOT NULL DEFAULT 'user_message'",
}

MEMORY_ADDRESS_COLUMNS = {
    "memory_scope": "TEXT NOT NULL DEFAULT 'global'",
    "address_kind": "TEXT",
    "context_tags_json": "TEXT NOT NULL DEFAULT '[]'",
    "address_priority": "REAL NOT NULL DEFAULT 0",
}

SCHEMA_RELATIONSHIP_STATES = """
CREATE TABLE IF NOT EXISTS relationship_states (
    user_id                TEXT PRIMARY KEY,
    familiarity            REAL NOT NULL,
    warmth                 REAL NOT NULL,
    trust                  REAL NOT NULL,
    teasing_permission     REAL NOT NULL,
    shared_context_density REAL NOT NULL,
    recent_tension         REAL NOT NULL,
    updated_at             TEXT NOT NULL
)
"""

SCHEMA_SCENE_STATES = """
CREATE TABLE IF NOT EXISTS scene_states (
    conversation_id     TEXT PRIMARY KEY,
    current_topic       TEXT,
    mood                TEXT NOT NULL,
    energy              REAL NOT NULL,
    response_tempo      TEXT NOT NULL,
    emotional_context   TEXT,
    unresolved_json     TEXT NOT NULL,
    updated_at          TEXT NOT NULL
)
"""

SCHEMA_EVALUATION_RUNS = """
CREATE TABLE IF NOT EXISTS evaluation_runs (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    dataset     TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    summary_json TEXT NOT NULL
)
"""

SCHEMA_EVALUATION_OUTPUTS = """
CREATE TABLE IF NOT EXISTS evaluation_outputs (
    run_id            TEXT NOT NULL,
    profile           TEXT NOT NULL,
    case_id           TEXT NOT NULL,
    raw_output_text   TEXT NOT NULL,
    output_text       TEXT NOT NULL,
    latency_seconds   REAL NOT NULL,
    metrics_json      TEXT NOT NULL,
    PRIMARY KEY (run_id, profile, case_id)
)
"""

SCHEMA_REQUEST_EXECUTIONS = """
CREATE TABLE IF NOT EXISTS request_executions (
    request_id          TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    conversation_id     TEXT NOT NULL,
    request_hash        TEXT NOT NULL,
    trace_id            TEXT NOT NULL,
    status              TEXT NOT NULL,
    response_json       TEXT,
    error_code          TEXT,
    attempts            INTEGER NOT NULL DEFAULT 1,
    stage_timings_json  TEXT NOT NULL DEFAULT '{}',
    effective_request_json TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
)
"""

SCHEMA_REPLY_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS reply_snapshots (
    reply_id               TEXT PRIMARY KEY,
    request_id             TEXT NOT NULL UNIQUE,
    request_hash           TEXT NOT NULL,
    user_id                TEXT NOT NULL,
    conversation_id        TEXT NOT NULL,
    snapshot_json          TEXT NOT NULL,
    response_json          TEXT NOT NULL,
    post_turn_payload_json TEXT NOT NULL,
    post_turn_status       TEXT NOT NULL DEFAULT 'pending',
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
)
"""

SCHEMA_POST_TURN_FAILURES = """
CREATE TABLE IF NOT EXISTS post_turn_failures (
    id                 TEXT PRIMARY KEY,
    request_id         TEXT NOT NULL UNIQUE,
    trace_id           TEXT NOT NULL,
    stage              TEXT NOT NULL,
    status             TEXT NOT NULL,
    attempts           INTEGER NOT NULL DEFAULT 1,
    payload_json       TEXT NOT NULL,
    last_error_type    TEXT NOT NULL,
    last_error_message TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    completed_at       TEXT
)
"""


class ClosingConnection(sqlite3.Connection):
    """Match C# `using` semantics: commit/rollback, then close the handle."""

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(db_path),
        factory=ClosingConnection,
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(SCHEMA_MIGRATIONS)
    conn.execute(SCHEMA_DOCUMENTS)
    conn.execute(SCHEMA_TOKENS)
    conn.execute(SCHEMA_DOCUMENT_CHUNKS)
    conn.execute(SCHEMA_CHUNK_TOKENS)
    conn.execute(SCHEMA_VECTOR_EMBEDDINGS)
    conn.execute(SCHEMA_VECTOR_EMBEDDINGS_V2)
    conn.execute(SCHEMA_INDEX_GENERATIONS)
    conn.execute(SCHEMA_ACTIVE_INDEX_GENERATIONS)
    conn.execute(SCHEMA_STYLE_EXAMPLES)
    conn.execute(SCHEMA_CONVERSATIONS)
    conn.execute(SCHEMA_MESSAGES)
    conn.execute(SCHEMA_CONVERSATION_SUMMARIES)
    conn.execute(SCHEMA_MEMORIES)
    conn.execute(SCHEMA_RELATIONSHIP_STATES)
    conn.execute(SCHEMA_SCENE_STATES)
    conn.execute(SCHEMA_EVALUATION_RUNS)
    conn.execute(SCHEMA_EVALUATION_OUTPUTS)
    conn.execute(SCHEMA_REQUEST_EXECUTIONS)
    conn.execute(SCHEMA_REPLY_SNAPSHOTS)
    conn.execute(SCHEMA_POST_TURN_FAILURES)
    _apply_memory_assertion_migration(conn)
    _apply_memory_address_migration(conn)
    _apply_style_review_migration(conn)
    _apply_performance_snapshot_migration(conn)
    _apply_persona_trace_migration(conn)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunks_document "
        "ON document_chunks(document_id, chunk_index)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunk_tokens_token "
        "ON chunk_tokens(token)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vectors_collection_model "
        "ON vector_embeddings(collection, model)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vectors_v2_generation_model "
        "ON vector_embeddings_v2(collection, generation, model)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_style_quality "
        "ON style_examples(source_type, quality_score)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_style_review_eligibility "
        "ON style_examples(review_status, source_tier, source_type)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_conversation "
        "ON messages(conversation_id, turn_index)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memories_user_status "
        "ON memories(user_id, status, type)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_request_execution_status "
        "ON request_executions(status, updated_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_post_turn_failure_status "
        "ON post_turn_failures(status, updated_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_reply_snapshots_owner "
        "ON reply_snapshots(user_id, conversation_id, created_at)"
    )
    conn.commit()


def _apply_performance_snapshot_migration(conn: sqlite3.Connection) -> None:
    """Migration 6: effective request snapshots and atomic canonical replies."""

    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(request_executions)").fetchall()
    }
    if "effective_request_json" not in columns:
        conn.execute(
            "ALTER TABLE request_executions "
            "ADD COLUMN effective_request_json TEXT NOT NULL DEFAULT '{}'"
        )
    conn.execute(SCHEMA_REPLY_SNAPSHOTS)
    conn.execute(
        """
        INSERT OR IGNORE INTO schema_migrations(version, name, applied_at)
        VALUES (6, 'performance_reply_snapshots', datetime('now'))
        """
    )


def _apply_persona_trace_migration(conn: sqlite3.Connection) -> None:
    """Migration 7: persist Style IDs only for committed assistant turns."""

    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(messages)").fetchall()
    }
    if "style_example_ids_json" not in columns:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN style_example_ids_json "
            "TEXT NOT NULL DEFAULT '[]'"
        )
    conn.execute(
        """
        INSERT OR IGNORE INTO schema_migrations(version, name, applied_at)
        VALUES (7, 'persona_style_turn_trace', datetime('now'))
        """
    )


def apply_slice4_failure_state_migration(conn: sqlite3.Connection) -> None:
    """Migration 5: request idempotency and retryable post-turn state."""
    init_db(conn)
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version=5").fetchone():
        return
    conn.execute(
        """
        INSERT INTO schema_migrations(version, name, applied_at)
        VALUES (5, 'request_and_model_failure_boundaries', datetime('now'))
        """
    )
    conn.commit()


def apply_slice5_ownership_index_migration(conn: sqlite3.Connection) -> None:
    """Migration 4: immutable conversation ownership and atomic vector generations.

    The caller must run the preview first. Ambiguous owner/vector rows are rejected
    instead of being repaired heuristically. The legacy vector table is retained.
    """
    init_db(conn)
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version=4").fetchone():
        return
    missing_owners = conn.execute(
        "SELECT id FROM conversations WHERE user_id IS NULL OR trim(user_id)=''"
    ).fetchall()
    if missing_owners:
        raise ValueError("Slice 5 migration blocked: conversations with missing owner")
    conflicts = conn.execute(
        """
        SELECT collection
        FROM vector_embeddings
        GROUP BY collection
        HAVING COUNT(DISTINCT model || ':' || dimensions) > 1
        """
    ).fetchall()
    if conflicts:
        raise ValueError("Slice 5 migration blocked: ambiguous vector layouts")

    groups = conn.execute(
        """
        SELECT collection, model, dimensions, COUNT(*) AS item_count
        FROM vector_embeddings
        GROUP BY collection, model, dimensions
        """
    ).fetchall()
    for row in groups:
        collection = str(row["collection"])
        generation = "legacy-v1"
        conn.execute(
            """
            INSERT INTO index_generations
                (collection, generation, status, model, dimensions,
                 source_revision, config_hash, item_count, created_at, published_at)
            VALUES (?, ?, 'active', ?, ?, ?, 'legacy', ?, datetime('now'), datetime('now'))
            """,
            (
                collection,
                generation,
                str(row["model"]),
                int(row["dimensions"]),
                f"legacy:{int(row['item_count'])}",
                int(row["item_count"]),
            ),
        )
        conn.execute(
            """
            INSERT INTO vector_embeddings_v2
                (collection, generation, item_id, model, dimensions, vector)
            SELECT collection, ?, item_id, model, dimensions, vector
            FROM vector_embeddings WHERE collection=?
            """,
            (generation, collection),
        )
        conn.execute(
            """
            INSERT INTO active_index_generations
                (collection, generation, revision, updated_at)
            VALUES (?, ?, 1, datetime('now'))
            """,
            (collection, generation),
        )
    conn.execute(
        """
        INSERT INTO schema_migrations(version, name, applied_at)
        VALUES (4, 'conversation_ownership_and_index_generations', datetime('now'))
        """
    )
    conn.commit()


def _apply_memory_assertion_migration(conn: sqlite3.Connection) -> None:
    """Migration 1: assertion semantics and revision provenance for memory."""
    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(memories)").fetchall()
    }
    for name, definition in MEMORY_ASSERTION_COLUMNS.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE memories ADD COLUMN {name} {definition}")
    applied = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version = 1"
    ).fetchone()
    if applied:
        return

    conn.execute(
        "UPDATE memories SET source_kind = 'legacy_import'"
    )
    conn.execute(
        """
        UPDATE memories
        SET polarity = 'negative', subject = 'user', predicate = 'likes',
            object_value = substr(memory_key, length('preference:dislike:') + 1),
            memory_key = 'preference:like:' ||
                substr(memory_key, length('preference:dislike:') + 1)
        WHERE type = 'user_preference'
          AND memory_key LIKE 'preference:dislike:%'
        """
    )
    conn.execute(
        """
        UPDATE memories
        SET polarity = 'positive', subject = 'user', predicate = 'likes',
            object_value = COALESCE(
                object_value,
                substr(memory_key, length('preference:like:') + 1)
            )
        WHERE type = 'user_preference'
          AND memory_key LIKE 'preference:like:%'
          AND polarity = 'neutral'
        """
    )
    conn.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY user_id, type, memory_key
                       ORDER BY created_at DESC, id DESC
                   ) AS revision_rank
            FROM memories WHERE status = 'active'
        )
        UPDATE memories SET status = 'superseded'
        WHERE id IN (SELECT id FROM ranked WHERE revision_rank > 1)
        """
    )
    # A legacy shared-event row proves only that a user said the sentence.
    conn.execute(
        """
        UPDATE memories
        SET validity = 'unverified', source_kind = 'legacy_import'
        WHERE type IN ('shared_event', 'relationship_event', 'episode')
          AND revision_of_id IS NULL
        """
    )
    conn.execute(
        """
        UPDATE memories
        SET validity = 'unverified'
        WHERE json_array_length(source_message_ids_json) = 0
           OR EXISTS (
                SELECT 1 FROM json_each(memories.source_message_ids_json) source
                LEFT JOIN messages ON messages.id = source.value
                WHERE messages.id IS NULL OR messages.role != 'user'
           )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO schema_migrations(version, name, applied_at)
        VALUES (1, 'memory_assertion_semantics', datetime('now'))
        """
    )


def _apply_style_review_migration(conn: sqlite3.Connection) -> None:
    """Migration 2: review eligibility and replayable source spans for style."""
    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(style_examples)").fetchall()
    }
    for name, definition in STYLE_REVIEW_COLUMNS.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE style_examples ADD COLUMN {name} {definition}")
    applied = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version = 2"
    ).fetchone()
    if applied:
        return
    placeholders = ",".join("?" for _ in CONFIRMED_STYLE_DEFECT_IDS)
    conn.execute(
        f"""
        UPDATE style_examples
        SET review_status = 'quarantined',
            review_notes = 'confirmed structural mismatch from F02 audit'
        WHERE id IN ({placeholders})
        """,
        CONFIRMED_STYLE_DEFECT_IDS,
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO schema_migrations(version, name, applied_at)
        VALUES (2, 'style_review_provenance', datetime('now'))
        """
    )


def _apply_memory_address_migration(conn: sqlite3.Connection) -> None:
    """Migration 3: typed, concurrently active address options."""
    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(memories)").fetchall()
    }
    for name, definition in MEMORY_ADDRESS_COLUMNS.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE memories ADD COLUMN {name} {definition}")
    applied = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version = 3"
    ).fetchone()
    if applied:
        return
    conn.execute(
        """
        UPDATE memories
        SET address_kind='personal_name', address_priority=1.0,
            memory_scope='global', context_tags_json='["personal"]',
            predicate='preferred_address',
            memory_key='address:personal_name:' || lower(object_value)
        WHERE memory_key='user:name' AND predicate='preferred_name'
          AND object_value IS NOT NULL
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO schema_migrations(version, name, applied_at)
        VALUES (3, 'memory_address_options', datetime('now'))
        """
    )


def get_document_content(conn: sqlite3.Connection, doc_id: int) -> str:
    row = conn.execute("SELECT content FROM documents WHERE id = ?", (doc_id,)).fetchone()
    return str(row["content"]) if row else ""


def get_document_content_by_filename(conn: sqlite3.Connection, filename: str) -> str:
    row = conn.execute("SELECT content FROM documents WHERE filename = ?", (filename,)).fetchone()
    return str(row["content"]) if row else ""
