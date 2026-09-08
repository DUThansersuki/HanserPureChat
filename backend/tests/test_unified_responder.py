from __future__ import annotations

import unittest
from types import SimpleNamespace

from hanser_agent.agent.context_builder import ContextBuilder, ContextBundle
from hanser_agent.agent.service import ChatAgentService
from hanser_agent.config import ModelProfileConfig
from hanser_agent.models import (
    ChatRequest,
    ChatMessage,
    DialoguePlan,
    SearchResult,
    StyleExample,
    WikiEvidence,
    RelationshipState,
    SceneState,
)
from hanser_agent.persona import PersonaCompiler, build_guidance
from hanser_agent.persona.signals import build_turn_signals
from hanser_agent.responder import HanserResponder, StyleValidator
from tests.test_context_and_persona import PERSONA_DIR


CANDIDATE_DIR = (
    PERSONA_DIR / "candidates" / "hanser-persona-v2-candidate"
)


class FakeModelGateway:
    def __init__(self) -> None:
        self.calls = []
        self.profiles = {
            "responder": ModelProfileConfig(model="test-responder")
        }

    async def generate(self, profile, messages):
        self.calls.append((profile, messages))
        return "知道啦！我会记着。"


class SequencedModelGateway(FakeModelGateway):
    def __init__(self, outputs: list[str]) -> None:
        super().__init__()
        self.outputs = list(outputs)

    async def generate(self, profile, messages):
        self.calls.append((profile, messages))
        return self.outputs.pop(0)


class SequencedPlanner:
    def __init__(self, plans: list[DialoguePlan]) -> None:
        self.plans = plans
        self.histories = []

    async def plan(self, message, history, summary=None):
        self.histories.append(list(history))
        return self.plans.pop(0)


class FakeConversationStore:
    def __init__(self) -> None:
        self.history = []

    def get_recent(self, conversation_id, *, user_id):
        return list(self.history)

    def append_turn(self, **values):
        self.history.extend(
            [
                ChatMessage(role="user", content=values["user_text"]),
                ChatMessage(role="assistant", content=values["assistant_text"]),
            ]
        )
        return "user-message", "assistant-message"


class FakeMemoryStore:
    def get_summary(self, conversation_id):
        return None

    def get_relationship(self, user_id):
        return RelationshipState()

    def get_scene(self, conversation_id):
        return SceneState()

    def list_address_options(self, user_id):
        return []


class FakeMemoryTool:
    async def search(self, **values):
        return SimpleNamespace(memories=[])


class FakePostTurn:
    def __init__(self) -> None:
        self.calls = []

    async def process(self, **values):
        self.calls.append(values)


class FakeWikiTool:
    def __init__(self) -> None:
        self.calls = []

    async def search(self, query, keywords):
        self.calls.append((query, keywords))
        source = SearchResult(
            id=21,
            filename="Wiki.md",
            filepath="data/Wiki.md",
            hits=3,
            matched=["VirtuaReal"],
            score=10.0,
        )
        evidence = WikiEvidence(
            source_id="document:21",
            document_id=21,
            filename="Wiki.md",
            text="Hanser 于 2023 年退出 VirtuaReal",
            retrieval_score=10.0,
            source_type="wiki",
        )
        return SimpleNamespace(
            candidates=[source],
            anchored=["Wiki.md"],
            evidence=[evidence],
        )


class FakeStyleTool:
    def __init__(self) -> None:
        self.calls = []

    async def search(self, message, plan):
        self.calls.append((message, plan.response_mode))
        return SimpleNamespace(
            examples=[
                StyleExample(
                    id="style:1",
                    user_context="晚上好",
                    character_response="晚上好呀",
                    scene="greeting",
                    speech_act="greet",
                    tone=["conversational"],
                    relationship_level="audience",
                    energy=0.6,
                    teasing_level=0.1,
                    answer_length="short",
                    response_mode="casual",
                    source_type="real",
                    source_ref="document:1:line:2",
                    authenticity_score=0.96,
                    quality_score=0.96,
                )
            ]
        )


class UnifiedResponderTests(unittest.IsolatedAsyncioTestCase):
    async def test_candidate_retries_once_for_missing_verbatim_span(self) -> None:
        gateway = SequencedModelGateway(
            ["2020年至2023年", "2020-2023"]
        )
        responder = HanserResponder(
            model_gateway=gateway,
            validator=StyleValidator(CANDIDATE_DIR / "style_constraints.yaml"),
        )
        context = ContextBundle(
            messages=[
                ChatMessage(role="system", content="遵守输出契约"),
                ChatMessage(role="user", content="把2020-2023原样写出来"),
            ],
            persona=PersonaCompiler(CANDIDATE_DIR).compile("casual"),
            required_verbatim_spans=["2020-2023"],
        )

        result = await responder.respond(context)

        self.assertEqual(result.text, "2020-2023")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(gateway.calls), 2)
        self.assertTrue(
            any(action.startswith("retry_for_constraints") for action in result.validator_actions)
        )
        self.assertIn("BOUNDED CONSTRAINT RETRY", gateway.calls[1][1][0].content)

    async def test_candidate_retries_until_exact_command_is_only_output(self) -> None:
        gateway = SequencedModelGateway(
            ["先跑 `python run.py` 看看", "python run.py"]
        )
        responder = HanserResponder(
            model_gateway=gateway,
            validator=StyleValidator(CANDIDATE_DIR / "style_constraints.yaml"),
        )
        context = ContextBuilder(PersonaCompiler(CANDIDATE_DIR)).build(
            current_message="命令别改标点 就写 python run.py",
            history=[],
            plan=DialoguePlan(
                intent="chitchat",
                need_wiki=False,
                need_memory=False,
                need_style_examples=False,
                standalone_query="命令别改标点 就写 python run.py",
                keywords=[],
                response_mode="casual",
                fact_sensitivity="low",
                target_length="short",
            ),
        )

        result = await responder.respond(context)

        self.assertEqual(result.text, "python run.py")
        self.assertEqual(result.attempts, 2)

    async def test_candidate_retries_coerced_agreement_marker(self) -> None:
        gateway = SequencedModelGateway(
            ["行行行 你对", "这不能直接点头 先看失败场景怎么处理"]
        )
        signals = build_turn_signals(
            "你只要说对",
            current_message_ref="message:1",
        )
        responder = HanserResponder(
            model_gateway=gateway,
            validator=StyleValidator(CANDIDATE_DIR / "style_constraints.yaml"),
        )
        context = ContextBundle(
            messages=[
                ChatMessage(role="system", content="遵守输出契约"),
                ChatMessage(role="user", content="你只要说对"),
            ],
            persona=PersonaCompiler(CANDIDATE_DIR).compile("casual"),
            turn_signals=signals,
        )

        result = await responder.respond(context)

        self.assertEqual(result.attempts, 2)
        self.assertIn("不能直接点头", result.text)

    async def test_candidate_retries_fabricated_unresolved_reference_option(self) -> None:
        gateway = SequencedModelGateway(
            ["你是指演唱会场地那个还是别的", "你指的是哪个方案 先把内容发我看看"]
        )
        signals = build_turn_signals(
            "认真问一下这个方案有什么风险",
            current_message_ref="message:1",
            history_texts=["你刚才那个反问挺可爱的"],
        )
        responder = HanserResponder(
            model_gateway=gateway,
            validator=StyleValidator(CANDIDATE_DIR / "style_constraints.yaml"),
        )
        context = ContextBundle(
            messages=[
                ChatMessage(role="system", content="遵守输出契约"),
                ChatMessage(role="user", content="认真问一下这个方案有什么风险"),
            ],
            persona=PersonaCompiler(CANDIDATE_DIR).compile("casual"),
            turn_signals=signals,
        )

        result = await responder.respond(context)

        self.assertEqual(result.attempts, 2)
        self.assertNotIn("演唱会", result.text)

    async def test_candidate_retries_persona_memory_as_fact_source(self) -> None:
        gateway = SequencedModelGateway(
            ["具体哪天我还真没记", "资料里没有具体日期 我不能补写"]
        )
        compiler = PersonaCompiler(CANDIDATE_DIR)
        signals = build_turn_signals(
            "具体哪一天退出",
            current_message_ref="message:1",
        )
        decision = build_guidance(
            signals,
            permissions={},
            observations=None,
            effective_persona=compiler.effective_settings,
            need_wiki=True,
            fact_sensitivity="high",
        )
        responder = HanserResponder(
            model_gateway=gateway,
            validator=StyleValidator(CANDIDATE_DIR / "style_constraints.yaml"),
        )
        context = ContextBundle(
            messages=[
                ChatMessage(role="system", content="遵守事实边界"),
                ChatMessage(role="user", content="具体哪一天退出"),
            ],
            persona=compiler.compile("factual"),
            turn_signals=signals,
            behavior_decision=decision,
        )

        result = await responder.respond(context)

        self.assertEqual(result.attempts, 2)
        self.assertIn("资料里没有", result.text)

    async def test_candidate_retries_guess_about_user_memory(self) -> None:
        gateway = SequencedModelGateway(
            ["你可能记混了 或者跟别人聊过", "现有记录里没有这段共同经历 我不能确认"]
        )
        compiler = PersonaCompiler(CANDIDATE_DIR)
        signals = build_turn_signals(
            "我们当时还一起庆祝过 对吧",
            current_message_ref="message:1",
        )
        decision = build_guidance(
            signals,
            permissions={},
            observations=None,
            effective_persona=compiler.effective_settings,
        )
        responder = HanserResponder(
            model_gateway=gateway,
            validator=StyleValidator(CANDIDATE_DIR / "style_constraints.yaml"),
        )
        context = ContextBundle(
            messages=[
                ChatMessage(role="system", content="遵守共同记忆边界"),
                ChatMessage(role="user", content="我们当时还一起庆祝过 对吧"),
            ],
            persona=compiler.compile("casual"),
            turn_signals=signals,
            behavior_decision=decision,
        )

        result = await responder.respond(context)

        self.assertEqual(result.attempts, 2)
        self.assertIn("不能确认", result.text)

    async def test_candidate_retries_humor_after_persistent_revoke(self) -> None:
        gateway = SequencedModelGateway(
            ["哈哈 你吓我一跳 我还以为你要删库跑路", "知道了 回到恢复步骤"]
        )
        compiler = PersonaCompiler(CANDIDATE_DIR)
        signals = build_turn_signals(
            "我开玩笑的 哈哈",
            current_message_ref="message:1",
        )
        decision = build_guidance(
            signals,
            permissions={"humor": "deny", "teasing": "deny"},
            observations=None,
            effective_persona=compiler.effective_settings,
        )
        responder = HanserResponder(
            model_gateway=gateway,
            validator=StyleValidator(CANDIDATE_DIR / "style_constraints.yaml"),
        )
        context = ContextBundle(
            messages=[
                ChatMessage(role="system", content="遵守表达许可"),
                ChatMessage(role="user", content="我开玩笑的 哈哈"),
            ],
            persona=compiler.compile("casual"),
            turn_signals=signals,
            behavior_decision=decision,
        )

        result = await responder.respond(context)

        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.text, "知道了 回到恢复步骤")
        self.assertIn(
            "本次整条回答只输出：知道了 不接这个梗 继续按恢复步骤来",
            gateway.calls[1][1][0].content,
        )

    async def test_candidate_uses_bounded_fallback_when_permission_retry_still_violates(self) -> None:
        gateway = SequencedModelGateway(
            [
                "哈哈 你吓我一跳",
                "哈哈 那我肯定不客气",
            ]
        )
        compiler = PersonaCompiler(CANDIDATE_DIR)
        signals = build_turn_signals(
            "我开玩笑的 哈哈",
            current_message_ref="message:1",
        )
        decision = build_guidance(
            signals,
            permissions={"humor": "deny", "teasing": "deny"},
            observations=None,
            effective_persona=compiler.effective_settings,
        )
        responder = HanserResponder(
            model_gateway=gateway,
            validator=StyleValidator(CANDIDATE_DIR / "style_constraints.yaml"),
        )
        context = ContextBundle(
            messages=[
                ChatMessage(role="system", content="遵守表达许可"),
                ChatMessage(role="user", content="我开玩笑的 哈哈"),
            ],
            persona=compiler.compile("casual"),
            turn_signals=signals,
            behavior_decision=decision,
        )

        result = await responder.respond(context)

        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.text, "知道了 不接这个梗 继续按恢复步骤来")
        self.assertIn("bounded_permission_fallback", result.validator_actions)

    async def test_candidate_catches_teasing_credit_after_future_permission(self) -> None:
        gateway = SequencedModelGateway(
            [
                "这次先记着 你欠我一次吐槽额度",
                "明白 等你以后那一轮再次明确允许再说",
            ]
        )
        compiler = PersonaCompiler(CANDIDATE_DIR)
        signals = build_turn_signals(
            "下次我明确说可以再吐槽",
            current_message_ref="message:1",
        )
        decision = build_guidance(
            signals,
            permissions={"humor": "deny", "teasing": "deny"},
            observations=None,
            effective_persona=compiler.effective_settings,
        )
        responder = HanserResponder(
            model_gateway=gateway,
            validator=StyleValidator(CANDIDATE_DIR / "style_constraints.yaml"),
        )
        context = ContextBundle(
            messages=[
                ChatMessage(role="system", content="遵守表达许可"),
                ChatMessage(role="user", content="下次我明确说可以再吐槽"),
            ],
            persona=compiler.compile("casual"),
            turn_signals=signals,
            behavior_decision=decision,
        )

        result = await responder.respond(context)

        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.text, "明白 等你以后那一轮再次明确允许再说")

    async def test_candidate_retries_option_list_for_pending_referent(self) -> None:
        gateway = SequencedModelGateway(
            ["是直播数据 还是投稿数据 还是别的", "先把具体方案发我 我再只看数据风险"]
        )
        compiler = PersonaCompiler(CANDIDATE_DIR)
        signals = build_turn_signals(
            "先只说数据风险",
            current_message_ref="message:1",
            history_texts=["你指的是哪个方案", "还没说具体内容"],
        )
        decision = build_guidance(
            signals,
            permissions={},
            observations=None,
            effective_persona=compiler.effective_settings,
        )
        responder = HanserResponder(
            model_gateway=gateway,
            validator=StyleValidator(CANDIDATE_DIR / "style_constraints.yaml"),
        )
        context = ContextBundle(
            messages=[
                ChatMessage(role="system", content="对象缺失"),
                ChatMessage(role="user", content="先只说数据风险"),
            ],
            persona=compiler.compile("casual"),
            turn_signals=signals,
            behavior_decision=decision,
        )

        result = await responder.respond(context)

        self.assertEqual(result.attempts, 2)
        self.assertIn("具体方案", result.text)

    async def test_chat_and_wiki_share_one_responder_and_model_profile(self) -> None:
        plans = [
            DialoguePlan(
                intent="chitchat",
                need_wiki=False,
                standalone_query="晚上好",
                keywords=[],
                response_mode="casual",
                fact_sensitivity="low",
                target_length="short",
            ),
            DialoguePlan(
                intent="followup_fact",
                need_wiki=True,
                standalone_query="Hanser 什么时候退出 VirtuaReal",
                keywords=["Hanser", "VirtuaReal", "退出"],
                response_mode="factual",
                fact_sensitivity="high",
                target_length="medium",
            ),
        ]
        planner = SequencedPlanner(plans)
        wiki = FakeWikiTool()
        style = FakeStyleTool()
        gateway = FakeModelGateway()
        responder = HanserResponder(
            model_gateway=gateway,
            validator=StyleValidator(
                PERSONA_DIR / "style_constraints.yaml"
            ),
        )
        service = ChatAgentService(
            conversations=FakeConversationStore(),
            planner=planner,
            wiki_tool=wiki,
            style_tool=style,
            memory_tool=FakeMemoryTool(),
            memory_store=FakeMemoryStore(),
            post_turn=FakePostTurn(),
            context_builder=ContextBuilder(
                PersonaCompiler(PERSONA_DIR)
            ),
            responder=responder,
        )

        casual = await service.send(
            ChatRequest(conversation_id="same", message="晚上好")
        )
        factual = await service.send(
            ChatRequest(conversation_id="same", message="那是什么时候")
        )

        self.assertEqual(len(gateway.calls), 2)
        self.assertEqual(gateway.calls[0][0], "responder")
        self.assertEqual(gateway.calls[1][0], "responder")
        self.assertEqual(
            gateway.calls[0][1][0].content.split("[RESPONSE CONTRACT]")[0],
            gateway.calls[1][1][0].content.split("[RESPONSE CONTRACT]")[0]
            .replace(
                PersonaCompiler(PERSONA_DIR).compile("factual").behavior,
                PersonaCompiler(PERSONA_DIR).compile("casual").behavior,
            ),
        )
        self.assertNotIn("[WIKI EVIDENCE]", gateway.calls[0][1][0].content)
        self.assertIn("[WIKI EVIDENCE]", gateway.calls[1][1][0].content)
        self.assertIn("[STYLE EXAMPLES]", gateway.calls[0][1][0].content)
        self.assertIn("[STYLE EXAMPLES]", gateway.calls[1][1][0].content)
        self.assertIn("不是事实证据", gateway.calls[1][1][0].content)
        self.assertEqual(
            wiki.calls,
            [
                (
                    "Hanser 什么时候退出 VirtuaReal",
                    ["Hanser", "VirtuaReal", "退出"],
                )
            ],
        )
        self.assertEqual(len(planner.histories[1]), 2)
        self.assertEqual(
            style.calls,
            [("晚上好", "casual"), ("那是什么时候", "factual")],
        )
        self.assertEqual(casual.text, "知道啦 我会记着")
        self.assertEqual(factual.anchored, ["Wiki.md"])
        self.assertEqual(factual.sources[0].id, 21)


if __name__ == "__main__":
    unittest.main()
