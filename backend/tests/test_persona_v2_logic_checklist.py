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

    def test_strong_self_reference_is_profanity_but_laozi_name_is_not(self) -> None:
        observed = observe_recent_expressions(
            [
                ChatMessage(role="assistant", content="老子今天非把这破关过了"),
                ChatMessage(role="assistant", content="老娘现在就去收拾这个烂摊子"),
            ]
        )
        philosophy = observe_recent_expressions(
            [ChatMessage(role="assistant", content="我最近在看老子的《道德经》")]
        )

        self.assertEqual(observed.features["profanity"].value, "present")
        self.assertEqual(philosophy.features["profanity"].value, "absent")

    def test_wo_kao_is_observed_as_profanity(self) -> None:
        observed = observe_recent_expressions(
            [ChatMessage(role="assistant", content="我靠 这游戏又闪退了")]
        )

        self.assertEqual(observed.features["profanity"].value, "present")

    def test_profanity_deny_blocks_strong_self_reference(self) -> None:
        validator = StyleValidator(CANDIDATE_DIR / "style_constraints.yaml")
        decision = build_guidance(
            build_turn_signals("别爆粗", current_message_ref="current"),
            {"profanity": "deny"},
            None,
            EffectivePersonaSettings(),
        )

        blocked = validator.validate_semantic_output(
            "老子今天非把这关过了", behavior_decision=decision
        )
        allowed_name = validator.validate_semantic_output(
            "我在读老子的道德经", behavior_decision=decision
        )

        self.assertIn("disallowed_feature:profanity", blocked.violations)
        self.assertNotIn("disallowed_feature:profanity", allowed_name.violations)

    def test_scheduled_profanity_is_verified_in_final_output(self) -> None:
        validator = StyleValidator(CANDIDATE_DIR / "style_constraints.yaml")
        decision = build_guidance(
            build_turn_signals(
                "这游戏又闪退了",
                current_message_ref="current",
                planner_payload={
                    "playful_frame": {
                        "value": True,
                        "confidence": "high",
                        "evidence_refs": ["current_user"],
                    }
                },
            ),
            {},
            observe_recent_expressions([]),
            EffectivePersonaSettings(profanity_target_rate=0.15),
            pacing_key="frequency-eval-user:profanity15.daily.pet",
            successful_assistant_turns=0,
        )

        self.assertIn(
            "expression.use_light_profanity",
            {item.requirement_id for item in decision.must_do},
        )
        missing = validator.validate_semantic_output(
            "这游戏也太折磨人了", behavior_decision=decision
        )
        present = validator.validate_semantic_output(
            "我靠 这游戏也太折磨人了", behavior_decision=decision
        )
        self.assertIn("missing_required_feature:profanity", missing.violations)
        self.assertNotIn("missing_required_feature:profanity", present.violations)

    def test_unverified_past_experience_exposed_by_profanity_eval_is_rejected(self) -> None:
        validator = StyleValidator(CANDIDATE_DIR / "style_constraints.yaml")

        result = validator.validate_semantic_output(
            "我靠 经典环节 我当时装柜子也这样"
        )

        self.assertIn(
            "unsupported_first_person_past_experience",
            result.violations,
        )

    def test_l13_fallback_is_topic_neutral(self) -> None:
        text = HanserResponder._permission_fallback_text(
            [ChatMessage(role="user", content="披萨话题，我开玩笑的")],
            ["disallowed_feature:teasing"],
        )
        self.assertEqual(text, "明白 我会停下相关表达")
        self.assertNotIn("恢复步骤", text or "")

    def test_adult_innuendo_setting_opens_only_the_adult_permission_gates(self) -> None:
        signals = build_turn_signals(
            "这个标题的双关有点坏",
            current_message_ref="current",
            adult_innuendo_opt_in=True,
        )
        permissions = infer_expression_permissions(
            signals.preference_events,
            current_message_id="current",
            explicit_overrides={"innuendo": "allow"},
        )
        decision = build_guidance(
            signals, permissions, None, EffectivePersonaSettings()
        )

        self.assertEqual(signals.get("audience_age_status").source, "explicit_setting")
        self.assertTrue(signals.get("audience_age_status").hard_rule_eligible)
        self.assertNotIn("innuendo", decision.expression_caps.hard_disallowed)

    def test_current_minor_statement_overrides_adult_innuendo_setting(self) -> None:
        signals = build_turn_signals(
            "我未成年，这个双关是什么意思",
            current_message_ref="current",
            adult_innuendo_opt_in=True,
        )
        decision = build_guidance(
            signals,
            {"innuendo": "allow"},
            None,
            EffectivePersonaSettings(),
        )

        self.assertEqual(signals.get("audience_age_status").value, "minor")
        self.assertIn("innuendo", decision.expression_caps.hard_disallowed)


if __name__ == "__main__":
    unittest.main()
