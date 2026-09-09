from __future__ import annotations

import unittest

from hanser_agent.models import ChatMessage
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

        self.assertTrue(signals.observed_bool("explicit_stop"))
        self.assertEqual(signals.get("explicit_stop").source, "rule")

    def test_quoted_stop_is_not_current_user_revocation(self) -> None:
        signals = build_turn_signals(
            "他说“别开玩笑” 然后自己笑得最大声",
            current_message_ref="message:current",
        )

        self.assertTrue(signals.observed_bool("quoted_or_hypothetical"))
        self.assertIsNone(signals.observed_bool("disable_humor"))
        self.assertIsNone(signals.observed_bool("explicit_stop"))

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

    def test_unresolved_reference_is_not_inferred_from_surface_template(self) -> None:
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

        self.assertIsNone(missing.observed_bool("unresolved_reference"))
        self.assertIsNone(resolved.observed_bool("unresolved_reference"))

    def test_unresolved_reference_persists_until_referent_is_defined(self) -> None:
        pending = build_turn_signals(
            "这个方案先只说数据风险",
            current_message_ref="message:current",
            history_messages=[
                ChatMessage(role="assistant", content="你指的是哪个方案"),
            ],
        )
        resolved = build_turn_signals(
            "这个方案先只说数据风险",
            current_message_ref="message:current",
            history_messages=[
                ChatMessage(role="assistant", content="你指的是哪个方案"),
                ChatMessage(role="user", content="方案是把旧库迁移到新库"),
            ],
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

    def test_daily_dialogue_functions_cover_confirmation_correction_advice_and_end(self) -> None:
        examples = {
            "对，就是先把蒜炒香": "confirm",
            "展馆周一闭馆，你刚才记错了": "correct",
            "她很难受，我不知道怎么接": "advice",
            "雨停了，我也该出门了": "end",
        }
        for message, expected in examples.items():
            with self.subTest(message=message):
                signals = build_turn_signals(message, current_message_ref="message:current")
                self.assertEqual(signals.get("dialogue_function").value, expected)

    def test_explicit_adult_status_is_rule_level_but_planner_status_is_not(self) -> None:
        explicit = build_turn_signals(
            "都是成年人了，这个双关可以接一下",
            current_message_ref="message:current",
        )
        inferred = build_turn_signals(
            "雨停了，我也该出门了",
            current_message_ref="message:current",
            planner_payload={
                "audience_age_status": {
                    "value": "adult",
                    "confidence": "high",
                    "evidence_refs": ["current_user"],
                }
            },
        )

        self.assertEqual(explicit.get("audience_age_status").value, "adult")
        self.assertTrue(explicit.get("audience_age_status").hard_rule_eligible)
        self.assertIsNone(inferred.get("audience_age_status").value)
        self.assertFalse(inferred.get("audience_age_status").hard_rule_eligible)
        minor = build_turn_signals(
            "我未成年，这类内容不要展开",
            current_message_ref="message:current",
        )
        self.assertEqual(minor.get("audience_age_status").value, "minor")
        self.assertTrue(minor.get("audience_age_status").hard_rule_eligible)

    def test_quiet_company_is_current_turn_no_advice(self) -> None:
        signals = build_turn_signals(
            "不用解决，我只是想有人听我说完",
            current_message_ref="message:current",
        )

        self.assertTrue(signals.hard_bool("no_advice"))
        self.assertEqual(signals.get("no_advice").scope, "current_turn")

    def test_playful_frame_uses_overt_current_message_cues(self) -> None:
        playful = build_turn_signals(
            "草莓蛋糕很可靠——我知道这句话很怪",
            current_message_ref="message:current",
        )
        plain = build_turn_signals(
            "草莓蛋糕很可靠",
            current_message_ref="message:current",
        )

        self.assertTrue(playful.observed_bool("playful_frame"))
        self.assertIsNone(plain.observed_bool("playful_frame"))


if __name__ == "__main__":
    unittest.main()
