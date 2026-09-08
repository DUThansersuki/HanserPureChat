from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hanser_agent.retrieval.chunker import chunk_document
from hanser_agent.retrieval.hybrid import reciprocal_rank_fusion
from hanser_agent.retrieval.vector_store import SQLiteVectorStore


class HybridRetrievalTests(unittest.TestCase):
    def test_chunker_preserves_offsets_and_semantic_boundaries(self) -> None:
        text = "\n".join(
            f"第{index}段 这是用于检索的完整语义句子。"
            for index in range(40)
        )
        chunks = chunk_document(
            text,
            target_chars=180,
            max_chars=240,
            overlap_chars=50,
        )

        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(len(item.text) <= 240 for item in chunks))
        self.assertTrue(
            all(text[item.start_char : item.end_char] == item.text for item in chunks)
        )
        self.assertEqual(
            [item.chunk_index for item in chunks],
            list(range(len(chunks))),
        )

    def test_rrf_rewards_items_found_by_both_retrievers(self) -> None:
        scores = reciprocal_rank_fusion(
            [[1, 2, 3], [3, 4, 1]],
            rank_constant=60,
        )

        self.assertGreater(scores[1], scores[2])
        self.assertGreater(scores[3], scores[4])

    def test_sqlite_vector_store_uses_exact_cosine_ranking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteVectorStore(Path(tmp) / "vectors.db")
            store.upsert(
                "facts",
                "test-model",
                [
                    ("a", [1.0, 0.0]),
                    ("b", [0.0, 1.0]),
                    ("c", [0.7, 0.7]),
                ],
            )
            hits = store.search(
                "facts",
                "test-model",
                [1.0, 0.0],
                top_k=2,
            )

        self.assertEqual([item.item_id for item in hits], ["a", "c"])


if __name__ == "__main__":
    unittest.main()
