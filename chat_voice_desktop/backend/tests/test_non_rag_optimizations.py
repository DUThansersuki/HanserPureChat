from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hanser_agent import db
from hanser_agent.agent.conversation import ConversationStore
from hanser_agent.memory.store import MemoryStore
from hanser_agent.memory.summarizer import ConversationSummarizer
from hanser_agent.persona.permissions import infer_expression_permissions
from hanser_agent.persona.signals import build_turn_signals


class TopicPermissionTests(unittest.TestCase):
    def test_topic_permission_survives_history_and_explicit_end(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.sqlite3"
            with db.connect(path) as conn:
                db.init_db(conn)
            store = MemoryStore(path)
            first = build_turn_signals("别拿这件事开玩笑", current_message_ref="m1")
            store.save_permission_events(user_id="u", conversation_id="c", events=first.preference_events)
            persisted = store.list_permission_events(user_id="u", conversation_id="c")
            followup = build_turn_signals("然后呢", current_message_ref="m40", prior_preference_events=persisted)
            permissions = infer_expression_permissions(followup.preference_events, current_message_id="m40")
            self.assertEqual(permissions["target:humor:这件事"], "deny")
            self.assertNotIn("humor", permissions)
            self.assertEqual(store.list_permission_events(user_id="u", conversation_id="other"), [])
            end = build_turn_signals("换个话题，聊游戏", current_message_ref="m41", prior_preference_events=persisted)
            store.save_permission_events(user_id="u", conversation_id="c", events=[e for e in end.preference_events if e.source_message_id == "m41"])
            self.assertNotIn("target:humor:这件事", infer_expression_permissions(store.list_permission_events(user_id="u", conversation_id="c"), current_message_id="m42"))

    def test_explicit_allow_restores_topic_but_laughter_does_not(self):
        first = build_turn_signals("别拿这件事开玩笑", current_message_ref="m1")
        laughter = build_turn_signals("哈哈", current_message_ref="m2", prior_preference_events=first.preference_events)
        self.assertEqual(infer_expression_permissions(laughter.preference_events, current_message_id="m2")["target:humor:这件事"], "deny")
        restore = build_turn_signals("可以开玩笑", current_message_ref="m3", prior_preference_events=first.preference_events)
        permissions = infer_expression_permissions(restore.preference_events, current_message_id="m3")
        self.assertNotIn("target:humor:这件事", permissions)
        self.assertEqual(permissions["humor"], "allow")

    def test_topic_end_preserves_durable_preferences_and_quoted_end_is_ignored(self):
        first = build_turn_signals("以后不要说脏话，别拿生日开玩笑", current_message_ref="m1")
        quoted = build_turn_signals('他说“换个话题”', current_message_ref="m2", prior_preference_events=first.preference_events)
        self.assertEqual(infer_expression_permissions(quoted.preference_events, current_message_id="m2")["target:humor:生日"], "deny")
        end = build_turn_signals("这个话题结束", current_message_ref="m3", prior_preference_events=first.preference_events)
        permissions = infer_expression_permissions(end.preference_events, current_message_id="m3")
        self.assertEqual(permissions["profanity"], "deny")
        self.assertNotIn("target:humor:生日", permissions)


class SummaryBudgetTests(unittest.TestCase):
    def test_oversized_latest_entry_retains_content_and_correction(self):
        value = ConversationSummarizer._fit_complete_entries(["用户: 较早上下文", "用户: [历史说法，已被修订] 开头" + "x" * 6000 + "结尾改成茶"], 800)
        self.assertLessEqual(len(value), 800)
        self.assertIn("开头", value)
        self.assertIn("结尾改成茶", value)
        self.assertIn("较早上下文", value)
        self.assertIn("已被修订", value)


if __name__ == "__main__":
    unittest.main()
