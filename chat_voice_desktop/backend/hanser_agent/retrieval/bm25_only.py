from __future__ import annotations

import json

from .. import db
from ..config import HybridRetrievalConfig
from .bm25 import search_chunks
from .types import HybridCandidate, HybridSearchOutcome


class BM25OnlyRetriever:
    """Lightweight Wiki retrieval for the desktop runtime."""

    def __init__(self, *, db_path, userdict_path, config: HybridRetrievalConfig):
        self.db_path = db_path
        self.userdict_path = userdict_path
        self.config = config

    async def search(self, query: str, keywords: list[str], *, top_k: int | None = None):
        return (await self.search_with_status(query, keywords, top_k=top_k)).candidates

    async def search_with_status(
        self,
        query: str,
        keywords: list[str],
        *,
        mode: str | None = None,
        top_k: int | None = None,
    ) -> HybridSearchOutcome:
        del mode
        hits = search_chunks(
            self.db_path,
            [query, *keywords],
            top_k=top_k or self.config.candidate_pool_size,
            userdict_path=self.userdict_path,
        )
        if not hits:
            return HybridSearchOutcome(candidates=[])
        ids = [item.chunk_id for item in hits]
        placeholders = ",".join("?" for _ in ids)
        with db.connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT c.id, c.document_id, c.chunk_index, c.text,
                       c.metadata_json, d.filename, d.filepath
                FROM document_chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE c.id IN ({placeholders})
                """,
                ids,
            ).fetchall()
        by_id = {int(row["id"]): row for row in rows}
        return HybridSearchOutcome(
            candidates=[
                HybridCandidate(
                    chunk_id=item.chunk_id,
                    document_id=item.document_id,
                    chunk_index=item.chunk_index,
                    filename=item.filename,
                    filepath=item.filepath,
                    text=item.text,
                    metadata=json.loads(item.metadata_json),
                    retrieval_score=item.score,
                    bm25_score=item.score,
                    dense_score=None,
                    matched=item.matched,
                )
                for item in hits
                if item.chunk_id in by_id
            ]
        )
