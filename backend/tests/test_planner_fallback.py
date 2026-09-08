from __future__ import annotations

import unittest
from hanser_agent.agent.planner import DialoguePlanner
from hanser_agent.models import ChatMessage, DialoguePlan


class FailingGateway:
    async def generate_json(self, profile, messages, schema):
        raise RuntimeError("empty JSON")

    async def unload(self, profile):
        return None


class RecordingGateway:
    def __init__(self) -> None:
        self.messages = []

    async def generate_json(self, profile, messages, schema):
        self.messages = messages
        return DialoguePlan(
            intent="chitchat",
            need_wiki=False,
            standalone_query="当前问题",
            keywords=[],
            response_mode="casual",
            fact_sensitivity="low",
            target_length="short",
        )


class PlannerFallbackTests(unittest.IsolatedAsyncioTestCase):
    def test_old_planner_json_without_optional_signals_remains_valid(self) -> None:
        plan = DialoguePlan.model_validate({
            "intent": "chitchat",
            "need_wiki": False,
            "standalone_query": "晚上好",
            "keywords": [],
            "response_mode": "casual",
            "fact_sensitivity": "low",
            "target_length": "short",
        })

        self.assertEqual(plan.persona_signals, {})

    async def test_fact_request_still_routes_when_model_json_fails(self) -> None:
        planner = DialoguePlanner(FailingGateway())

        plan = await planner.plan(
            "Hanser什么时候退出VirtuaReal",
            [],
        )

        self.assertTrue(plan.need_wiki)
        self.assertEqual(plan.intent, "wiki_fact")
        self.assertEqual(plan.response_mode, "factual")
        self.assertEqual(plan.degraded_reasons, ["planner_deterministic_fallback"])
        self.assertIn("VirtuaReal", plan.keywords)

    async def test_followup_rewrite_uses_recent_user_context_on_failure(self) -> None:
        planner = DialoguePlanner(FailingGateway())
        history = [
            ChatMessage(
                role="user",
                content="Hanser什么时候退出VirtuaReal",
            ),
            ChatMessage(
                role="assistant",
                content="2023年",
            ),
        ]

        plan = await planner.plan("那她之前是什么时候加入的", history)

        self.assertTrue(plan.need_wiki)
        self.assertEqual(plan.intent, "followup_fact")
        self.assertIn("Hanser什么时候退出VirtuaReal", plan.standalone_query)
        self.assertIn("什么时候加入", plan.standalone_query)

    async def test_planner_only_sends_eight_recent_history_messages(self) -> None:
        gateway = RecordingGateway()
        planner = DialoguePlanner(gateway)
        history = [
            ChatMessage(role="user", content=f"history-{index}")
            for index in range(10)
        ]

        await planner.plan("当前问题", history)

        contents = [message.content for message in gateway.messages]
        self.assertNotIn("history-0", contents)
        self.assertNotIn("history-1", contents)
        self.assertEqual(contents[-9:-1], [f"history-{i}" for i in range(2, 10)])


if __name__ == "__main__":
    unittest.main()
