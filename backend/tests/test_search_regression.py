from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from hanser_agent.db import connect, init_db
from hanser_agent.search import K1, B, context_snippets, search_documents


class SearchRegressionTests(unittest.TestCase):
    def test_bm25_constants_and_ranking(self) -> None:
        self.assertEqual(K1, 1.5)
        self.assertEqual(B, 0.75)

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "documents.db"
            with connect(db_path) as conn:
                init_db(conn)
                conn.executemany(
                    """
                    INSERT INTO documents
                        (id, filename, filepath, content, mtime, size, indexed_at)
                    VALUES (?, ?, ?, ?, 0, 0, 'now')
                    """,
                    [
                        (1, "focused.md", "focused.md", "VirtuaReal 退出"),
                        (2, "long.md", "long.md", "VirtuaReal 其他内容"),
                    ],
                )
                conn.executemany(
                    "INSERT INTO doc_tokens (doc_id, token, tf) VALUES (?, ?, ?)",
                    [
                        (1, "VirtuaReal", 3),
                        (1, "退出", 1),
                        (2, "VirtuaReal", 1),
                        (2, "其他内容", 50),
                    ],
                )
                conn.commit()
                results = search_documents(
                    conn,
                    ["VirtuaReal"],
                    top_n=2,
                )

            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")

        self.assertEqual([item.id for item in results], [1, 2])
        self.assertGreater(results[0].score, results[1].score)

    def test_context_snippets_merge_overlaps(self) -> None:
        snippets = context_snippets(
            "前文 Hanser 中间 VirtuaReal 后文",
            ["Hanser", "VirtuaReal"],
            radius=12,
            max_snippets=3,
        )
        self.assertEqual(len(snippets), 1)
        self.assertIn("Hanser", snippets[0])
        self.assertIn("VirtuaReal", snippets[0])


if __name__ == "__main__":
    unittest.main()
