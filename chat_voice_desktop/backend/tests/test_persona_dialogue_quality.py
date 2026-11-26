from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hanser_agent.agent.context_builder import ContextBuilder
from hanser_agent.agent.planner import DialoguePlanner, PlannerDecision
from hanser_agent.config import ContextConfig, ModelProfileConfig
from hanser_agent.model_gateway import ModelGateway
from hanser_agent.models import ChatMessage, DialoguePlan, StyleExample, WikiEvidence
from hanser_agent.persona.compiler import PersonaCompiler
from hanser_agent.persona.expression import (
    build_language_control,
    observe_recent_expressions,
)
from hanser_agent.persona.policy import build_guidance
from hanser_agent.persona.schemas import EffectivePersonaSettings, LanguageControl
from hanser_agent.persona.signals import build_turn_signals
from hanser_agent.persona.style_ranking import rank_fixed_style_candidates
from hanser_agent.responder.service import HanserResponder
from hanser_agent.responder.validator import StyleValidator


CANDIDATE_DIR = (
    Path(__file__).parents[1]
    / "hanser_agent"
    / "prompts"
    / "persona"
    / "candidates"
    / "hanser-persona-v2-candidate"
)


class FailingGateway:
    async def generate_json(self, profile, messages, schema):
        raise ValueError("invalid planner payload")


class PersonaDialogueQualityTests(unittest.IsolatedAsyncioTestCase):
    def test_deepseek_alias_still_receives_explicit_non_thinking_mode(self) -> None:
        deepseek_payload: dict[str, object] = {}
        other_payload: dict[str, object] = {}

        ModelGateway._apply_provider_capabilities(
            ModelProfileConfig(
                endpoint="https://api.deepseek.com",
                model="deepseek-flash",
                think=False,
            ),
            deepseek_payload,
        )
        ModelGateway._apply_provider_capabilities(
            ModelProfileConfig(
                endpoint="https://example.invalid/v1",
                model="deepseek-flash",
                think=False,
            ),
            other_payload,
        )

        self.assertEqual(deepseek_payload["thinking"], {"type": "disabled"})
        self.assertNotIn("thinking", other_payload)

    def test_compact_planner_contract_tolerates_omitted_and_legacy_fields(self) -> None:
        decision = PlannerDecision.model_validate({
            "intent": "chitchat",
            "wiki": False,
            "mode": "casual",
            "signals": None,
        })

        self.assertEqual(decision.length, "short")
        self.assertEqual(decision.dialogue_function, "unknown")
        self.assertEqual(decision.signals, {})

    async def test_planner_fallback_keeps_dialogue_function_and_failure_kind(self) -> None:
        planner = DialoguePlanner(FailingGateway())

        plan = await planner.plan("好呀 就这样", [])

        self.assertEqual(plan.dialogue_function, "confirm")
        self.assertEqual(plan.continuation_policy, "stop")
        self.assertEqual(plan.planner_failure_kind, "valueerror")
        self.assertEqual(plan.planner_route_basis, "fallback")
        self.assertIn("planner_failure:valueerror", plan.degraded_reasons)

    def test_planner_reanchors_json_contract_after_unchanged_history(self) -> None:
        planner = DialoguePlanner(FailingGateway())
        history = [
            ChatMessage(role="user", content="晚上好~"),
            ChatMessage(role="assistant", content="晚上好~ 今天过得怎么样"),
            ChatMessage(role="user", content="刚吃完饭呀 就过来找你聊聊天了"),
            ChatMessage(
                role="assistant",
                content="吃得挺早啊 我这边刚收拾完 聊呗 今天有什么想说的",
            ),
        ]

        messages = planner.build_messages("其实就吃了个泡面 懒得做饭了", history)

        self.assertEqual(messages[1:-1], history)
        self.assertTrue(
            messages[-1].content.startswith("其实就吃了个泡面 懒得做饭了\n\n")
        )
        self.assertTrue(messages[-1].content.endswith("现在只输出一个JSON对象"))

    def test_repeated_xing_family_creates_turn_scoped_avoidance(self) -> None:
        observation = observe_recent_expressions([
            ChatMessage(role="assistant", content="行 先这样"),
            ChatMessage(role="user", content="继续"),
            ChatMessage(role="assistant", content="收到\n\n行吧 这次算你对"),
        ])

        self.assertEqual(observation.avoid_openers, ["行", "行吧", "行了"])

    def test_new_interjection_is_available_without_becoming_a_fixed_opener(self) -> None:
        observation = observe_recent_expressions([
            ChatMessage(role="assistant", content="诶呀 这次算了"),
            ChatMessage(role="assistant", content="诶呀 你怎么又来了"),
        ])

        self.assertIn("诶呀", observation.avoid_openers)

    def test_plain_reliance_phrase_is_not_counted_as_profanity(self) -> None:
        ordinary = observe_recent_expressions([
            ChatMessage(role="assistant", content="我靠这个生气也太没劲了"),
        ])
        interjection = observe_recent_expressions([
            ChatMessage(role="assistant", content="我靠 这也太快了"),
        ])

        self.assertEqual(ordinary.features["profanity"].value, "absent")
        self.assertEqual(interjection.features["profanity"].value, "present")
        patterns = StyleValidator(
            CANDIDATE_DIR / "style_constraints.yaml"
        ).disallowed_feature_patterns["profanity"]
        self.assertFalse(any(pattern.search("我靠这个生气") for pattern in patterns))
        self.assertTrue(any(pattern.search("我靠 这也太快了") for pattern in patterns))

    def test_greeting_fast_path_rejects_bare_echo_only(self) -> None:
        planner = DialoguePlanner(FailingGateway())
        plan = planner._fast_plan("晚上好~")
        self.assertIsNotNone(plan)
        assert plan is not None
        control = build_language_control(
            observe_recent_expressions([]),
            build_turn_signals("晚上好~", current_message_ref="current"),
            planner_dialogue_function=plan.dialogue_function,
            planner_greeting_turn=plan.greeting_turn,
        )
        validator = StyleValidator(CANDIDATE_DIR / "style_constraints.yaml")

        bare = validator.validate_semantic_output(
            "晚上好~",
            language_control=control,
        )
        extended = validator.validate_semantic_output(
            "晚上好~ 刚吃完饭还是刚忙完呀",
            language_control=control,
        )

        self.assertIn("bare_greeting", bare.violations)
        self.assertNotIn("bare_greeting", extended.violations)

    def test_stamp_metaphor_is_a_soft_language_quality_violation(self) -> None:
        validator = StyleValidator(CANDIDATE_DIR / "style_constraints.yaml")

        result = validator.validate_semantic_output(
            "我刚说不生气你转头就给我盖章了",
            language_control=LanguageControl(),
        )

        self.assertIn("persona_blurred_stamp_metaphor", result.violations)
        self.assertTrue(
            HanserResponder._is_language_quality_violation(
                "persona_blurred_stamp_metaphor"
            )
        )

    def test_factual_guidance_keeps_supported_hanser_facts_in_persona(self) -> None:
        decision = build_guidance(
            build_turn_signals(
                "讲讲你以前印象很深的一件事",
                current_message_ref="current",
            ),
            {},
            None,
            EffectivePersonaSettings(),
            response_mode="factual",
            fact_sensitivity="high",
            need_wiki=True,
        )
        requirement_ids = {item.requirement_id for item in decision.must_do}

        self.assertIn(
            "task.render_supported_hanser_evidence_in_first_person",
            requirement_ids,
        )
        self.assertNotIn("task.reject_unverified_shared_memory", requirement_ids)

    def test_runtime_time_and_wiki_identity_are_explicit_context(self) -> None:
        context = ContextBuilder(PersonaCompiler(CANDIDATE_DIR)).build(
            current_message="讲讲你最近印象很深的一件事",
            history=[],
            plan=DialoguePlan(
                intent="wiki_fact",
                need_wiki=True,
                need_memory=False,
                standalone_query="讲讲你最近印象很深的一件事",
                keywords=[],
                response_mode="factual",
                fact_sensitivity="high",
                target_length="medium",
            ),
            wiki_evidence=[WikiEvidence(
                source_id="document:536:chunk:8188",
                document_id=536,
                chunk_id=8188,
                filename="2024年8月23日 星期五.docx",
                text="憨憨在直播印象最深刻的事情是有次睡过头。",
                retrieval_score=13.19,
            )],
            current_time=datetime(
                2026,
                9,
                14,
                10,
                30,
                tzinfo=timezone(timedelta(hours=8), "Asia/Shanghai"),
            ),
        )

        self.assertTrue(context.has_wiki_evidence)
        self.assertIn("2026-09-14T10:30:00+08:00", context.blocks["current_time"])
        self.assertIn("timezone=Asia/Shanghai", context.blocks["current_time"])
        self.assertIn("第一人称自然转述", context.blocks["wiki_evidence"])
        self.assertEqual(
            context.block_sources["current_time"],
            ["runtime_clock:Asia/Shanghai"],
        )

    def test_evidence_identity_detachment_retries_but_first_person_is_allowed(self) -> None:
        validator = StyleValidator(CANDIDATE_DIR / "style_constraints.yaml")
        detached = validator.validate_semantic_output(
            "我没法拿直播记录里的事当成我自己的经历来讲，不能替她补一段。",
            has_wiki_evidence=True,
        )
        supported = validator.validate_semantic_output(
            "我之前也遇到过这种事。",
            has_wiki_evidence=True,
        )
        unsupported = validator.validate_semantic_output(
            "我之前也遇到过这种事。",
            has_wiki_evidence=False,
        )

        self.assertIn(
            "persona_evidence_third_person_detachment",
            detached.violations,
        )
        self.assertNotIn(
            "unsupported_first_person_past_experience",
            supported.violations,
        )
        self.assertIn(
            "unsupported_first_person_past_experience",
            unsupported.violations,
        )
        self.assertTrue(
            HanserResponder._is_language_quality_violation(
                "persona_evidence_third_person_detachment"
            )
        )

    def test_confirm_turn_stops_and_language_validator_requests_rewrite(self) -> None:
        observation = observe_recent_expressions([
            ChatMessage(role="assistant", content="行 先这样"),
            ChatMessage(role="assistant", content="行吧 这次算你对"),
        ])
        signals = build_turn_signals("好呀", current_message_ref="current")
        control = build_language_control(observation, signals)
        validator = StyleValidator(CANDIDATE_DIR / "style_constraints.yaml")

        repeated = validator.validate_semantic_output(
            "行 这事儿翻篇 你还想聊点什么",
            language_control=control,
        )
        forced = validator.validate_semantic_output(
            "原谅你了 再哭我可要收费了",
            language_control=control,
        )

        self.assertEqual(control.continuation_policy, "stop")
        self.assertIn("repeated_opener:行", repeated.violations)
        self.assertIn("generic_continuation_tail", repeated.violations)
        self.assertIn("forced_playful_extension_tail", forced.violations)

    def test_profanity_debt_becomes_hard_only_at_suitable_opportunity(self) -> None:
        settings = EffectivePersonaSettings(profanity_target_rate=0.15)
        opportunity = build_turn_signals(
            "这游戏又闪退了",
            current_message_ref="current",
        )
        neutral = build_turn_signals("我去倒杯水", current_message_ref="current")
        empty = observe_recent_expressions([])

        due = build_guidance(
            opportunity,
            {},
            empty,
            settings,
            successful_assistant_turns=6,
            current_message="这游戏又闪退了",
        )
        early = build_guidance(
            opportunity,
            {},
            empty,
            settings,
            successful_assistant_turns=5,
            current_message="这游戏又闪退了",
        )
        unsuitable = build_guidance(
            neutral,
            {},
            empty,
            settings,
            successful_assistant_turns=10,
            current_message="我去倒杯水",
        )

        self.assertIn(
            "expression.use_light_profanity",
            {item.requirement_id for item in due.must_do},
        )
        self.assertNotIn(
            "expression.use_light_profanity",
            {item.requirement_id for item in early.must_do},
        )
        self.assertNotIn(
            "expression.use_light_profanity",
            {item.requirement_id for item in unsuitable.must_do},
        )

    def test_recent_style_is_soft_penalized_but_zero_applicability_is_excluded(self) -> None:
        candidate = {
            "id": "style:2010",
            "index_generation": "v2",
            "review_status": "approved",
            "schema_review_status": "approved",
            "speaker_status": "transcript_verified",
            "provenance_kind": "verbatim",
            "payload_class": "reaction_only",
            "group_id": "g1",
            "content_relevance": 0.0,
            "quality_score": 0.9,
            "authenticity_score": 0.9,
            "expression_tags": ["profanity"],
            "source_tier": "primary",
            "source_type": "real",
            "source_speaker": "hanser",
            "source_user_turn": "又闪退了",
            "source_response_turn": "真烦",
            "runtime_scope": ["style_runtime"],
        }
        result = rank_fixed_style_candidates(
            [candidate],
            build_guidance(
                build_turn_signals("又闪退了", current_message_ref="current"),
                {},
                observe_recent_expressions([]),
                EffectivePersonaSettings(profanity_target_rate=0.15),
                successful_assistant_turns=6,
                current_message="又闪退了",
            ),
            response_mode="casual",
            active_generation="v2",
            recent_example_ids={"style:2010"},
        )

        self.assertEqual(result["selected"], [])
        self.assertEqual(
            result["excluded"]["style:2010"],
            "no_applicable_signal",
        )

    def test_dynamic_language_control_is_last_system_block(self) -> None:
        compiler = PersonaCompiler(CANDIDATE_DIR)
        context = ContextBuilder(compiler).build(
            current_message="好呀",
            history=[],
            plan=DialoguePlan(
                intent="chitchat",
                need_wiki=False,
                need_memory=False,
                standalone_query="好呀",
                keywords=[],
                response_mode="casual",
                fact_sensitivity="low",
                target_length="short",
                dialogue_function="confirm",
                continuation_policy="stop",
            ),
            style_examples=[StyleExample(
                id="fixture",
                user_context="好呀",
                character_response="那就这样",
                scene="casual_chat",
                speech_act="react",
                answer_length="short",
                response_mode="casual",
                source_type="real",
                source_ref="fixture:reviewed",
                authenticity_score=1.0,
                quality_score=1.0,
                source_tier="primary",
                review_status="approved",
            )],
            language_control=LanguageControl(
                continuation_policy="stop",
                avoid_openers=["行"],
            ),
        )

        self.assertEqual(list(context.blocks)[-1], "turn_language_control")
        self.assertLess(
            context.messages[0].content.index("[STYLE EXAMPLES]"),
            context.messages[0].content.index("[TURN LANGUAGE CONTROL]"),
        )
        self.assertEqual(context.selected_style_ids, ["fixture"])
        self.assertEqual(context.injected_style_ids, ["fixture"])

    def test_only_budget_injected_style_ids_are_exposed(self) -> None:
        compiler = PersonaCompiler(CANDIDATE_DIR)
        plan = DialoguePlan(
            intent="chitchat", need_wiki=False, need_memory=False,
            standalone_query="好呀", keywords=[], response_mode="casual",
            fact_sensitivity="low", target_length="short",
        )
        example = StyleExample(
            id="fixture-1", user_context="好呀", source_user_turn="好呀",
            character_response="那就这样" * 20, scene="casual_chat", speech_act="react",
            answer_length="short", response_mode="casual", source_type="real",
            source_ref="fixture:1", authenticity_score=1.0, quality_score=1.0,
            source_tier="primary", review_status="approved",
        )
        one = ContextBuilder(compiler).build(
            current_message="好呀", history=[], plan=plan, style_examples=[example],
        )
        constrained = ContextBuilder(
            compiler,
            ContextConfig(
                input_token_budget=one.estimated_input_tokens,
                output_reserve_tokens=0,
            ),
        ).build(
            current_message="好呀",
            history=[],
            plan=plan,
            style_examples=[
                example,
                example.model_copy(update={"id": "fixture-2", "source_ref": "fixture:2"}),
                example.model_copy(update={"id": "fixture-3", "source_ref": "fixture:3"}),
            ],
        )
        self.assertEqual(constrained.selected_style_ids, ["fixture-1", "fixture-2", "fixture-3"])
        self.assertEqual(constrained.injected_style_ids, ["fixture-1"])
        self.assertEqual(
            [item.item for item in constrained.drop_ledger if item.item.startswith("style_example:")],
            ["style_example:fixture-2", "style_example:fixture-3"],
        )


if __name__ == "__main__":
    unittest.main()
