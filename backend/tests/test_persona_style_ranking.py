from __future__ import annotations

import unittest

from hanser_agent.persona.policy import build_guidance
from hanser_agent.persona.schemas import BehaviorDecision, EffectivePersonaSettings, PersonaAffordance
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
        decision = BehaviorDecision(
            persona_affordances=[
                PersonaAffordance(
                    id="light_contextual_tease", weight=1.0, guidance="轻吐槽"
                )
            ]
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

    def test_recent_example_penalty_changes_fixed_ranking_component(self) -> None:
        decision = BehaviorDecision()
        candidate = {
            "id": "recent",
            "index_generation": "v2",
            "review_status": "approved",
            "schema_review_status": "approved",
            "speaker_status": "transcript_verified",
            "provenance_kind": "verbatim",
            "payload_class": "reaction_only",
            "group_id": "g1",
            "dense_score": 0.8,
            "quality_score": 0.9,
            "authenticity_score": 0.9,
        }
        result = rank_fixed_style_candidates(
            [candidate], decision,
            response_mode="casual", active_generation="v2",
            recent_example_ids={"recent"}, repetition_penalty=0.4,
        )
        components = result["selected"][0]["score_components"]
        self.assertEqual(components["recent_example_penalty"], -0.2)

    def test_expression_opportunity_promotes_matching_reviewed_example(self) -> None:
        decision = BehaviorDecision(
            persona_affordances=[
                PersonaAffordance(id="light_profanity_release", weight=0.6)
            ]
        )
        base = {
            "index_generation": "v2",
            "review_status": "approved",
            "schema_review_status": "approved",
            "speaker_status": "transcript_verified",
            "provenance_kind": "verbatim",
            "payload_class": "reaction_only",
            "response_mode": "casual",
            "quality_score": 0.9,
            "authenticity_score": 0.9,
        }
        result = rank_fixed_style_candidates(
            [
                {
                    **base,
                    "id": "plain",
                    "group_id": "g1",
                    "dense_score": 0.8,
                    "expression_tags": [],
                },
                {
                    **base,
                    "id": "profanity",
                    "group_id": "g2",
                    "dense_score": 0.3,
                    "behavior_tags": ["light_profanity_release"],
                    "expression_tags": ["profanity"],
                },
            ],
            decision,
            response_mode="casual",
            active_generation="v2",
            top_k=1,
        )

        self.assertEqual(result["selected"][0]["id"], "profanity")


if __name__ == "__main__":
    unittest.main()
