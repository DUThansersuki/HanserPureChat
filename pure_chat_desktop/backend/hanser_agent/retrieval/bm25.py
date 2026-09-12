from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .. import db
from ..search import B, K1, query_tokens


@dataclass(frozen=True, slots=True)
class ChunkSearchHit:
    chunk_id: int
    document_id: int
    chunk_index: int
    filename: str
    filepath: str
    text: str
    metadata_json: str
    score: float
    matched: tuple[str, ...]


def search_chunks(
    db_path: str | Path,
    values: Iterable[str],
    *,
    top_k: int,
    userdict_path: str | Path | None = None,
) -> list[ChunkSearchHit]:
    tokens = query_tokens(values, userdict_path)
    if not tokens:
        return []
    with db.connect(db_path) as conn:
        return _search(conn, tokens, top_k)


def _search(
    conn: sqlite3.Connection,
    tokens: list[str],
    top_k: int,
) -> list[ChunkSearchHit]:
    total = int(conn.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0])
    if total == 0:
        return []
    avg_length = float(
        conn.execute("SELECT AVG(token_count) FROM document_chunks").fetchone()[0]
        or 0.0
    )
    tf_by_chunk: dict[int, dict[str, int]] = defaultdict(dict)
    matched_by_chunk: dict[int, list[str]] = defaultdict(list)
    document_frequency: dict[str, int] = {}
    for token in tokens:
        rows = conn.execute(
            "SELECT chunk_id, tf FROM chunk_tokens WHERE token = ?",
            (token,),
        ).fetchall()
        document_frequency[token] = len(rows)
        for row in rows:
            chunk_id = int(row["chunk_id"])
            tf_by_chunk[chunk_id][token] = int(row["tf"])
            matched_by_chunk[chunk_id].append(token)
    if not tf_by_chunk:
        return []

    ids = list(tf_by_chunk)
    placeholders = ",".join("?" for _ in ids)
    chunks = {
        int(row["id"]): row
        for row in conn.execute(
            f"""
            SELECT c.id, c.document_id, c.chunk_index, c.text,
                   c.token_count, c.metadata_json,
                   d.filename, d.filepath
            FROM document_chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.id IN ({placeholders})
            """,
            ids,
        )
    }
    scored: list[tuple[float, int]] = []
    for chunk_id, frequencies in tf_by_chunk.items():
        length = int(chunks[chunk_id]["token_count"])
        score = 0.0
        for token, frequency in frequencies.items():
            frequency_in_docs = document_frequency[token]
            inverse_frequency = math.log(
                1 + (total - frequency_in_docs + 0.5) / (frequency_in_docs + 0.5)
            )
            denominator = (
                frequency
                + K1 * (1 - B + B * length / avg_length)
                if avg_length > 0
                else frequency + K1
            )
            score += (
                inverse_frequency
                * frequency
                * (K1 + 1)
                / denominator
            )
        scored.append((score, chunk_id))
    scored.sort(reverse=True)

    hits: list[ChunkSearchHit] = []
    for score, chunk_id in scored[:top_k]:
        row = chunks[chunk_id]
        hits.append(
            ChunkSearchHit(
                chunk_id=chunk_id,
                document_id=int(row["document_id"]),
                chunk_index=int(row["chunk_index"]),
                filename=str(row["filename"]),
                filepath=str(row["filepath"]),
                text=str(row["text"]),
                metadata_json=str(row["metadata_json"]),
                score=round(score, 6),
                matched=tuple(matched_by_chunk[chunk_id]),
            )
        )
    return hits
