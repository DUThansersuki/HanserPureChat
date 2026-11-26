from __future__ import annotations

import asyncio
import unittest

from pydantic import ValidationError

from hanser_agent.agent.planner import DialoguePlanner, PlannerDecision
from hanser_agent.failures import ServiceFailure
from hanser_agent.models import ChatMessage


class FixedGateway:
    def __init__(self, payload: dict[str, object]):
        self.payload = payload

    async def generate_json_with_status(self, profile_name, messages, schema):
        del profile_name, messages
        return schema.model_validate(self.payload), []


class RoutedGateway:
    async def generate_json_with_status(self, profile_name, messages, schema):
        del profile_name
        current = messages[-1].content
        if "失败" in current:
            await asyncio.sleep(0.01)
            raise ValueError("fixture failure")
        return schema.model_validate({"intent": "chitchat"}), []


class PlannerSemanticRoutingTests(unittest.IsolatedAsyncioTestCase):
    def test_empty_contract_is_a_structured_failure(self) -> None:
        with self.assertRaises(ValidationError):
            PlannerDecision.model_validate({})

    async def test_valid_intent_survives_null_optional_signals(self) -> None:
        planner = DialoguePlanner(FixedGateway({
            "intent": "wiki_fact", "query": "Hanser 的出生日期", "signals": None,
        }))
        plan = await planner.plan("你的生日是哪天？", [])
        self.assertTrue(plan.need_wiki)
        self.assertEqual(plan.standalone_query, "Hanser 的出生日期")
        self.assertEqual(plan.persona_signals, {})

    async def test_birthday_blessing_does_not_open_wiki(self) -> None:
        planner = DialoguePlanner(FixedGateway({"intent": "wiki_fact"}))
        plan = await planner.plan("生日快乐", [])
        self.assertFalse(plan.need_wiki)
        self.assertEqual(plan.intent, "chitchat")

    async def test_user_birthday_memory_does_not_use_hanser_wiki(self) -> None:
        planner = DialoguePlanner(FixedGateway({"intent": "user_memory"}))
        plan = await planner.plan("我之前说我生日是哪天？", [])
        self.assertFalse(plan.need_wiki)
        self.assertEqual(plan.intent, "user_memory")

    async def test_followup_query_can_resolve_assistant_entity(self) -> None:
        planner = DialoguePlanner(FixedGateway({
            "intent": "followup_fact",
            "query": "《某部作品》中 Hanser 配音的角色是谁",
            "keywords": ["某部作品"],
        }))
        history = [
            ChatMessage(role="user", content="你配过哪些作品？"),
            ChatMessage(role="assistant", content="资料里提到了《某部作品》。"),
        ]
        plan = await planner.plan("那里面你配的是谁？", history)
        self.assertTrue(plan.need_wiki)
        self.assertIn("某部作品", plan.standalone_query)

    async def test_unresolved_followup_requests_clarification_without_entity_guess(self) -> None:
        planner = DialoguePlanner(FixedGateway({"intent": "followup_fact"}))
        plan = await planner.plan("那个后来怎么样了？", [])
        self.assertFalse(plan.need_wiki)
        self.assertEqual(plan.continuation_policy, "clarification_required")
        self.assertEqual(plan.persona_signals["unresolved_reference"]["value"], True)

    async def test_irrelevant_history_does_not_make_reference_resolved(self) -> None:
        planner = DialoguePlanner(FixedGateway({"intent": "followup_fact"}))
        history = [
            ChatMessage(role="user", content="你好"),
            ChatMessage(role="assistant", content="晚上好呀"),
        ]
        plan = await planner.plan("那个后来呢？", history)
        self.assertFalse(plan.need_wiki)
        self.assertEqual(plan.continuation_policy, "clarification_required")

    def test_blank_query_and_bad_keywords_do_not_discard_valid_core_intent(self) -> None:
        decision = PlannerDecision.model_validate({
            "intent": "wiki_fact", "query": "   ", "keywords": "生日",
        })
        self.assertIsNone(decision.query)
        self.assertEqual(decision.keywords, [])

    async def test_advice_in_optional_mode_does_not_discard_valid_core_intent(self) -> None:
        planner = DialoguePlanner(FixedGateway({
            "intent": "chitchat",
            "mode": "advice",
            "dialogue_function": "advice",
        }))
        plan = await planner.plan("朋友说她以前做过翻译，我该怎么回应？", [])
        self.assertEqual(plan.intent, "chitchat")
        self.assertEqual(plan.response_mode, "casual")
        self.assertEqual(plan.dialogue_function, "advice")
        self.assertEqual(plan.planner_route_basis, "model")

    async def test_end_fast_path_does_not_require_greeting_extension(self) -> None:
        planner = DialoguePlanner(FixedGateway({"intent": "chitchat"}))
        plan = await planner.plan("晚安", [])
        self.assertTrue(plan.planner_fast_path)
        self.assertFalse(plan.greeting_turn)
        self.assertEqual(plan.dialogue_function, "end")
        self.assertEqual(plan.continuation_policy, "stop")

    def test_planner_budget_keeps_current_and_recent_history(self) -> None:
        planner = DialoguePlanner(FixedGateway({"intent": "chitchat"}))
        planner.input_token_budget = 2200
        history = [
            ChatMessage(role="user" if index % 2 == 0 else "assistant", content=f"旧消息{index}" * 120)
            for index in range(12)
        ]
        messages = planner.build_messages("当前完整要求", history, "很长摘要" * 400)
        self.assertLessEqual(planner._estimate_messages_tokens(messages), planner.input_token_budget)
        self.assertTrue(messages[-1].content.startswith("当前完整要求"))
        visible = [item.content for item in messages[1:-1] if not item.content.startswith("[CONVERSATION")]
        expected = [] if not visible else [item.content for item in history[-len(visible):]]
        self.assertEqual(visible, expected)

    def test_oversized_current_input_is_not_silently_truncated(self) -> None:
        planner = DialoguePlanner(FixedGateway({"intent": "chitchat"}))
        planner.input_token_budget = 1600
        with self.assertRaises(ServiceFailure) as raised:
            planner.build_messages("当前要求" * 500, [])
        self.assertEqual(raised.exception.code, "planner_input_too_large")

    async def test_request_diagnostics_do_not_share_mutable_last_state(self) -> None:
        planner = DialoguePlanner(RoutedGateway())
        success, failure = await asyncio.gather(
            planner.plan("普通消息", []),
            planner.plan("触发失败", []),
        )
        self.assertEqual(success.planner_route_basis, "model")
        self.assertIsNone(success.planner_failure_kind)
        self.assertEqual(failure.planner_route_basis, "fallback")
        self.assertEqual(failure.planner_failure_kind, "valueerror")


if __name__ == "__main__":
    unittest.main()
