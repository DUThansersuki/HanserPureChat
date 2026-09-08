from __future__ import annotations

import unittest

from hanser_agent.persona.signals import build_turn_signals


class PersonaSignalsTests(unittest.TestCase):
    def test_current_explicit_stop_overrides_planner_playful_signal(self) -> None:
        signals = build_turn_signals(
            "这个玩笑让我不舒服 别逗我了",
            current_message_ref="message:current",
            planner_payload={
                "playful_frame": {
                    "value": True,
                    "confidence": "high",
                    "evidence_refs": ["current_user"],
                    "source": "rule",
                }
            },
        )

        self.assertTrue(signals.observed_bool("disable_humor"))
        self.assertTrue(signals.observed_bool("explicit_stop"))
        self.assertEqual(signals.get("disable_humor").source, "rule")
        self.assertEqual(signals.get("playful_frame").status, "conflicted")
        self.assertIn("signal_conflict:playful_frame:explicit_disable", signals.degraded_reasons)

    def test_quoted_stop_is_not_current_user_revocation(self) -> None:
        signals = build_turn_signals(
            "他说“别开玩笑” 然后自己笑得最大声",
            current_message_ref="message:current",
        )

        self.assertTrue(signals.observed_bool("quoted_or_hypothetical"))
        self.assertIsNone(signals.observed_bool("disable_humor"))
        self.assertIsNone(signals.observed_bool("explicit_stop"))
        self.assertIn("rule_scope_unknown:disable_humor", signals.degraded_reasons)

    def test_one_invalid_planner_field_does_not_remove_valid_field(self) -> None:
        signals = build_turn_signals(
            "这个也太离谱了",
            current_message_ref="message:current",
            planner_payload={
                "playful_frame": {
                    "value": True,
                    "confidence": "medium",
                    "evidence_refs": ["current_user"],
                },
                "distress": {
                    "value": "definitely",
                    "confidence": "high",
                    "evidence_refs": ["current_user"],
                },
                "invented": {"value": True},
            },
        )

        self.assertTrue(signals.observed_bool("playful_frame"))
        self.assertIsNone(signals.observed_bool("distress"))
        self.assertIn("planner_signal_invalid:distress:value", signals.degraded_reasons)
        self.assertIn("planner_signal_unknown:invented", signals.degraded_reasons)

    def test_unknown_evidence_reference_degrades_only_that_signal(self) -> None:
        signals = build_turn_signals(
            "普通消息",
            current_message_ref="message:current",
            planner_payload={
                "playful_frame": {
                    "value": True,
                    "confidence": "high",
                    "evidence_refs": ["history_-9"],
                }
            },
        )

        self.assertIsNone(signals.observed_bool("playful_frame"))
        self.assertIn(
            "planner_signal_invalid:playful_frame:unknown_evidence_ref",
            signals.degraded_reasons,
        )

    def test_forced_agreement_request_is_current_turn_signal(self) -> None:
        signals = build_turn_signals(
            "我这个方案肯定没问题 你只要说对",
            current_message_ref="message:current",
        )

        self.assertTrue(signals.observed_bool("forced_agreement_request"))

    def test_ordinary_confirmation_question_is_not_forced_agreement(self) -> None:
        signals = build_turn_signals(
            "先确认影响范围 对不对",
            current_message_ref="message:current",
        )

        self.assertIsNone(signals.observed_bool("forced_agreement_request"))

    def test_unresolved_reference_requires_missing_history_object(self) -> None:
        missing = build_turn_signals(
            "认真问一下这个方案有什么风险",
            current_message_ref="message:current",
            history_texts=["你刚才那个反问挺可爱的"],
        )
        resolved = build_turn_signals(
            "这个方案有什么风险",
            current_message_ref="message:current",
            history_texts=["方案是把生产数据直接迁移到新库"],
        )

        self.assertTrue(missing.observed_bool("unresolved_reference"))
        self.assertIsNone(resolved.observed_bool("unresolved_reference"))

    def test_unresolved_reference_persists_until_referent_is_defined(self) -> None:
        pending = build_turn_signals(
            "先只说数据风险",
            current_message_ref="message:current",
            history_texts=["你指的是哪个方案", "还没说具体内容"],
        )
        resolved = build_turn_signals(
            "先只说数据风险",
            current_message_ref="message:current",
            history_texts=["你指的是哪个方案", "方案是把旧库迁移到新库"],
        )

        self.assertTrue(pending.observed_bool("unresolved_reference"))
        self.assertIsNone(resolved.observed_bool("unresolved_reference"))

    def test_unverified_shared_memory_claim_is_narrowly_detected(self) -> None:
        claimed = build_turn_signals(
            "我们当时还一起庆祝过 对吧",
            current_message_ref="message:current",
        )
        quoted = build_turn_signals(
            "她说“我们当时还一起庆祝过”",
            current_message_ref="message:current",
        )

        self.assertTrue(claimed.observed_bool("unverified_shared_memory_claim"))
        self.assertIsNone(quoted.observed_bool("unverified_shared_memory_claim"))


if __name__ == "__main__":
    unittest.main()
