from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from hanser_agent.models import ChatMessage
from hanser_agent.persona.expression import observe_recent_expressions
from hanser_agent.persona.policy import build_guidance
from hanser_agent.persona.schemas import BehaviorPrior, EffectivePersonaSettings, TurnSignals
from hanser_agent.persona.signals import build_turn_signals


class PersonaPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = EffectivePersonaSettings()
        path = (
            Path(__file__).resolve().parents[1]
            / "hanser_agent" / "prompts" / "persona" / "candidates"
            / "hanser-persona-v2-candidate" / "behavior.yaml"
        )
        self.cards = [
            BehaviorPrior.model_validate(item)
            for item in yaml.safe_load(path.read_text(encoding="utf-8"))["priors"]
        ]

    def test_unknown_signals_do_not_disable_all_natural_humor(self) -> None:
        decision = build_guidance(
            TurnSignals(), {}, None, self.settings,
        )

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

        self.assertIn("teasing", decision.expression_caps.hard_disallowed)
        self.assertIn("user.explicit_stop", decision.boundary_ids)
        self.assertNotIn("light_contextual_tease", [item.id for item in decision.persona_affordances])

    def test_planner_only_explicit_stop_cannot_create_hard_rule(self) -> None:
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

        self.assertNotIn(
            "user.explicit_stop",
            {item.requirement_id for item in decision.must_not},
        )
        self.assertNotIn("teasing", decision.expression_caps.hard_disallowed)

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
        self.assertIn("innuendo", allowed.expression_caps.hard_disallowed)

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

    def test_dialogue_card_changes_only_relevant_turn(self) -> None:
        share = build_guidance(
            build_turn_signals("今天捡到一片很好看的叶子", current_message_ref="m"),
            {}, None, self.settings, behavior_priors=self.cards,
        )
        correction = build_guidance(
            build_turn_signals("我说错了，其实是周日", current_message_ref="m"),
            {}, None, self.settings, behavior_priors=self.cards,
        )
        self.assertIn("dialogue.share_without_unsolicited_solution.v1", share.selected_prior_ids)
        self.assertNotIn("dialogue.accept_correction.v1", share.selected_prior_ids)
        self.assertIn("dialogue.accept_correction.v1", correction.selected_prior_ids)

    def test_warmth_parameter_changes_support_affordance_weight(self) -> None:
        signals = build_turn_signals("我真的很难受", current_message_ref="m")
        low = build_guidance(
            signals, {}, None, self.settings.model_copy(update={"warmth": 0.3}),
            behavior_priors=self.cards,
        )
        high = build_guidance(
            signals, {}, None, self.settings.model_copy(update={"warmth": 0.8}),
            behavior_priors=self.cards,
        )
        def weight(decision):
            return next(item.weight for item in decision.persona_affordances if item.id == "acknowledge_specific_distress")
        self.assertLess(weight(low), weight(high))


if __name__ == "__main__":
    unittest.main()
