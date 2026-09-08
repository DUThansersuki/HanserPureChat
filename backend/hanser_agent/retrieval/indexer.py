from __future__ import annotations

import json
import hashlib
import re
from collections import Counter
from pathlib import Path

from .. import db
from ..config import HybridRetrievalConfig
from ..tokenizer import tokenize
from .chunker import chunk_document
from .embedding import Embedder
from .hybrid import HybridRetriever
from .vector_store import VectorStore


_DATE = re.compile(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日")
_TITLE = re.compile(r"(?:Title|标题)[：:]\s*([^\n]{1,100})", re.IGNORECASE)


def rebuild_document_chunks(
    *,
    db_path: str | Path,
    userdict_path: str | Path,
    config: HybridRetrievalConfig,
) -> int:
    with db.connect(db_path) as conn:
        db.init_db(conn)
        documents = conn.execute(
            "SELECT id, filename, filepath, content FROM documents ORDER BY id"
        ).fetchall()
        conn.execute("DELETE FROM chunk_tokens")
        conn.execute("DELETE FROM document_chunks")
        for document in documents:
            filename = str(document["filename"])
            content = str(document["content"])
            metadata = _document_metadata(filename, content)
            for chunk in chunk_document(
                content,
                target_chars=config.chunk_target_chars,
                max_chars=config.chunk_max_chars,
                overlap_chars=config.chunk_overlap_chars,
            ):
                frequencies = Counter(tokenize(chunk.text, userdict_path))
                cursor = conn.execute(
                    """
                    INSERT INTO document_chunks
                        (document_id, chunk_index, text, start_char, end_char,
                         token_count, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(document["id"]),
                        chunk.chunk_index,
                        chunk.text,
                        chunk.start_char,
                        chunk.end_char,
                        sum(frequencies.values()),
                        json.dumps(metadata, ensure_ascii=False),
                    ),
                )
                chunk_id = int(cursor.lastrowid)
                conn.executemany(
                    "INSERT INTO chunk_tokens (chunk_id, token, tf) VALUES (?, ?, ?)",
                    [
                        (chunk_id, token, frequency)
                        for token, frequency in frequencies.items()
                    ],
                )
        conn.commit()
        return int(
            conn.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0]
        )


def delete_document_chunks(*, db_path: str | Path, document_id: int) -> int:
    """Delete lexical rows in dependency order inside one transaction."""
    with db.connect(db_path) as conn:
        db.init_db(conn)
        conn.execute("BEGIN IMMEDIATE")
        chunk_ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM document_chunks WHERE document_id=?", (document_id,)
            ).fetchall()
        ]
        if chunk_ids:
            placeholders = ",".join("?" for _ in chunk_ids)
            conn.execute(
                f"DELETE FROM chunk_tokens WHERE chunk_id IN ({placeholders})",
                chunk_ids,
            )
        conn.execute("DELETE FROM document_chunks WHERE document_id=?", (document_id,))
        conn.commit()
        return len(chunk_ids)


async def rebuild_fact_embeddings(
    *,
    db_path: str | Path,
    embedder: Embedder,
    vector_store: VectorStore,
    batch_size: int,
) -> int:
    with db.connect(db_path) as conn:
        chunks = conn.execute(
            """
            SELECT c.id, c.text, d.filename
            FROM document_chunks c
            JOIN documents d ON d.id = c.document_id
            ORDER BY c.id
            """
        ).fetchall()
    if not chunks:
        return 0
    source_revision = hashlib.sha256(
        "\n".join(f"{row['id']}:{row['filename']}:{row['text']}" for row in chunks).encode("utf-8")
    ).hexdigest()
    generation: str | None = None
    try:
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            vectors = await embedder.embed_documents(
                [f"{row['filename']}\n{row['text']}" for row in batch]
            )
            if generation is None:
                generation = vector_store.begin_generation(
                    HybridRetriever.FACT_COLLECTION,
                    embedder.model_name,
                    len(vectors[0]),
                    source_revision=source_revision,
                    config_hash=hashlib.sha256(f"batch_size={batch_size}".encode()).hexdigest(),
                )
            vector_store.stage_upsert(
                HybridRetriever.FACT_COLLECTION, generation, embedder.model_name,
                [(str(row["id"]), vector) for row, vector in zip(batch, vectors, strict=True)],
            )
        assert generation is not None
        vector_store.publish_generation(
            HybridRetriever.FACT_COLLECTION, generation, expected_count=len(chunks)
        )
    except Exception:
        if generation is not None:
            vector_store.fail_generation(HybridRetriever.FACT_COLLECTION, generation)
        raise

    with db.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE document_chunks
            SET embedding_ref = 'fact_chunks:' || id
            """
        )
        conn.commit()
    return len(chunks)


def _document_metadata(filename: str, content: str) -> dict[str, object]:
    date_match = _DATE.search(filename) or _DATE.search(content[:100])
    date = (
        f"{int(date_match.group(1)):04d}-{int(date_match.group(2)):02d}-"
        f"{int(date_match.group(3)):02d}"
        if date_match
        else None
    )
    title_match = _TITLE.search(content[:500])
    suffix = Path(filename).suffix.lower()
    source_type = (
        "wiki"
        if suffix == ".md"
        else "transcript"
        if suffix == ".docx"
        else "document"
    )
    return {
        "date": date,
        "section": title_match.group(1).strip() if title_match else None,
        "speaker": None,
        "source_type": source_type,
    }
