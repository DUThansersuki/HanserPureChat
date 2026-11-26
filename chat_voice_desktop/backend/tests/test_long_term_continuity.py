from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hanser_agent import db
from hanser_agent.agent.conversation import ConversationStore
from hanser_agent.config import MemoryConfig
from hanser_agent.memory.continuity import ConversationContinuity
from hanser_agent.memory.extractor import MemoryCandidateExtractor, MemoryWriteGate
from hanser_agent.memory.store import MemoryStore
from hanser_agent.memory.summarizer import ConversationSummarizer
from hanser_agent.models import MemoryPatch


class LongTermContinuityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "memory.sqlite3"
        with db.connect(self.path) as conn:
            db.init_db(conn)
        self.messages = ConversationStore(self.path)
        self.memories = MemoryStore(self.path)
        self.config = MemoryConfig(recent_messages=4, summary_trigger_messages=8,
                                   summary_interval_messages=6, summary_max_chars=800)
        self.summarizer = ConversationSummarizer(conversations=self.messages, memories=self.memories,
                                                 config=self.config)

    def append(self, user, assistant="收到", *, conversation="c", owner="u"):
        return self.messages.append_turn(conversation_id=conversation, user_id=owner,
            user_text=user, assistant_text=assistant, model_name="test", trace_id="test", persona_version="test")

    def extract(self, text, message_id, conversation="c"):
        candidates = MemoryCandidateExtractor().extract(user_id="u", conversation_id=conversation,
                                                        message_id=message_id, message=text)
        return [self.memories.upsert_candidate(candidate)[0]
                for candidate in MemoryWriteGate(self.config).select(candidates)]

    def context(self, query, conversation="c", owner="u"):
        summary = self.summarizer.update(conversation, user_id=owner)
        return self.summarizer.context(conversation, user_id=owner, query=query, summary=summary) or ""

    def test_early_interaction_survives_300_later_turns_and_restart(self):
        user_id, assistant_id = self.append("请帮我检查星海方案的第七步", "第七步应先保存原始测量再归一化。")
        for index in range(300):
            self.append(f"随便聊天第{index}轮")
            self.summarizer.update("c", user_id="u")
        self.summarizer = ConversationSummarizer(conversations=ConversationStore(self.path),
                                                memories=MemoryStore(self.path), config=self.config)
        content = self.context("星海方案第七步之前怎么改的？")
        self.assertIn("原始测量", content)
        self.assertIn(user_id, content)
        self.assertIn(assistant_id, content)
        self.assertLessEqual(len(content), 800)

    def test_query_recalls_middle_of_single_oversized_message(self):
        self.append("开场" + "x" * 5000 + "。星海试验的保留温度为37度。" + "y" * 5000,
                    "先保留温度参数，再开始试验。", conversation="old")
        content = self.context("星海试验保留温度是多少？", "new")
        self.assertIn("37度", content)
        self.assertLessEqual(len(content), 800)

    def test_cross_session_callbacks_are_two_sided_quotes_and_owner_isolated(self):
        self.append("帮我选蓝鲸项目的标题", "可以叫蓝鲸观测日志。", conversation="old")
        with db.connect(self.path) as conn:
            conn.execute("UPDATE messages SET created_at='2025-12-30T18:20:00+00:00' WHERE conversation_id='old'")
        self.append("蓝鲸项目密码是秘密123", "秘密123", conversation="other", owner="other")
        content = self.context("蓝鲸项目上次选了什么标题", "new")
        self.assertIn("旧会话已保存互动", content)
        self.assertIn("用户原话", content)
        self.assertIn("Hanser当时回答", content)
        self.assertIn("观测日志", content)
        self.assertIn("2025-12-31 02:20+08", content)
        self.assertNotIn("秘密123", content)

    def test_unverified_claim_of_shared_life_is_not_written_as_verified_memory(self):
        text = "我们上次一起去过月球"
        message, _ = self.append(text, "我没有实际一起去过月球的经历。", conversation="old")
        self.assertEqual(self.extract(text, message, "old"), [])
        content = self.context("上次一起去月球？", "new")
        self.assertIn("用户原话", content)
        self.assertIn("没有实际", content)
        self.assertNotIn("当前记忆", content)

    def test_correction_and_forgetting_refresh_without_waiting_for_interval(self):
        text = "我喜欢咖啡"
        message, _ = self.append(text, "记住了，你喜欢咖啡。")
        original = self.extract(text, message)[0]
        for index in range(5):
            self.append(f"无关闲聊{index}")
        before = self.summarizer.update("c", user_id="u")
        self.assertIn("喜欢咖啡", before.content)
        correction = "我其实不喜欢咖啡"
        message, _ = self.append(correction, "收到，修正偏好。", conversation="other")
        revised = self.extract(correction, message, "other")[0]
        after = self.summarizer.update("c", user_id="u")
        self.assertEqual(before.through_message_index, after.through_message_index)
        self.assertIn("不喜欢咖啡", after.content)
        self.assertNotIn(original.id, after.content)
        self.memories.delete_memory(revised.id)
        content = self.context("之前喜欢咖啡吗？")
        self.assertNotIn("喜欢咖啡", content)
        self.assertNotIn("记住了", content)

    def test_manual_edit_blocks_original_pair_and_uses_current_state(self):
        text = "我喜欢咖啡"
        message, _ = self.append(text, "喜欢咖啡。", conversation="old")
        memory = self.extract(text, message, "old")[0]
        self.memories.update_memory(memory.id, MemoryPatch(content="用户更喜欢茶"))
        content = self.context("之前喜欢咖啡吗？", "new")
        self.assertIn("更喜欢茶", content)
        self.assertNotIn("喜欢咖啡", content)

    def test_unresolved_then_closed_event_keeps_latest_state_across_sessions(self):
        text = "我明天要参加考试"
        message, _ = self.append(text)
        self.extract(text, message)
        self.assertIn("未决事项", self.context("明天考试", "new"))
        closing = "我已经考完试了"
        message, _ = self.append(closing, "辛苦了。", conversation="new")
        self.memories.close_unresolved(user_id="u", conversation_id="new", memory_key="unresolved:考试",
                                      source_message_id=message, content=closing)
        content = self.context("之前的考试", "other")
        self.assertIn("事项已结束", content)
        self.assertNotIn("未决事项", content)

    def test_index_is_incremental_idempotent_and_semantic_transaction_rolls_back(self):
        self.append("先讨论星海方案")
        continuity = ConversationContinuity(self.memories)
        with self.assertRaises(RuntimeError):
            with self.memories.transaction():
                self.assertEqual(continuity.index_new(user_id="u"), 1)
                raise RuntimeError("semantic commit failed")
        self.assertEqual(continuity.index_new(user_id="u"), 1)
        self.assertEqual(continuity.index_new(user_id="u"), 0)
        self.append("接着继续星海方案")
        self.assertEqual(continuity.index_new(user_id="u"), 1)
        with db.connect(self.path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM continuity_turns").fetchone()[0], 2)

    def test_full_source_ids_remain_with_many_facts_and_history_boundary_covers_pairs(self):
        for index in range(10):
            text = f"我喜欢饮品{index}"
            message, _ = self.append(text)
            self.extract(text, message)
        user, assistant = self.append("星海方案如何校准", "星海方案先保存原始读数。")
        for index in range(4):
            self.append(f"无关闲聊{index}")
        content = self.context("星海方案如何校准")
        self.assertIn(user, content)
        self.assertIn(assistant, content)
        self.assertIn("原始读数", content)
        self.assertLessEqual(len(content), 800)
        self.summarizer.config.recent_messages = 3
        summary = self.summarizer.update("c", user_id="u")
        self.assertEqual(summary.through_message_index % 2, 1)


if __name__ == "__main__":
    unittest.main()
