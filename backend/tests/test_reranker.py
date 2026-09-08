from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hanser_agent.agent.tools.wiki_search import WikiSearchTool
from hanser_agent.config import LLMTaskConfig, RerankerConfig, Settings
from hanser_agent.retrieval import (
    BM25Reranker,
    HybridCandidate,
    LocalQwenReranker,
    RerankCandidate,
    RerankHit,
)


class SelectSecondReranker:
    name = "test_select_second"

    async def rerank(self, query, candidates, *, top_k):
        del query, top_k
        item = candidates[1]
        return [
            RerankHit(
                document_id=item.document_id,
                filename=item.filename,
                score=0.91,
                chunk_id=item.chunk_id,
            )
        ]


class FakeHybridRetriever:
    async def search(self, query, keywords, *, top_k):
        del query, keywords, top_k
        return [
            HybridCandidate(
                chunk_id=101,
                document_id=1,
                chunk_index=0,
                filename="first.md",
                filepath="first.md",
                text="Hanser 直播 A",
                metadata={"source_type": "wiki", "date": None},
                retrieval_score=0.03,
                bm25_score=3.0,
                dense_score=0.7,
                matched=("Hanser", "直播"),
            ),
            HybridCandidate(
                chunk_id=102,
                document_id=2,
                chunk_index=1,
                filename="second.md",
                filepath="second.md",
                text="Hanser 直播 B",
                metadata={"source_type": "wiki", "date": "2023-01-01"},
                retrieval_score=0.02,
                bm25_score=2.0,
                dense_score=0.8,
                matched=("Hanser",),
            ),
        ]


class RerankerTests(unittest.IsolatedAsyncioTestCase):
    async def test_bm25_profile_preserves_retrieval_order(self) -> None:
        candidates = [
            RerankCandidate(1, "a.md", "A", 3.0),
            RerankCandidate(2, "b.md", "B", 8.0),
        ]
        hits = await BM25Reranker().rerank(
            "query",
            candidates,
            top_k=1,
        )
        self.assertEqual(hits[0].document_id, 2)
        self.assertEqual(hits[0].score, 8.0)

    async def test_wiki_search_uses_injected_reranker_and_records_score(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "documents.db"
            task = LLMTaskConfig("unused", 64, 0.1, 0.9)
            settings = Settings(
                root=root,
                db_path=db_path,
                data_dir=root,
                userdict_path=root / "userdict.txt",
                base_url="http://localhost/v1",
                api_key="test",
                default_model="unused",
                bunny=task,
                prometheus=task,
                hanser=task,
                reranker=RerankerConfig(top_k=1),
            )
            result = await WikiSearchTool(
                settings,
                SelectSecondReranker(),
                FakeHybridRetriever(),
            ).search("Hanser 直播", ["Hanser", "直播"])

        self.assertEqual(result.anchored, ["second.md"])
        self.assertEqual(
            result.rerank_scores,
            {"document:2:chunk:102": 0.91},
        )
        self.assertEqual(result.evidence[0].filename, "second.md")
        self.assertEqual(result.evidence[0].chunk_id, 102)
        self.assertEqual(result.evidence[0].rerank_score, 0.91)

    def test_qwen_input_keeps_fact_query_and_document_separate(self) -> None:
        reranker = LocalQwenReranker(
            RerankerConfig(instruction="Retrieve evidence")
        )
        formatted = reranker._format_instruction(
            "什么时候退出",
            "Wiki evidence",
        )
        self.assertEqual(
            formatted,
            "<Instruct>: Retrieve evidence\n"
            "<Query>: 什么时候退出\n"
            "<Document>: Wiki evidence",
        )


if __name__ == "__main__":
    unittest.main()
