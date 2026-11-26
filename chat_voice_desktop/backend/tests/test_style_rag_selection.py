from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hanser_agent import db
from hanser_agent.agent.context_builder import ContextBuilder
from hanser_agent.agent.tools.style_search import StyleSearchTool
from hanser_agent.models import DialoguePlan, StyleExample
from hanser_agent.persona.schemas import (
    BehaviorDecision,
    PersonaAffordance,
    SignalObservation,
    TurnSignals,
)
from hanser_agent.persona.signals import build_turn_signals
from hanser_agent.persona.style_applicability import (
    has_specific_behavior_applicability,
    style_applicability_exclusion_reason,
)
from hanser_agent.persona.style_eligibility import style_exclusion_reason
from hanser_agent.persona.style_ranking import rank_fixed_style_candidates
from hanser_agent.persona.compiler import PersonaCompiler
from hanser_agent.retrieval.style_lexical import (
    StyleLexicalIndex,
    StyleLexicalMatch,
    score_style_fields,
)
from hanser_agent.responder.validator import StyleValidator
from hanser_agent.responder.service import HanserResponder
from scripts.run_style_v3_full_corpus import materialize_units
from scripts.run_style_v3_pilot import RangeExtractionResult


def candidate(**changes):
    item = {
        "id": "style:1", "index_generation": "g1", "review_status": "approved",
        "schema_review_status": "approved", "quality_score": 0.9,
        "authenticity_score": 0.9, "source_tier": "primary", "source_type": "real",
        "source_speaker": "hanser", "source_user_turn": "睡不着",
        "source_response_turn": "那就先躺一会", "character_response": "那就先躺一会",
        "runtime_scope": ["style_runtime"],
        "speaker_status": "transcript_verified", "provenance_kind": "verbatim",
        "payload_class": "reaction_only", "group_id": "group-1", "hidden_eval": False,
        "expression_tags": [], "behavior_tags": ["direct_natural_reply"], "audience": "individual",
        "response_mode": "casual", "content_relevance": 0.8,
        "runtime_payload_kind": "pattern_only", "runtime_reaction_fragment": None,
        "runtime_payload_reviewed": True,
    }
    item.update(changes)
    return item


class StyleRagSelectionTests(unittest.IsolatedAsyncioTestCase):
    def test_source_unit_id_is_stable_and_range_round_trips(self) -> None:
        window = {
            "window_id": "DOC-0001-W0001", "source_id": "DOC-0001",
            "document_id": 1, "filename": "demo.docx", "source_start_char": 100,
            "source_span": "弹幕：好耶\n憨：确实很好",
        }
        segments = {
            "S001": {"segment_id": "S001", "start": 0, "end": 6, "length": 6,
                     "speaker": "audience", "text": "弹幕：好耶\n"},
            "S002": {"segment_id": "S002", "start": 6, "end": 13, "length": 7,
                     "speaker": "hanser", "text": "憨：确实很好"},
        }
        result = RangeExtractionResult.model_validate({
            "units": [{
                "source_user_span": {"segment_id": "S001", "start": 3, "end": 5},
                "response_span": {"segment_id": "S002", "start": 2, "end": 6},
                "trigger_basis": "explicit", "trigger_evidence_segment_ids": ["S001"],
                "intervening_segment_ids": [], "relation_basis": "直接回应",
                "retrieval_description": "回应好消息", "behavior_tags": ["acknowledge_shared_detail"],
                "payload_class": "reaction_only", "transfer_scope": "general",
                "requires_all": [], "forbids_any": [], "audience": "individual",
                "extraction_notes": "exact",
            }],
            "no_unit_reason": None,
        })
        rows, failures = materialize_units({
            "window": window, "segments": segments, "result": result,
        })
        self.assertEqual(failures, [])
        self.assertEqual(rows[0]["character_response"], "确实很好")
        self.assertEqual(rows[0]["response_source_start_char"], 108)
        rows_again, _ = materialize_units({
            "window": {**window, "window_id": "overlap-copy"},
            "segments": segments, "result": result,
        })
        self.assertEqual(rows[0]["unit_id"], rows_again[0]["unit_id"])

    def test_verified_a_only_contract_and_assumed_runtime_boundary(self) -> None:
        item = candidate(
            style_record_version=3, unit_id="a-only", source_episode_id="episode-1",
            source_user_turn=None, trigger_basis="self_initiated",
            retrieval_description="", transfer_scope="general",
            applicability={"requires_all": [], "forbids_any": []},
            projection_schema_version="source-reaction-unit-projection-v1",
            q_projection=None,
            a_projection={
                "review_status": "approved", "behavior_ids": ["acknowledge_shared_detail"],
                "mechanism_description": "承接结果", "compatibility_features": {},
                "evidence_refs": ["source:1"],
            },
            speaker_candidate="hanser", attribution_status="verified",
        )
        self.assertIsNone(style_exclusion_reason(item, active_generation="g1"))
        self.assertEqual(
            style_exclusion_reason(
                {**item, "attribution_status": "assumed"}, active_generation="g1"
            ),
            "speaker_attribution_not_verified",
        )

    def test_m1_template_is_independent_of_style_presence(self) -> None:
        signals = build_turn_signals(
            "我过了", current_message_ref="message:1", planner_payload={
                "dialogue_function": {"value": "share", "confidence": "high", "evidence_refs": ["current_user"]},
                "interaction_frame": {"value": "achievement_share", "confidence": "high", "evidence_refs": ["current_user"]},
                "interaction_target": {"value": "current_user", "confidence": "high", "evidence_refs": ["current_user"]},
            },
        )
        base = dict(exact_output=None, required_verbatim_spans=[], turn_signals=signals)
        without_style = SimpleNamespace(**base, injected_style_ids=[])
        with_style = SimpleNamespace(**base, injected_style_ids=["style:1"])
        self.assertEqual(
            HanserResponder._bounded_reaction_text(without_style, generation_policy="M1"),
            HanserResponder._bounded_reaction_text(with_style, generation_policy="M1"),
        )

    def test_runtime_and_fixed_evaluation_share_each_hard_gate(self) -> None:
        mutations = {
            "outer_review_not_approved": {"review_status": "rejected"},
            "schema_review_not_approved": {"schema_review_status": "rejected"},
            "generation_mismatch": {"index_generation": "old"},
            "source_speaker_mismatch": {"source_speaker": "unknown"},
            "runtime_scope_missing": {"runtime_scope": []},
            "hidden_eval_group": {"hidden_eval": True},
            "payload_not_runtime_safe": {"payload_class": "third_party_fact"},
            "provenance_not_runtime_eligible": {"provenance_kind": "generated"},
        }
        for expected, changes in mutations.items():
            with self.subTest(expected=expected):
                self.assertEqual(
                    style_exclusion_reason(candidate(**changes), active_generation="g1"),
                    expected,
                )

    def test_verbatim_cleaning_operations_must_replay_exactly(self) -> None:
        base = candidate(
            style_record_version=3,
            unit_id="cleaned-unit",
            source_episode_id="episode-1",
            source_response_turn="12:34 Hanser：  好呀",
            character_response="好呀",
            trigger_basis="explicit",
            trigger_evidence_refs=["source:1"],
            retrieval_description="简短接话",
            transfer_scope="general",
            applicability={"requires_all": [], "forbids_any": []},
            cleaning_operations=[
                "remove_timestamp", "remove_hanser_prefix", "normalize_whitespace",
            ],
        )
        self.assertIsNone(style_exclusion_reason(base, active_generation="g1"))
        self.assertEqual(
            style_exclusion_reason(
                {**base, "cleaning_operations": ["unreviewed_rewrite"]},
                active_generation="g1",
            ),
            "verbatim_response_mismatch",
        )
        self.assertEqual(
            style_exclusion_reason(
                {**base, "character_response": "好的"}, active_generation="g1"
            ),
            "verbatim_response_mismatch",
        )

    def test_recent_candidate_is_soft_penalized_not_excluded(self) -> None:
        result = rank_fixed_style_candidates(
            [candidate()], BehaviorDecision(), response_mode="casual",
            active_generation="g1", recent_example_ids={"style:1"},
        )
        self.assertEqual(result["selected"][0]["id"], "style:1")
        self.assertLess(result["selected"][0]["score_components"]["recent_example_penalty"], 0)

    def test_v3_inferred_reaction_needs_no_fake_user_turn(self) -> None:
        item = candidate(
            style_record_version=3,
            unit_id="unit-1",
            source_episode_id="episode-1",
            source_user_turn=None,
            trigger_basis="inferred",
            trigger_evidence_refs=["source:10:20"],
            retrieval_description="适合回应轻微紧张",
            transfer_scope="general",
            applicability={"requires_all": [], "forbids_any": []},
        )
        self.assertIsNone(style_exclusion_reason(item, active_generation="g1"))
        item["source_user_turn"] = "用户当时说了某句话"
        self.assertEqual(
            style_exclusion_reason(item, active_generation="g1"),
            "inferred_trigger_in_source_user_turn",
        )

    def test_required_context_uses_observed_medium_confidence_only(self) -> None:
        item = candidate(
            style_record_version=3,
            unit_id="unit-1",
            source_episode_id="episode-1",
            trigger_basis="self_initiated",
            source_user_turn=None,
            retrieval_description="适合玩笑式接锅",
            transfer_scope="requires_context",
            applicability={
                "requires_all": [{"field": "playful_frame", "one_of": [True]}],
                "forbids_any": [{"field": "distress", "one_of": [True]}],
            },
        )
        unknown = TurnSignals(values={
            "playful_frame": SignalObservation(
                name="playful_frame", value=True, source="planner",
                confidence="low", status="observed", evidence_refs=["message:1"],
            )
        })
        reason, details = style_applicability_exclusion_reason(item, unknown)
        self.assertEqual(reason, "required_context_unknown")
        self.assertEqual(details[0]["evidence_refs"], ["message:1"])

        matched = TurnSignals(values={
            "playful_frame": SignalObservation(
                name="playful_frame", value=True, source="planner",
                confidence="medium", status="observed", evidence_refs=["message:1"],
            ),
            "distress": SignalObservation(
                name="distress", value=False, source="rule",
                confidence="high", status="observed", evidence_refs=["message:1"],
            ),
        })
        self.assertIsNone(style_applicability_exclusion_reason(item, matched)[0])

    def test_enum_unknown_is_not_positive_applicability_evidence(self) -> None:
        item = candidate(
            style_record_version=3, unit_id="unit-unknown", source_episode_id="episode-1",
            trigger_basis="explicit", trigger_evidence_refs=["source:1"],
            retrieval_description="对象未知", transfer_scope="requires_context",
            applicability={
                "requires_all": [{"field": "interaction_target", "one_of": ["assistant"]}],
                "forbids_any": [],
            },
        )
        signals = TurnSignals(values={
            "interaction_target": SignalObservation(
                name="interaction_target", value="unknown", source="planner",
                confidence="medium", status="observed", evidence_refs=["message:1"],
            )
        })
        reason, checks = style_applicability_exclusion_reason(item, signals)
        self.assertEqual(reason, "required_context_unknown")
        self.assertEqual(checks[0]["outcome"], "unknown")

    def test_partial_required_forbidden_overlap_keeps_remaining_value(self) -> None:
        item = candidate(
            style_record_version=3, unit_id="unit-overlap", source_episode_id="episode-1",
            trigger_basis="explicit", trigger_evidence_refs=["source:1"],
            retrieval_description="夸奖或成果", transfer_scope="requires_context",
            applicability={
                "requires_all": [{"field": "interaction_frame", "one_of": ["praise", "achievement_share"]}],
                "forbids_any": [{"field": "interaction_frame", "one_of": ["praise"]}],
            },
        )
        self.assertIsNone(style_exclusion_reason(item, active_generation="g1"))
        matched = TurnSignals(values={
            "interaction_frame": SignalObservation(
                name="interaction_frame", value="achievement_share", source="planner",
                confidence="high", status="observed", evidence_refs=["message:1"],
            )
        })
        self.assertIsNone(style_applicability_exclusion_reason(item, matched)[0])

    def test_unknown_cannot_be_authored_as_required_value(self) -> None:
        item = candidate(
            style_record_version=3, unit_id="unit-bad-unknown", source_episode_id="episode-1",
            trigger_basis="explicit", trigger_evidence_refs=["source:1"],
            retrieval_description="未知对象", transfer_scope="requires_context",
            applicability={
                "requires_all": [{"field": "interaction_target", "one_of": ["unknown"]}],
                "forbids_any": [],
            },
        )
        self.assertEqual(
            style_exclusion_reason(item, active_generation="g1"),
            "applicability_unknown_not_assertable",
        )

    def test_interaction_observations_are_accepted_from_planner(self) -> None:
        signals = build_turn_signals(
            "哈哈，都怪你",
            current_message_ref="message:1",
            planner_payload={
                "interaction_frame": {"value": "playful_blame", "confidence": "high", "evidence_refs": ["current_user"]},
                "interaction_target": {"value": "assistant", "confidence": "high", "evidence_refs": ["current_user"]},
            },
        )
        self.assertEqual(signals.get("interaction_frame").value, "playful_blame")
        self.assertEqual(signals.get("interaction_target").value, "assistant")

    def test_playful_blame_frame_implies_playful_context_observation(self) -> None:
        signals = build_turn_signals(
            "neutral input",
            current_message_ref="message:1",
            planner_payload={
                "interaction_frame": {
                    "value": "playful_blame",
                    "confidence": "medium",
                    "evidence_refs": ["current_user"],
                },
            },
        )
        self.assertIs(signals.get("playful_frame").value, True)
        self.assertEqual(signals.get("playful_frame").source, "planner")

    def test_v3_runtime_serialization_exposes_pattern_not_source_proposition(self) -> None:
        item = StyleExample.model_validate({
            **candidate(
                style_record_version=3,
                unit_id="unit-1",
                source_episode_id="episode-1",
                source_user_turn=None,
                trigger_basis="inferred",
                trigger_evidence_refs=["source:10:20"],
                retrieval_description="适合安抚轻微紧张",
                transfer_scope="general",
                applicability={"requires_all": [], "forbids_any": []},
                behavior_tags=["brief_contextual_uptake"],
                runtime_payload_kind="pattern_only",
                runtime_payload_reviewed=True,
            ),
            "user_context": "",
            "character_response": "没事啦，先缓一缓",
            "context_before": "",
            "context_after": "",
            "scene": "comfort",
            "speech_act": "reassure",
            "tone": [],
            "answer_length": "short",
        })
        rendered = ContextBuilder._style_item(object.__new__(ContextBuilder), item, 1)
        self.assertNotIn("<source_user>", rendered)
        self.assertNotIn("适合安抚轻微紧张", rendered)
        self.assertNotIn("没事啦，先缓一缓", rendered)
        self.assertIn("<reaction_pattern", rendered)
        self.assertIn("brief_contextual_uptake", rendered)

    def test_v3_reviewed_fragment_must_be_grounded_in_source_reaction(self) -> None:
        item = candidate(
            style_record_version=3, unit_id="fragment", source_episode_id="episode-1",
            trigger_basis="explicit", trigger_evidence_refs=["source:1"],
            retrieval_description="简短认错", transfer_scope="general",
            applicability={"requires_all": [], "forbids_any": []},
            behavior_tags=["brief_contextual_uptake"],
            runtime_payload_kind="reviewed_fragment",
            runtime_reaction_fragment="先这样",
        )
        self.assertEqual(
            style_exclusion_reason(item, active_generation="g1"),
            "runtime_fragment_not_source_grounded",
        )

    def test_correction_runtime_payload_is_pattern_only(self) -> None:
        item = candidate(
            style_record_version=3, unit_id="correction", source_episode_id="episode-1",
            trigger_basis="explicit", trigger_evidence_refs=["source:1"],
            retrieval_description="简短接受纠正", transfer_scope="requires_context",
            applicability={
                "requires_all": [{"field": "dialogue_function", "one_of": ["correct"]}],
                "forbids_any": [],
            },
            behavior_tags=["brief_contextual_uptake"],
            runtime_payload_kind="reviewed_fragment",
            runtime_reaction_fragment="那就改一下",
            character_response="那就改一下",
            source_response_turn="那就改一下",
        )
        self.assertEqual(
            style_exclusion_reason(item, active_generation="g1"),
            "correction_payload_must_be_pattern_only",
        )

    def test_correction_semantic_contract_prioritizes_current_user_update(self) -> None:
        plan = DialoguePlan(
            intent="chitchat", need_wiki=False, standalone_query="x", keywords=[],
            response_mode="casual", fact_sensitivity="low", target_length="short",
            dialogue_function="correct",
        )
        contract = ContextBuilder._turn_semantic_contract(plan)
        self.assertIn("premise_update=current_user_authoritative", contract)
        self.assertIn("ambiguity_policy=invariant", contract)
        self.assertIn("content_strategy=acknowledge_update_without_paraphrase", contract)

    def test_achievement_semantic_contract_limits_content_to_explicit_outcome(self) -> None:
        plan = DialoguePlan(
            intent="chitchat", need_wiki=False, standalone_query="x", keywords=[],
            response_mode="casual", fact_sensitivity="low", target_length="short",
            dialogue_function="share",
            persona_signals={"interaction_frame": {"value": "achievement_share"}},
        )
        contract = ContextBuilder._turn_semantic_contract(plan)
        self.assertIn("content_strategy=acknowledge_explicit_outcome", contract)
        self.assertIn("allowed_content_slots=explicit_outcome_or_progress", contract)
        self.assertIn("question_policy=none", contract)
        self.assertIn("不推测此前尝试次数", contract)

    def test_unresolved_interpretation_overrides_specific_content_strategy(self) -> None:
        plan = DialoguePlan(
            intent="chitchat", need_wiki=False, standalone_query="x", keywords=[],
            response_mode="casual", fact_sensitivity="low", target_length="short",
            dialogue_function="share",
            persona_signals={
                "interaction_frame": {"value": "achievement_share"},
                "unresolved_reference": {"value": True},
            },
        )
        contract = ContextBuilder._turn_semantic_contract(plan)
        self.assertIn("content_strategy=ambiguity_invariant_only", contract)
        self.assertNotIn("content_strategy=acknowledge_explicit_outcome", contract)

    def test_achievement_question_is_rejected_by_semantic_validator(self) -> None:
        signals = build_turn_signals(
            "neutral input",
            current_message_ref="message:1",
            planner_payload={
                "interaction_frame": {
                    "value": "achievement_share",
                    "confidence": "high",
                    "evidence_refs": ["current_user"],
                },
            },
        )
        constraints = Path(__file__).parents[1] / "hanser_agent" / "prompts" / "persona" / "candidates" / "hanser-persona-v2-candidate" / "style_constraints.yaml"
        result = StyleValidator(constraints).validate_semantic_output(
            "挺好的，之前发生过什么？",
            turn_signals=signals,
        )
        self.assertIn("achievement_followup_not_grounded", result.violations)
        long_result = StyleValidator(constraints).validate_semantic_output(
            "挺好的，这个结果确实值得认真庆祝一下，之后还可以继续详细聊聊。",
            turn_signals=signals,
        )
        self.assertIn("achievement_reaction_overelaborated", long_result.violations)

    def test_semantic_reaction_fallback_contains_no_event_proposition(self) -> None:
        self.assertEqual(
            HanserResponder._semantic_reaction_fallback(
                ["achievement_reaction_overelaborated"]
            ),
            "这不是很好嘛 恭喜啊",
        )

    def test_reaction_only_realizer_uses_frame_not_user_words(self) -> None:
        signals = build_turn_signals(
            "neutral input",
            current_message_ref="message:1",
            planner_payload={
                "dialogue_function": {
                    "value": "share", "confidence": "high", "evidence_refs": ["current_user"]
                },
                "interaction_frame": {
                    "value": "achievement_share", "confidence": "high", "evidence_refs": ["current_user"]
                },
                "interaction_target": {
                    "value": "current_user", "confidence": "high", "evidence_refs": ["current_user"]
                },
            },
        )
        context = SimpleNamespace(
            exact_output=None,
            required_verbatim_spans=[],
            turn_signals=signals,
            injected_style_ids=["style:any"],
        )
        self.assertEqual(
            HanserResponder._bounded_reaction_text(context),
            "这不是很好嘛 恭喜啊",
        )

    def test_reaction_only_realizer_respects_third_party_target(self) -> None:
        signals = build_turn_signals(
            "neutral input",
            current_message_ref="message:1",
            planner_payload={
                "dialogue_function": {
                    "value": "share", "confidence": "high", "evidence_refs": ["current_user"]
                },
                "interaction_frame": {
                    "value": "praise", "confidence": "high", "evidence_refs": ["current_user"]
                },
                "interaction_target": {
                    "value": "third_party", "confidence": "high", "evidence_refs": ["current_user"]
                },
            },
        )
        context = SimpleNamespace(
            exact_output=None,
            required_verbatim_spans=[],
            turn_signals=signals,
            injected_style_ids=[],
        )
        self.assertEqual(
            HanserResponder._bounded_reaction_text(context),
            "确实挺好的",
        )

    def test_behavior_channel_can_select_synonym_without_content_overlap(self) -> None:
        item = candidate(content_relevance=0.0, behavior_tags=["listen_without_forcing_advice"])
        decision = BehaviorDecision(persona_affordances=[PersonaAffordance(
            id="listen_without_forcing_advice", guidance="先听", weight=0.8,
        )])
        result = rank_fixed_style_candidates(
            [item], decision, response_mode="casual", active_generation="g1",
        )
        self.assertEqual(result["selected"][0]["id"], "style:1")

    def test_v3_broad_playful_behavior_cannot_select_narrow_reaction_alone(self) -> None:
        item = candidate(
            style_record_version=3, unit_id="guess", source_episode_id="episode-1",
            trigger_basis="explicit", trigger_evidence_refs=["source:1"],
            retrieval_description="一起猜题", transfer_scope="requires_context",
            applicability={
                "requires_all": [{"field": "playful_frame", "one_of": [True]}],
                "forbids_any": [],
            },
            content_relevance=0.0, behavior_tags=["playful_reframe"],
        )
        decision = BehaviorDecision(persona_affordances=[PersonaAffordance(
            id="playful_reframe", guidance="玩笑重构", weight=0.8,
        )])
        result = rank_fixed_style_candidates(
            [item], decision, response_mode="playful", active_generation="g1",
            turn_signals=TurnSignals(values={
                "playful_frame": SignalObservation(
                    name="playful_frame", value=True, source="planner",
                    confidence="high", status="observed", evidence_refs=["message:1"],
                )
            }),
        )
        self.assertEqual(result["selected"], [])
        self.assertEqual(result["excluded"]["style:1"], "behavior_match_too_broad")

    def test_interaction_labels_filter_but_do_not_admit_without_content(self) -> None:
        item = candidate(
            style_record_version=3, unit_id="narrow", source_episode_id="episode-1",
            trigger_basis="explicit", trigger_evidence_refs=["source:1"],
            retrieval_description="某个特定成果", transfer_scope="requires_context",
            applicability={
                "requires_all": [
                    {"field": "interaction_frame", "one_of": ["achievement_share"]},
                    {"field": "interaction_target", "one_of": ["current_user"]},
                ],
                "forbids_any": [],
            },
            content_relevance=0.0,
            behavior_tags=["acknowledge_shared_detail"],
        )
        decision = BehaviorDecision(persona_affordances=[PersonaAffordance(
            id="acknowledge_shared_detail", guidance="承接", weight=0.8,
        )])
        signals = TurnSignals(values={
            name: SignalObservation(
                name=name, value=value, source="planner", confidence="high",
                status="observed", evidence_refs=["message:1"],
            )
            for name, value in {
                "interaction_frame": "achievement_share",
                "interaction_target": "current_user",
            }.items()
        })
        result = rank_fixed_style_candidates(
            [item], decision, response_mode="casual", active_generation="g1",
            turn_signals=signals,
        )
        self.assertEqual(result["selected"], [])
        self.assertEqual(result["excluded"]["style:1"], "behavior_match_too_broad")

    def test_behavior_only_requires_independent_event_role_and_state_evidence(self) -> None:
        item = candidate(
            style_record_version=3,
            applicability={
                "requires_all": [
                    {"field": "interaction_frame", "one_of": ["achievement_share"]},
                    {"field": "interaction_target", "one_of": ["current_user"]},
                    {"field": "user_emotion", "one_of": ["positive"]},
                ],
                "forbids_any": [],
            },
        )
        self.assertTrue(has_specific_behavior_applicability(item))

    def test_behavior_only_accepts_narrow_dialogue_act(self) -> None:
        item = candidate(
            style_record_version=3,
            applicability={
                "requires_all": [
                    {"field": "dialogue_function", "one_of": ["correct"]},
                    {"field": "interaction_target", "one_of": ["assistant"]},
                ],
                "forbids_any": [],
            },
        )
        self.assertTrue(has_specific_behavior_applicability(item))

    def test_behavior_only_accepts_dialogue_act_with_independent_state(self) -> None:
        item = candidate(
            style_record_version=3,
            applicability={
                "requires_all": [
                    {"field": "dialogue_function", "one_of": ["share"]},
                    {"field": "user_emotion", "one_of": ["negative"]},
                ],
                "forbids_any": [],
            },
        )
        self.assertTrue(has_specific_behavior_applicability(item))

    def test_style_lexical_returns_raw_field_evidence(self) -> None:
        matches = score_style_fields("最近失眠", {
            "short": {"user_context": "失眠"},
            "long": {"user_context": "失眠 加班 工作 压力 睡觉"},
        })
        self.assertIn("失眠", matches["short"].meaningful_terms)
        self.assertIn("user_context", matches["short"].field_scores)
        self.assertNotEqual(matches["short"].raw_score, 1.0)

    def test_cached_style_lexical_index_matches_one_shot_scoring(self) -> None:
        documents = {
            "short": {"user_context": "失眠"},
            "long": {"user_context": "失眠 加班 工作 压力 睡觉"},
        }
        expected = score_style_fields("最近失眠", documents)
        actual = StyleLexicalIndex.build(documents).score("最近失眠")
        self.assertEqual(actual, expected)

    def test_hello_is_not_accepted_from_nihaojiu_fragment(self) -> None:
        self.assertFalse(StyleSearchTool._boundary_compatible(
            "你好", {"user_context": "你好久没播了", "source_user_turn": "", "context_before": ""},
        ))

    def test_single_weak_topic_word_does_not_admit_unrelated_reaction(self) -> None:
        match = StyleLexicalMatch(
            raw_score=1.6,
            meaningful_terms=("男生",),
            auxiliary_bigrams=("男生",),
            coverage=0.2,
            field_scores={"trigger_text": 1.6},
        )
        self.assertFalse(StyleSearchTool._content_match_is_admissible(
            "男生不会轻易流露感情",
            {"男生", "不会", "轻易", "流露", "感情"},
            frozenset({"男生", "穿衣"}),
            {"trigger_text": "男生为什么不能穿这种衣服", "retrieval_description": ""},
            match,
        ))

    async def test_revision_change_reloads_corpus_after_emergency_revoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "style.db"
            with db.connect(db_path) as conn:
                db.init_db(conn)
                metadata = json.dumps({
                    "schema_review_status": "approved", "provenance_kind": "verbatim",
                    "payload_class": "reaction_only",
                    "behavior_tags": ["listen_without_forcing_advice"], "expression_tags": [],
                    "audience": "individual", "group_id": "group-1",
                    "speaker_status": "transcript_verified", "runtime_scope": ["style_runtime"],
                }, ensure_ascii=False)
                conn.execute(
                    """INSERT INTO style_examples
                    (prompt,response,context_before,context_after,scene,speech_act,tone_json,
                     answer_length,response_mode,source_type,source_ref,authenticity_score,
                     quality_score,metadata_json,review_status,source_tier,source_speaker,
                     source_user_turn,source_response_turn,index_generation)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    ("睡不着", "那就先躺会", "", "", "casual_chat", "react", "[]",
                     "short", "casual", "real", "fixture", 1.0, 1.0, metadata, "approved",
                     "primary", "hanser", "睡不着", "那就先躺会", "g1"),
                )
                conn.execute(
                    "INSERT INTO active_index_generations VALUES (?,?,?,datetime('now'))",
                    ("style_examples", "g1", 1),
                )
                conn.commit()
            settings = SimpleNamespace(
                db_path=db_path, userdict_path=None,
                style=SimpleNamespace(
                    enabled=True, min_quality=0.5, top_k=3, candidate_pool_size=48,
                ),
            )
            tool = StyleSearchTool(settings=settings, strict_v2=True, pinned_generation="g1")
            plan = DialoguePlan(
                intent="chitchat", need_wiki=False, standalone_query="睡不着", keywords=[],
                response_mode="casual", fact_sensitivity="low", target_length="short",
            )
            first = await tool.search("睡不着", plan)
            self.assertEqual([item.id for item in first.examples], ["style:1"])
            first_index = tool._lexical_index
            both = await tool.search(
                "睡不着", plan,
                behavior_decision=BehaviorDecision(persona_affordances=[PersonaAffordance(
                    id="listen_without_forcing_advice", guidance="先听", weight=0.8,
                )]),
            )
            self.assertEqual([item.id for item in both.examples], ["style:1"])
            self.assertEqual(both.matched_by, {"style:1": ["Q", "A"]})
            behavior_only = await tool.search(
                "你陪我待会",
                plan,
                behavior_decision=BehaviorDecision(persona_affordances=[PersonaAffordance(
                    id="listen_without_forcing_advice", guidance="先听", weight=0.8,
                )]),
            )
            self.assertEqual([item.id for item in behavior_only.examples], ["style:1"])
            self.assertEqual(behavior_only.candidate_counts["behavior_channel"], 1)
            self.assertIs(tool._lexical_index, first_index)
            with db.connect(db_path) as conn:
                conn.execute("UPDATE style_examples SET review_status='rejected' WHERE id=1")
                conn.execute("UPDATE active_index_generations SET revision=2 WHERE collection='style_examples'")
                conn.commit()
            second = await tool.search("睡不着", plan)
            self.assertEqual(second.examples, [])
            self.assertEqual(second.revision, 2)
            self.assertEqual(second.empty_reason, "no_eligible_corpus")
            self.assertIsNot(tool._lexical_index, first_index)

    async def test_behavior_channel_is_ranked_before_pool_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "style.db"
            with db.connect(db_path) as conn:
                db.init_db(conn)
                metadata = json.dumps({
                    "schema_review_status": "approved", "provenance_kind": "verbatim",
                    "payload_class": "reaction_only",
                    "behavior_tags": ["listen_without_forcing_advice"], "expression_tags": [],
                    "audience": "individual", "group_id": "shared-group",
                    "speaker_status": "transcript_verified", "runtime_scope": ["style_runtime"],
                }, ensure_ascii=False)
                for index in range(1, 26):
                    conn.execute(
                        """INSERT INTO style_examples
                        (prompt,response,context_before,context_after,scene,speech_act,tone_json,
                         answer_length,response_mode,source_type,source_ref,authenticity_score,
                         quality_score,metadata_json,review_status,source_tier,source_speaker,
                         source_user_turn,source_response_turn,index_generation)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (f"触发{index}", f"回应{index}", "", "", "casual_chat", "react", "[]",
                         "short", "casual", "real", f"fixture:{index}", 1.0,
                         0.7 + index / 1000, metadata, "approved", "primary", "hanser",
                         f"触发{index}", f"回应{index}", "g1"),
                    )
                conn.execute(
                    "INSERT INTO active_index_generations VALUES (?,?,?,datetime('now'))",
                    ("style_examples", "g1", 1),
                )
                conn.commit()
            settings = SimpleNamespace(
                db_path=db_path, userdict_path=None,
                style=SimpleNamespace(
                    enabled=True, min_quality=0.5, top_k=1, candidate_pool_size=48,
                ),
            )
            tool = StyleSearchTool(settings=settings, strict_v2=True, pinned_generation="g1")
            plan = DialoguePlan(
                intent="chitchat", need_wiki=False, standalone_query="完全无关", keywords=[],
                response_mode="casual", fact_sensitivity="low", target_length="short",
            )
            result = await tool.search(
                "完全无关",
                plan,
                behavior_decision=BehaviorDecision(persona_affordances=[PersonaAffordance(
                    id="listen_without_forcing_advice", guidance="先听", weight=0.8,
                )]),
            )
            self.assertEqual([item.id for item in result.examples], ["style:25"])
            self.assertEqual(result.candidate_counts["behavior_channel_overflow"], 1)

    async def test_a_only_zero_lexical_reaches_selector_and_context_injection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "style.db"
            with db.connect(db_path) as conn:
                db.init_db(conn)
                metadata = {
                    "provenance_kind": "verbatim", "payload_class": "reaction_only",
                    "schema_review_status": "approved",
                    "behavior_tags": ["update_corrected_premise"], "expression_tags": [],
                    "audience": "individual", "group_id": "source-group",
                    "speaker_status": "transcript_verified", "runtime_scope": ["style_runtime"],
                    "style_record_version": 3, "unit_id": "unit-a-only",
                    "source_episode_id": "episode-1", "trigger_basis": "self_initiated",
                    "trigger_evidence_refs": ["source:1"], "retrieval_description": "",
                    "transfer_scope": "requires_context",
                    "applicability": {
                        "requires_all": [{"field": "dialogue_function", "one_of": ["correct"]}],
                        "forbids_any": [],
                    },
                    "runtime_payload_kind": "pattern_only",
                    "runtime_reaction_fragment": None,
                    "runtime_payload_reviewed": True,
                    "projection_schema_version": "source-reaction-unit-projection-v1",
                    "projection_version": "reviewed-v1", "source_revision": "frozen-v1",
                    "q_projection": None,
                    "a_projection": {
                        "review_status": "approved",
                        "behavior_ids": ["update_corrected_premise"],
                        "mechanism_description": "接受纠正并更新前提",
                        "compatibility_features": {"continuation_policy": "optional_coda"},
                        "evidence_refs": ["source:1"],
                    },
                    "speaker_candidate": "hanser", "attribution_status": "verified",
                    "attribution_evidence_refs": ["source:1"],
                }
                conn.execute(
                    """INSERT INTO style_examples
                    (prompt,response,context_before,context_after,scene,speech_act,tone_json,
                     answer_length,response_mode,source_type,source_ref,authenticity_score,
                     quality_score,metadata_json,review_status,source_tier,source_speaker,
                     source_user_turn,source_response_turn,index_generation)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    ("", "嗯 是我没接住", "", "", "correction", "correct", "[]",
                     "short", "casual", "real", "source:1", 0.9, 0.9,
                     json.dumps(metadata, ensure_ascii=False), "approved", "primary", "hanser",
                     None, "嗯 是我没接住", "g1"),
                )
                conn.execute(
                    "INSERT INTO active_index_generations VALUES (?,?,?,datetime('now'))",
                    ("style_examples", "g1", 7),
                )
                conn.commit()
            settings = SimpleNamespace(
                db_path=db_path, userdict_path=None,
                style=SimpleNamespace(enabled=True, min_quality=0.5, top_k=3, candidate_pool_size=48),
            )
            tool = StyleSearchTool(settings=settings, strict_v2=True, pinned_generation="g1")
            plan = DialoguePlan(
                intent="chitchat", need_wiki=False, standalone_query="完全无关词面", keywords=[],
                response_mode="casual", fact_sensitivity="low", target_length="short",
                dialogue_function="correct",
            )
            signals = TurnSignals(values={
                "dialogue_function": SignalObservation(
                    name="dialogue_function", value="correct", source="planner",
                    confidence="high", status="observed", evidence_refs=["message:1"],
                )
            })
            decision = BehaviorDecision(persona_affordances=[PersonaAffordance(
                id="update_corrected_premise", guidance="接受纠正", weight=0.9,
            )])
            result = await tool.search(
                "完全无关词面", plan, turn_signals=signals, behavior_decision=decision,
            )
            self.assertEqual([item.unit_id for item in result.examples], ["unit-a-only"])
            self.assertEqual(result.matched_by, {"style:1": ["A"]})
            self.assertEqual(result.generation, "g1")
            self.assertEqual(result.revision, 7)
            self.assertEqual(result.examples[0].projection_version, "reviewed-v1")
            self.assertEqual(result.examples[0].source_revision, "frozen-v1")
            persona_dir = Path(__file__).parents[1] / "hanser_agent" / "prompts" / "persona" / "candidates" / "hanser-persona-v2-candidate"
            context = ContextBuilder(PersonaCompiler(persona_dir)).build(
                current_message="完全无关词面", history=[], plan=plan,
                style_examples=result.examples, turn_signals=signals,
                behavior_decision=decision,
            )
            self.assertEqual(context.injected_style_ids, ["style:1"])
            self.assertIn("<reaction_pattern", context.blocks["style_examples"])


if __name__ == "__main__":
    unittest.main()
