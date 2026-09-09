from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from hanser_agent.agent.context_builder import ContextBundle  # noqa: F401
from hanser_agent.models import ChatMessage, MemoryItem
from hanser_agent.persona.expression import observe_recent_expressions
from hanser_agent.persona.permissions import infer_expression_permissions
from hanser_agent.persona.policy import build_guidance
from hanser_agent.persona.schemas import EffectivePersonaSettings
from hanser_agent.persona.signals import build_turn_signals
from hanser_agent.responder import HanserResponder, StyleValidator


CANDIDATE_DIR = (
    Path(__file__).resolve().parents[1]
    / "hanser_agent"
    / "prompts"
    / "persona"
    / "candidates"
    / "hanser-persona-v2-candidate"
)


class PersonaV2LogicChecklistTests(unittest.TestCase):
    def test_l01_negated_deny_then_allow(self) -> None:
        signals = build_turn_signals(
            "我不是说别开玩笑，你可以自然聊", current_message_ref="current"
        )
        self.assertEqual(
            [(item.feature, item.decision) for item in signals.preference_events],
            [("humor", "allow")],
        )

    def test_l02_deny_substring_never_recovers_allow(self) -> None:
        history = [
            ChatMessage(role="user", content="别开玩笑", message_id="old"),
        ]
        signals = build_turn_signals(
            "不可以开玩笑",
            current_message_ref="current",
            history_messages=history,
        )
        permissions = infer_expression_permissions(
            signals.preference_events, current_message_id="current"
        )
        self.assertEqual(permissions["humor"], "deny")
        self.assertNotIn("allow", [item.decision for item in signals.preference_events])

    def test_l03_reported_stop_is_not_user_preference(self) -> None:
        signals = build_turn_signals(
            "他刚才说“别开玩笑”，我只是转述", current_message_ref="current"
        )
        self.assertEqual(signals.preference_events, [])

    def test_l04_profanity_deny_does_not_disable_humor(self) -> None:
        signals = build_turn_signals(
            "别爆粗，但可以继续玩梗", current_message_ref="current"
        )
        permissions = infer_expression_permissions(
            signals.preference_events, current_message_id="current"
        )
        decision = build_guidance(
            signals, permissions, None, EffectivePersonaSettings()
        )
        self.assertIn("profanity", decision.expression_caps.hard_disallowed)
        self.assertNotIn("humor", decision.expression_caps.hard_disallowed)
        self.assertIsNone(signals.observed_bool("explicit_stop"))

    def test_l05_no_advice_is_narrow(self) -> None:
        signals = build_turn_signals(
            "先别给建议，陪我随便聊聊就好", current_message_ref="current"
        )
        decision = build_guidance(signals, {}, None, EffectivePersonaSettings())
        ids = {item.requirement_id for item in decision.must_not}
        self.assertIn("user.no_unsolicited_advice", ids)
        self.assertNotIn("user.explicit_stop", ids)
        self.assertIsNone(signals.observed_bool("explicit_stop"))

    def test_l06_current_message_defines_reference(self) -> None:
        signals = build_turn_signals(
            "这个方案是先备份再升级，你分析一下风险",
            current_message_ref="current",
        )
        self.assertIsNone(signals.observed_bool("unresolved_reference"))

    def test_l07_prior_turn_defines_reference(self) -> None:
        signals = build_turn_signals(
            "这个方案的风险呢",
            current_message_ref="current",
            history_messages=[
                ChatMessage(role="user", content="先备份再升级", message_id="old-user"),
                ChatMessage(role="assistant", content="明白", message_id="old-assistant"),
            ],
        )
        self.assertIsNone(signals.observed_bool("unresolved_reference"))

    def test_l08_reported_emotion_is_not_user_distress(self) -> None:
        signals = build_turn_signals(
            "她说我很难受，我在转述她的话", current_message_ref="current"
        )
        self.assertIsNone(signals.observed_bool("distress"))
        self.assertIsNone(signals.observed_bool("user_emotion"))

    def test_l09_planner_stop_is_not_hard_rule(self) -> None:
        signals = build_turn_signals(
            "够了",
            current_message_ref="current",
            planner_payload={
                "explicit_stop": {
                    "value": True,
                    "confidence": "low",
                    "evidence_refs": [],
                }
            },
        )
        decision = build_guidance(signals, {}, None, EffectivePersonaSettings())
        self.assertTrue(signals.observed_bool("explicit_stop"))
        self.assertIsNone(signals.hard_bool("explicit_stop"))
        self.assertNotIn(
            "user.explicit_stop", {item.requirement_id for item in decision.must_not}
        )

    def test_l10_durable_preference_survives_history_window(self) -> None:
        durable = MemoryItem(
            id="memory-1",
            user_id="user-a",
            type="user_preference",
            memory_key="preference:expression:humor:*",
            content="以后别开玩笑",
            importance=1.0,
            confidence=1.0,
            predicate="expression_permission",
            object_value="deny",
            created_at=datetime.now(timezone.utc),
        )
        permissions = infer_expression_permissions([], persistent_preferences=[durable])
        self.assertEqual(permissions["humor"], "deny")

    def test_long_term_profanity_wording_is_a_user_scoped_event(self) -> None:
        signals = build_turn_signals(
            "记住我以后不喜欢你爆粗", current_message_ref="current"
        )
        event = next(item for item in signals.preference_events if item.feature == "profanity")
        self.assertEqual((event.decision, event.scope), ("deny", "user"))

    def test_elliptical_revoke_resolves_one_recent_explicit_feature(self) -> None:
        signals = build_turn_signals(
            "算了还是别说",
            current_message_ref="current",
            history_messages=[
                ChatMessage(role="user", content="你可以说点轻粗口", message_id="old")
            ],
        )
        current = [item for item in signals.preference_events if item.source_message_id == "current"]
        self.assertEqual([(item.feature, item.decision) for item in current], [("profanity", "deny")])

    def test_l11_reliable_is_not_profanity_violation(self) -> None:
        validator = StyleValidator(CANDIDATE_DIR / "style_constraints.yaml")
        decision = build_guidance(
            build_turn_signals("别爆粗", current_message_ref="current"),
            {"profanity": "deny"},
            None,
            EffectivePersonaSettings(),
        )
        result = validator.validate_semantic_output(
            "这个办法很可靠", behavior_decision=decision
        )
        self.assertNotIn("disallowed_feature:profanity", result.violations)

    def test_l12_substrings_are_not_expression_observations(self) -> None:
        observed = observe_recent_expressions(
            [ChatMessage(role="assistant", content="草莓蛋糕很好吃，这个办法很可靠")]
        )
        self.assertEqual(observed.features["meme"].value, "absent")
        self.assertEqual(observed.features["profanity"].value, "absent")

    def test_l13_fallback_is_topic_neutral(self) -> None:
        text = HanserResponder._permission_fallback_text(
            [ChatMessage(role="user", content="披萨话题，我开玩笑的")],
            ["disallowed_feature:teasing"],
        )
        self.assertEqual(text, "明白 我会停下相关表达")
        self.assertNotIn("恢复步骤", text or "")


if __name__ == "__main__":
    unittest.main()
