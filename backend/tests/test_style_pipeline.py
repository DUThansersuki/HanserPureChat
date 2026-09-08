from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hanser_agent import db
from hanser_agent.agent.tools.style_search import StyleSearchTool
from hanser_agent.config import LLMTaskConfig, Settings, StyleConfig
from hanser_agent.models import DialoguePlan
from hanser_agent.persona.data_pipeline import (
    extract_style_drafts,
    label_style,
    persona_statistics,
)
from hanser_agent.retrieval.vector_store import SQLiteVectorStore


class FakeEmbedder:
    model_name = "test-embedding"

    async def embed_queries(self, texts):
        return [[1.0, 0.0] for _ in texts]

    async def embed_documents(self, texts):
        return [[1.0, 0.0] for _ in texts]


class StylePipelineTests(unittest.TestCase):
    def test_legacy_style_migration_never_auto_approves_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.db"
            with db.connect(path) as conn:
                conn.execute(db.SCHEMA_STYLE_EXAMPLES.split(",\n    review_status")[0] + "\n)")
                base = (
                    "INSERT INTO style_examples (id,prompt,response,context_before,"
                    "context_after,scene,speech_act,tone_json,answer_length,"
                    "response_mode,source_type,source_ref,authenticity_score,"
                    "quality_score,metadata_json) VALUES "
                    "(?, '问', '答', '', '', 'casual_chat', 'react', '[]', "
                    "'short', 'casual', 'real', 'source', .9, .9, '{}')"
                )
                conn.execute(base, (919,))
                conn.execute(base, (1000,))
                conn.commit()
            with db.connect(path) as conn:
                db.init_db(conn)
                rows = {
                    row["id"]: row["review_status"]
                    for row in conn.execute(
                        "SELECT id,review_status FROM style_examples"
                    ).fetchall()
                }
            self.assertEqual(rows[919], "quarantined")
            self.assertEqual(rows[1000], "pending")

    def test_only_explicit_bullet_dialogue_becomes_real_style_data(self) -> None:
        text = (
            "00:01 这是普通旁白 不应单独进入样例\n"
            "00:02 （弹幕：晚上好）晚上好呀 今天来得挺早\n"
            "00:03 弹幕：你是不是又熬夜了\n"
            "憨憨：才没有 我这是起得早\n"
            "00:04 S：这是其他嘉宾说的话"
        )
        rows = extract_style_drafts(
            document_id=7,
            filename="2023年1月1日.docx",
            content=text,
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].prompt, "晚上好")
        self.assertEqual(rows[0].response, "晚上好呀 今天来得挺早")
        self.assertTrue(all(item.source_type == "real" for item in rows))
        self.assertTrue(all(item.metadata["fact_eligible"] is False for item in rows))
        self.assertTrue(all(item.review_status == "pending" for item in rows))
        self.assertTrue(all(item.source_speaker == "hanser" for item in rows))
        self.assertTrue(all(item.source_raw_text for item in rows))

    def test_style_labels_and_statistics_are_deterministic(self) -> None:
        labels = label_style("今天好难过", "没事啦 别怕")
        self.assertEqual(labels["scene"], "comfort")
        self.assertEqual(labels["response_mode"], "emotional")

        rows = extract_style_drafts(
            document_id=1,
            filename="2023年1月1日.docx",
            content="（弹幕：你怎么这么可爱）哈哈 你说得对",
        )
        stats = persona_statistics([(1, rows[0])])
        self.assertEqual(stats["sample_count"], 1)
        self.assertEqual(stats["laughter_counts"]["哈哈"], 1)

    def test_parser_separates_mixed_bullets_speakers_and_timestamps(self) -> None:
        text = (
            "0.12；（弹幕：晚上好）憨憨：晚上好呀 "
            "弹幕：你累吗 憨憨：还好啦 海：我也还好\n"
            "弹幕：只给其他嘉宾的问题 海：不知道\n"
            "Q：今天开心吗 A：挺开心的 1.20；下一段"
        )
        rows = extract_style_drafts(
            document_id=9,
            filename="2023年1月1日.docx",
            content=text,
        )
        pairs = {(row.prompt, row.response) for row in rows}
        self.assertIn(("晚上好", "晚上好呀"), pairs)
        self.assertIn(("你累吗", "还好啦"), pairs)
        self.assertIn(("今天开心吗", "挺开心的"), pairs)
        self.assertFalse(any("海：" in row.response for row in rows))
        self.assertFalse(any("其他嘉宾" in row.prompt for row in rows))
        self.assertTrue(all(row.source_start_char < row.source_end_char for row in rows))
        self.assertTrue(all(row.source_user_turn == row.prompt for row in rows))
        self.assertTrue(all(row.source_response_turn == row.response for row in rows))

        comma_timestamp = extract_style_drafts(
            document_id=10,
            filename="2023年10月18日.docx",
            content=(
                "（弹幕：好听）憨憨：不会烦嘛？哪首鸭，不会是猫耳开关吧！"
                "0,59；《月》//"
            ),
        )
        self.assertEqual(comma_timestamp[0].response, "不会烦嘛？哪首鸭，不会是猫耳开关吧！")

    def test_answer_content_does_not_change_user_speech_act_label(self) -> None:
        self.assertEqual(
            label_style("不然呢！！！", "你打了三个感叹号感觉你好生气")["scene"],
            "casual_chat",
        )
        self.assertEqual(label_style("晚上好", "你好呀")["scene"], "greeting")
        self.assertNotEqual(
            label_style("你好久没播了", "回来啦")["scene"],
            "greeting",
        )
        self.assertNotEqual(label_style("累计三千", "好多啊")["scene"], "comfort")
        self.assertNotEqual(label_style("我没有哭", "那就好")["scene"], "comfort")
        self.assertEqual(label_style("今天真的好累", "辛苦啦")["scene"], "comfort")
        self.assertEqual(label_style("早上好呀，我来报到啦", "早呀")["scene"], "greeting")
        self.assertEqual(label_style("项目赶不完，我累坏了", "先歇会儿")["scene"], "comfort")


class StyleSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_factual_mode_does_not_retrieve_style_examples(self) -> None:
        task = LLMTaskConfig("unused", 64, 0.1, 0.9)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                root=root, db_path=root / "missing.db", data_dir=root,
                userdict_path=root / "userdict.txt", base_url="http://localhost/v1",
                api_key="test", default_model="unused", bunny=task,
                prometheus=task, hanser=task, style=StyleConfig(reviewed_only=True),
            )
            result = await StyleSearchTool(
                settings=settings, embedder=FakeEmbedder(),
                vector_store=SQLiteVectorStore(root / "missing.db"),
            ).search("你哪年加入VOMS", DialoguePlan(
                intent="wiki_fact", need_wiki=True, standalone_query="你哪年加入VOMS",
                keywords=[], response_mode="factual", fact_sensitivity="high",
                target_length="short",
            ))
        self.assertEqual(result.examples, [])

    async def test_style_search_returns_real_examples_by_scene(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "documents.db"
            with db.connect(db_path) as conn:
                db.init_db(conn)
                for prompt, response, scene, mode in (
                    ("晚上好", "晚上好呀", "greeting", "casual"),
                    ("为什么", "因为就是这样嘛", "question_answer", "casual"),
                ):
                    conn.execute(
                        """
                        INSERT INTO style_examples
                            (prompt, response, context_before, context_after,
                             scene, speech_act, tone_json, relationship_level,
                             energy, teasing_level, answer_length, response_mode,
                             source_type, source_ref, authenticity_score,
                             quality_score, metadata_json, review_status,
                             source_tier, source_document_id, source_start_line,
                             source_end_line, source_start_char, source_end_char,
                             source_speaker, source_user_turn, source_response_turn,
                             source_raw_text, embedding_ref)
                        VALUES (?, ?, '', '', ?, 'react', ?, 'audience',
                                0.6, 0.1, 'short', ?, 'real', ?, 0.95, 0.95, ?,
                                'approved', 'primary', 1, 1, 1, 0, 20,
                                'hanser', ?, ?, ?, 'style_examples:seed')
                        """,
                        (
                            prompt,
                            response,
                            scene,
                            json.dumps(["conversational"]),
                            mode,
                            f"source:{prompt}",
                            json.dumps({"fact_eligible": False}),
                            prompt,
                            response,
                            f"弹幕：{prompt} 憨憨：{response}",
                        ),
                    )
                conn.commit()

            store = SQLiteVectorStore(db_path)
            store.upsert(
                "style_examples",
                "test-embedding",
                [("1", [1.0, 0.0]), ("2", [0.0, 1.0])],
            )
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
                style=StyleConfig(top_k=1, reviewed_only=True),
            )
            result = await StyleSearchTool(
                settings=settings,
                embedder=FakeEmbedder(),
                vector_store=store,
            ).search(
                "晚上好呀，我来啦",
                DialoguePlan(
                    intent="chitchat",
                    need_wiki=False,
                    standalone_query="晚上好呀，我来啦",
                    keywords=[],
                    response_mode="casual",
                    fact_sensitivity="low",
                    target_length="short",
                ),
            )

        self.assertEqual(len(result.examples), 1)
        self.assertEqual(result.examples[0].user_context, "晚上好")
        self.assertEqual(result.examples[0].source_type, "real")
        self.assertEqual(result.examples[0].review_status, "approved")

    async def test_only_reviewed_real_or_explicit_synthetic_sources_are_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "documents.db"
            with db.connect(path) as conn:
                db.init_db(conn)
                for index, (review, tier, source_type) in enumerate((
                    ("pending", "primary", "real"),
                    ("approved", "synthetic", "synthetic"),
                    ("approved", "unknown", "real"),
                ), start=1):
                    conn.execute(
                        """
                        INSERT INTO style_examples
                            (prompt,response,context_before,context_after,scene,
                             speech_act,tone_json,answer_length,response_mode,
                             source_type,source_ref,authenticity_score,quality_score,
                             metadata_json,review_status,source_tier,source_speaker,
                             source_user_turn,source_response_turn,embedding_ref)
                        VALUES ('晚上好','晚上好呀','','','greeting','greet','[]',
                                'short','casual',?,?,.9,.9,'{}',?,?,'hanser',
                                '晚上好','晚上好呀',?)
                        """,
                        (source_type, f"source:{index}", review, tier, f"style:{index}"),
                    )
                conn.commit()
            vector = SQLiteVectorStore(path)
            vector.upsert("style_examples", "test-embedding", [
                ("1", [1.0, 0.0]), ("2", [1.0, 0.0]), ("3", [1.0, 0.0]),
            ])
            task = LLMTaskConfig("unused", 64, 0.1, 0.9)
            settings = Settings(
                root=root, db_path=path, data_dir=root,
                userdict_path=root / "userdict.txt", base_url="http://localhost/v1",
                api_key="test", default_model="unused", bunny=task,
                prometheus=task, hanser=task,
                style=StyleConfig(top_k=3, reviewed_only=True),
            )
            result = await StyleSearchTool(
                settings=settings, embedder=FakeEmbedder(), vector_store=vector,
            ).search("晚上好呀，我来啦", DialoguePlan(
                intent="chitchat", need_wiki=False, standalone_query="晚上好呀，我来啦",
                keywords=[], response_mode="casual", fact_sensitivity="low",
                target_length="short",
            ))
        self.assertEqual(len(result.examples), 1)
        self.assertEqual(result.examples[0].source_type, "synthetic")
        self.assertEqual(result.examples[0].source_tier, "synthetic")


if __name__ == "__main__":
    unittest.main()
