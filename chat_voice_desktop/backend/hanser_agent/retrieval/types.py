from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HybridCandidate:
    """Compatibility name for one lightweight BM25 candidate."""

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
    """Compatibility response shape retained for the agent tool contract."""

    candidates: list[HybridCandidate]
    degraded_reasons: tuple[str, ...] = ()
