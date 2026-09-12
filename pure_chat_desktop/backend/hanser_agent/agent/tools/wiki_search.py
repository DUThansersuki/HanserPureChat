from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from ...config import Settings
from ...models import SearchResult, WikiEvidence
from ...retrieval import (
    HybridCandidate,
    HybridRetriever,
    RerankCandidate,
    RerankHit,
    Reranker,
    BM25Reranker,
)


class WikiSearchResult(BaseModel):
    query: str
    keywords: list[str] = Field(default_factory=list)
    candidates: list[SearchResult] = Field(default_factory=list)
    anchored: list[str] = Field(default_factory=list)
    evidence: list[WikiEvidence] = Field(default_factory=list)
    rerank_scores: dict[str, float] = Field(default_factory=dict)
    status: str = "ok"
    degraded_reasons: list[str] = Field(default_factory=list)
    ranking_profile: str = "reranker"


class WikiSearchTool:
    """Hybrid chunk retrieval followed by the local cross-encoder reranker."""

    name = "wiki_search"
    description = (
        "搜索 Hanser Wiki，适用于查询 Hanser 的经历、"
        "作品、直播事件、人物关系、时间等事实信息。"
    )

    def __init__(
        self,
        settings: Settings,
        reranker: Reranker,
        retriever: HybridRetriever,
    ):
        self.settings = settings
        self.reranker = reranker
        self.retriever = retriever

    async def search(
        self,
        query: str,
        keywords: list[str],
    ) -> WikiSearchResult:
        logger = logging.getLogger(__name__)
        degraded_reasons: list[str] = []
        if hasattr(self.retriever, "search_with_status"):
            retrieval = await self.retriever.search_with_status(
                query, keywords, top_k=self.settings.retrieval.candidate_pool_size
            )
            candidates = retrieval.candidates
            degraded_reasons.extend(retrieval.degraded_reasons)
        else:
            candidates = await self.retriever.search(
                query, keywords, top_k=self.settings.retrieval.candidate_pool_size
            )
        ranking_profile = getattr(
            self.reranker, "name", type(self.reranker).__name__
        )
        try:
            reranked = await self.reranker.rerank(
                query,
                self.prepare_rerank_candidates(candidates),
                top_k=self.settings.retrieval.top_k,
            )
        except Exception as exc:
            logger.warning(
                "reranker failed; returning recalled candidates",
                extra={"error_type": type(exc).__name__},
            )
            degraded_reasons.append("reranker_unavailable_retrieval_fallback")
            ranking_profile = "retrieval_score_fallback"
            reranked = await BM25Reranker().rerank(
                query,
                self.prepare_rerank_candidates(candidates),
                top_k=self.settings.retrieval.top_k,
            )
        evidence = self._load_evidence(reranked, candidates)
        anchored = list(dict.fromkeys(item.filename for item in evidence))
        logger.info(
            "wiki hybrid search completed",
            extra={
                "query": query,
                "candidate_count": len(candidates),
                "anchored": anchored,
                "rerank_scores": {
                    item.source_id: item.rerank_score
                    for item in evidence
                },
            },
        )
        result = WikiSearchResult(
            query=query,
            keywords=keywords,
            candidates=self._document_results(candidates),
            anchored=anchored,
            evidence=evidence,
            rerank_scores={
                item.source_id: float(item.rerank_score or 0.0)
                for item in evidence
            },
            status="degraded" if degraded_reasons else "ok",
            degraded_reasons=degraded_reasons,
            ranking_profile=ranking_profile,
        )
        if self.settings.reranker.release_after_request:
            try:
                await self.reranker.unload()
            except Exception as exc:
                logger.warning(
                    "reranker release failed",
                    extra={"error_type": type(exc).__name__},
                )
                result.degraded_reasons.append("reranker_release_failed")
                result.status = "degraded"
        return result

    @staticmethod
    def prepare_rerank_candidates(
        candidates: list[HybridCandidate],
    ) -> list[RerankCandidate]:
        return [
            RerankCandidate(
                document_id=item.document_id,
                filename=item.filename,
                text=f"文件名：{item.filename}\n{item.text}",
                retrieval_score=item.retrieval_score,
                chunk_id=item.chunk_id,
            )
            for item in candidates
        ]

    @staticmethod
    def _load_evidence(
        reranked: list[RerankHit],
        candidates: list[HybridCandidate],
    ) -> list[WikiEvidence]:
        by_chunk = {item.chunk_id: item for item in candidates}
        evidence: list[WikiEvidence] = []
        for hit in reranked:
            if hit.chunk_id is None or hit.chunk_id not in by_chunk:
                continue
            item = by_chunk[hit.chunk_id]
            source_type = str(item.metadata.get("source_type") or "document")
            evidence.append(
                WikiEvidence(
                    source_id=(
                        f"document:{item.document_id}:chunk:{item.chunk_id}"
                    ),
                    document_id=item.document_id,
                    chunk_id=item.chunk_id,
                    chunk_index=item.chunk_index,
                    filename=item.filename,
                    text=item.text,
                    retrieval_score=item.retrieval_score,
                    rerank_score=hit.score,
                    source_type=source_type,
                    source_date=(
                        str(item.metadata["date"])
                        if item.metadata.get("date")
                        else None
                    ),
                )
            )
        return evidence

    @staticmethod
    def _document_results(
        candidates: list[HybridCandidate],
    ) -> list[SearchResult]:
        results: list[SearchResult] = []
        seen: set[int] = set()
        for item in candidates:
            if item.document_id in seen:
                continue
            seen.add(item.document_id)
            results.append(
                SearchResult(
                    id=item.document_id,
                    filename=item.filename,
                    filepath=item.filepath,
                    hits=len(item.matched),
                    matched=list(item.matched),
                    snippet=item.text[:240].replace("\n", " "),
                    score=round(item.retrieval_score, 6),
                )
            )
        return results
