from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RerankCandidate:
    document_id: int
    filename: str
    text: str
    retrieval_score: float
    chunk_id: int | None = None


@dataclass(frozen=True, slots=True)
class RerankHit:
    document_id: int
    filename: str
    score: float
    chunk_id: int | None = None


class Reranker(Protocol):
    name: str

    async def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        *,
        top_k: int,
    ) -> list[RerankHit]:
        ...


class BM25Reranker:
    """Keep the SQLite BM25 order without loading another model."""

    name = "bm25"

    async def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        *,
        top_k: int,
    ) -> list[RerankHit]:
        del query
        ranked = sorted(
            candidates,
            key=lambda item: item.retrieval_score,
            reverse=True,
        )
        return [
            RerankHit(
                document_id=item.document_id,
                filename=item.filename,
                score=item.retrieval_score,
                chunk_id=item.chunk_id,
            )
            for item in ranked[:top_k]
        ]
