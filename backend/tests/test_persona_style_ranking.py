from __future__ import annotations

import unittest

from hanser_agent.persona.policy import build_guidance
from hanser_agent.persona.schemas import EffectivePersonaSettings
from hanser_agent.persona.signals import build_turn_signals
from hanser_agent.persona.style_ranking import rank_fixed_style_candidates


class PersonaStyleRankingTests(unittest.TestCase):
    def test_v2_hard_filters_precede_behavior_ranking(self) -> None:
        signals = build_turn_signals(
            "最后一秒又掉下去了",
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
            {"humor": "allow"},
            None,
            EffectivePersonaSettings(),
            response_mode="playful",
        )
        base = {
            "index_generation": "v2",
            "review_status": "approved",
            "schema_review_status": "approved",
            "speaker_status": "transcript_verified",
            "provenance_kind": "verbatim",
            "payload_class": "reaction_only",
            "response_mode": "playful",
            "expression_tags": [],
            "quality_score": 0.9,
            "authenticity_score": 0.95,
        }
        result = rank_fixed_style_candidates(
            [
                {
                    **base,
                    "id": "behavior-match",
                    "group_id": "g1",
                    "behavior_tags": ["light_contextual_tease"],
                    "dense_score": 0.72,
                },
                {
                    **base,
                    "id": "plain",
                    "group_id": "g2",
                    "behavior_tags": [],
                    "dense_score": 0.76,
                },
                {
                    **base,
                    "id": "pending-high",
                    "group_id": "g3",
                    "schema_review_status": "pending",
                    "behavior_tags": ["light_contextual_tease"],
                    "dense_score": 1.0,
                },
            ],
            decision,
            response_mode="playful",
            active_generation="v2",
        )

        self.assertEqual(result["selected"][0]["id"], "behavior-match")
        self.assertEqual(result["excluded"]["pending-high"], "not_v2_approved")

    def test_empty_eligible_set_does_not_fall_back(self) -> None:
        decision = build_guidance(
            build_turn_signals("晚上好", current_message_ref="message:current"),
            {},
            None,
            EffectivePersonaSettings(),
        )
        result = rank_fixed_style_candidates(
            [
                {
                    "id": "legacy-pending",
                    "index_generation": "legacy-v1",
                    "review_status": "pending",
                    "schema_review_status": "pending",
                    "speaker_status": "unknown",
                    "group_id": "legacy",
                    "provenance_kind": "legacy_unknown",
                    "payload_class": "unreviewed",
                }
            ],
            decision,
            response_mode="casual",
            active_generation="v2",
        )

        self.assertEqual(result["selected"], [])


if __name__ == "__main__":
    unittest.main()
