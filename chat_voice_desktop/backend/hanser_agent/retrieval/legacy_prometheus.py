from __future__ import annotations

from ..config import LLMTaskConfig
from ..llm import LLMClient
from ..models import ChatMessage
from .reranker import RerankCandidate, RerankHit


class LegacyPrometheusReranker:
    """Offline-only adapter for Phase 2 A/B evaluation."""

    name = "legacy_prometheus"

    def __init__(
        self,
        *,
        llm: LLMClient,
        model_profile: LLMTaskConfig,
        prompt: str,
    ):
        self.llm = llm
        self.model_profile = model_profile
        self.prompt = prompt

    async def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        *,
        top_k: int,
    ) -> list[RerankHit]:
        if not candidates:
            return []

        candidate_text = "\n\n".join(
            f"{index}. 文件名：{item.filename}\n"
            f"BM25 分数：{item.retrieval_score}\n"
            f"关键词上下文：\n{item.text}"
            for index, item in enumerate(candidates, start=1)
        )
        data = await self.llm.chat_json(
            [
                ChatMessage(role="system", content=self.prompt),
                ChatMessage(
                    role="user",
                    content=(
                        f"用户问题：{query}\n\n"
                        f"候选文档：\n{candidate_text}"
                    ),
                ),
            ],
            self.model_profile,
        )
        filenames = (
            data.get("filenames", [])
            if isinstance(data, dict)
            else data
            if isinstance(data, list)
            else []
        )
        by_filename = {
            item.filename: item
            for item in candidates
        }
        selected = [
            by_filename[str(filename)]
            for filename in filenames
            if str(filename) in by_filename
        ][:top_k]
        return [
            RerankHit(
                document_id=item.document_id,
                filename=item.filename,
                score=float(len(selected) - index),
            )
            for index, item in enumerate(selected)
        ]
