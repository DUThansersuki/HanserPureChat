from __future__ import annotations

import unittest

from hanser_agent.models import ChatMessage
from hanser_agent.persona.expression import observe_recent_expressions
from hanser_agent.persona.policy import build_guidance
from hanser_agent.persona.schemas import EffectivePersonaSettings, TurnSignals
from hanser_agent.persona.signals import build_turn_signals


class PersonaPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = EffectivePersonaSettings()

    def test_unknown_signals_do_not_disable_all_natural_humor(self) -> None:
        decision = build_guidance(
            TurnSignals(), {}, None, self.settings,
        )

        self.assertIn("direct_natural_reply", [item.id for item in decision.persona_affordances])
        self.assertNotIn("humor", decision.expression_caps.hard_disallowed)
        self.assertIn("innuendo", decision.expression_caps.hard_disallowed)

    def test_explicit_stop_is_hard_and_soft_permission_cannot_reopen(self) -> None:
        signals = build_turn_signals(
            "别逗我了 我不舒服",
            current_message_ref="message:current",
            planner_payload={
                "playful_frame": {
                    "value": True,
                    "confidence": "high",
                    "evidence_refs": ["current_user"],
                }
            },
        )
        decision = build_guidance(
            signals,
            {"humor": "allow", "teasing": "allow"},
            None,
            self.settings,
        )

        self.assertIn("humor", decision.expression_caps.hard_disallowed)
        self.assertIn("user.disable_humor", decision.boundary_ids)
        self.assertNotIn("light_contextual_tease", [item.id for item in decision.persona_affordances])

    def test_planner_only_explicit_stop_closes_playful_features(self) -> None:
        signals = build_turn_signals(
            "够了",
            current_message_ref="message:current",
            planner_payload={
                "explicit_stop": {
                    "value": True,
                    "confidence": "high",
                    "evidence_refs": ["current_user"],
                },
                "playful_frame": {
                    "value": True,
                    "confidence": "medium",
                    "evidence_refs": ["current_user"],
                },
            },
        )
        decision = build_guidance(
            signals,
            {"humor": "allow", "teasing": "allow", "innuendo": "allow"},
            None,
            self.settings,
        )

        self.assertIn(
            "user.explicit_stop",
            {item.requirement_id for item in decision.must_not},
        )
        self.assertTrue(
            {"humor", "teasing", "innuendo", "cutesy"}.issubset(
                decision.expression_caps.hard_disallowed
            )
        )

    def test_no_advice_produces_testable_requirement(self) -> None:
        signals = build_turn_signals(
            "我真的很难受 先别给建议",
            current_message_ref="message:current",
        )
        decision = build_guidance(signals, {}, None, self.settings)

        self.assertIn(
            "user.no_unsolicited_advice",
            [item.requirement_id for item in decision.must_not],
        )
        self.assertIn("acknowledge_specific_distress", [item.id for item in decision.persona_affordances])

    def test_recent_expression_is_soft_penalty_not_hard_cap(self) -> None:
        observations = observe_recent_expressions(
            [
                ChatMessage(role="assistant", content="靠 就差一点"),
                ChatMessage(role="user", content="又掉下去了"),
            ]
        )
        decision = build_guidance(TurnSignals(), {}, observations, self.settings)

        self.assertNotIn("profanity", decision.expression_caps.hard_disallowed)
        repetition = [
            item for item in decision.soft_preferences
            if item.preference_id == "repetition.profanity"
        ]
        self.assertEqual(len(repetition), 1)
        self.assertGreater(repetition[0].weight, 0)

    def test_adult_humor_requires_both_context_and_permission(self) -> None:
        signals = build_turn_signals(
            "成年人聊天 这个标题是不是有点不正经",
            current_message_ref="message:current",
            planner_payload={
                "playful_frame": {
                    "value": True,
                    "confidence": "high",
                    "evidence_refs": ["current_user"],
                },
                "audience_age_status": {
                    "value": "adult",
                    "confidence": "high",
                    "evidence_refs": ["current_user"],
                },
            },
        )
        denied = build_guidance(signals, {}, None, self.settings)
        allowed = build_guidance(signals, {"innuendo": "allow"}, None, self.settings)

        self.assertIn("innuendo", denied.expression_caps.hard_disallowed)
        self.assertNotIn("innuendo", allowed.expression_caps.hard_disallowed)

    def test_fact_sensitive_path_rejects_unverified_shared_memory(self) -> None:
        decision = build_guidance(
            TurnSignals(),
            {},
            None,
            self.settings,
            response_mode="factual",
            fact_sensitivity="high",
            need_wiki=True,
        )

        self.assertIn(
            "task.reject_unverified_shared_memory",
            {item.requirement_id for item in decision.must_do},
        )
        autobiography = next(
            item
            for item in decision.must_not
            if item.requirement_id == "boundary.no_unsupported_autobiography"
        )
        self.assertIn("不记得", autobiography.instruction)

    def test_stored_user_denies_are_hard_and_scoped_by_caller(self) -> None:
        denied = build_guidance(
            TurnSignals(),
            {"cutesy": "deny", "address": "deny", "profanity": "deny"},
            None,
            self.settings,
        )
        other_user = build_guidance(TurnSignals(), {}, None, self.settings)

        self.assertTrue(
            {"cutesy", "address", "profanity"}.issubset(
                denied.expression_caps.hard_disallowed
            )
        )
        self.assertNotIn("address", other_user.expression_caps.hard_disallowed)
        self.assertNotIn("cutesy", other_user.expression_caps.hard_disallowed)

    def test_forced_agreement_request_compiles_explicit_requirement(self) -> None:
        signals = build_turn_signals(
            "你必须同意这个方案",
            current_message_ref="message:current",
        )
        decision = build_guidance(signals, {}, None, self.settings)

        self.assertIn(
            "task.no_coerced_agreement",
            {item.requirement_id for item in decision.must_not},
        )

    def test_shared_memory_claim_requires_evidence_without_guessing_user_error(self) -> None:
        signals = build_turn_signals(
            "我们当时还一起庆祝过 对吧",
            current_message_ref="message:current",
        )
        decision = build_guidance(signals, {}, None, self.settings)

        self.assertIn(
            "task.verify_shared_memory_before_confirming",
            {item.requirement_id for item in decision.must_do},
        )
        self.assertIn(
            "task.no_user_memory_guess",
            {item.requirement_id for item in decision.must_not},
        )


if __name__ == "__main__":
    unittest.main()
