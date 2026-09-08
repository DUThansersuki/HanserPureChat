from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from .. import db
from ..config import HybridRetrievalConfig
from .bm25 import ChunkSearchHit, search_chunks
from .embedding import Embedder
from .vector_store import VectorHit, VectorStore


@dataclass(frozen=True, slots=True)
class HybridCandidate:
    chunk_id: int
    document_id: int
    chunk_index: int
    filename: str
    filepath: str
    text: str
    metadata: dict[str, object]
    retrieval_score: float
    bm25_score: float | None
    dense_score: float | None
    matched: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HybridSearchOutcome:
    candidates: list[HybridCandidate]
    degraded_reasons: tuple[str, ...] = ()


def reciprocal_rank_fusion(
    rankings: list[list[int]],
    *,
    rank_constant: int = 60,
) -> dict[int, float]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item_id in enumerate(ranking, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (
                rank_constant + rank
            )
    return scores


class HybridRetriever:
    FACT_COLLECTION = "fact_chunks"

    def __init__(
        self,
        *,
        db_path,
        userdict_path,
        config: HybridRetrievalConfig,
        embedder: Embedder,
        vector_store: VectorStore,
    ):
        self.db_path = db_path
        self.userdict_path = userdict_path
        self.config = config
        self.embedder = embedder
        self.vector_store = vector_store

    async def search(
        self,
        query: str,
        keywords: list[str],
        *,
        mode: str | None = None,
        top_k: int | None = None,
    ) -> list[HybridCandidate]:
        return (
            await self.search_with_status(query, keywords, mode=mode, top_k=top_k)
        ).candidates

    async def search_with_status(
        self,
        query: str,
        keywords: list[str],
        *,
        mode: str | None = None,
        top_k: int | None = None,
    ) -> HybridSearchOutcome:
        active_mode = mode or self.config.mode
        bm25_hits = (
            search_chunks(
                self.db_path,
                [query, *keywords],
                top_k=self.config.bm25_top_k,
                userdict_path=self.userdict_path,
            )
            if active_mode in {"bm25", "hybrid"}
            else []
        )
        degraded: list[str] = []
        dense_hits: list[VectorHit] = []
        score_mode = active_mode
        if active_mode in {"dense", "hybrid"}:
            try:
                dense_hits = await self._dense_search(query)
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "dense retrieval failed; falling back to BM25",
                    extra={"error_type": type(exc).__name__},
                )
                degraded.append("dense_unavailable_bm25_fallback")
                if not bm25_hits:
                    bm25_hits = search_chunks(
                        self.db_path,
                        [query, *keywords],
                        top_k=self.config.bm25_top_k,
                        userdict_path=self.userdict_path,
                    )
                score_mode = "bm25"
        scores = self._scores(score_mode, bm25_hits, dense_hits)
        selected_ids = [
            chunk_id
            for chunk_id, _ in sorted(
                scores.items(),
                key=lambda item: item[1],
                reverse=True,
            )[: top_k or self.config.candidate_pool_size]
        ]
        return HybridSearchOutcome(
            candidates=self._load_candidates(
                selected_ids,
                scores,
                bm25_hits,
                dense_hits,
            ),
            degraded_reasons=tuple(degraded),
        )

    async def _dense_search(self, query: str) -> list[VectorHit]:
        vector = (await self.embedder.embed_queries([query]))[0]
        return self.vector_store.search(
            self.FACT_COLLECTION,
            self.embedder.model_name,
            vector,
            top_k=self.config.dense_top_k,
        )

    def _scores(
        self,
        mode: str,
        bm25_hits: list[ChunkSearchHit],
        dense_hits: list[VectorHit],
    ) -> dict[int, float]:
        if mode == "bm25":
            return {item.chunk_id: item.score for item in bm25_hits}
        if mode == "dense":
            return {int(item.item_id): item.score for item in dense_hits}
        return reciprocal_rank_fusion(
            [
                [item.chunk_id for item in bm25_hits],
                [int(item.item_id) for item in dense_hits],
            ],
            rank_constant=self.config.fusion_k,
        )

    def _load_candidates(
        self,
        selected_ids: list[int],
        scores: dict[int, float],
        bm25_hits: list[ChunkSearchHit],
        dense_hits: list[VectorHit],
    ) -> list[HybridCandidate]:
        if not selected_ids:
            return []
        placeholders = ",".join("?" for _ in selected_ids)
        with db.connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT c.id, c.document_id, c.chunk_index, c.text,
                       c.metadata_json, d.filename, d.filepath
                FROM document_chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE c.id IN ({placeholders})
                """,
                selected_ids,
            ).fetchall()
        by_id = {int(row["id"]): row for row in rows}
        bm25_by_id = {item.chunk_id: item for item in bm25_hits}
        dense_by_id = {int(item.item_id): item for item in dense_hits}
        return [
            HybridCandidate(
                chunk_id=chunk_id,
                document_id=int(by_id[chunk_id]["document_id"]),
                chunk_index=int(by_id[chunk_id]["chunk_index"]),
                filename=str(by_id[chunk_id]["filename"]),
                filepath=str(by_id[chunk_id]["filepath"]),
                text=str(by_id[chunk_id]["text"]),
                metadata=json.loads(str(by_id[chunk_id]["metadata_json"])),
                retrieval_score=scores[chunk_id],
                bm25_score=(
                    bm25_by_id[chunk_id].score
                    if chunk_id in bm25_by_id
                    else None
                ),
                dense_score=(
                    dense_by_id[chunk_id].score
                    if chunk_id in dense_by_id
                    else None
                ),
                matched=(
                    bm25_by_id[chunk_id].matched
                    if chunk_id in bm25_by_id
                    else ()
                ),
            )
            for chunk_id in selected_ids
            if chunk_id in by_id
        ]
