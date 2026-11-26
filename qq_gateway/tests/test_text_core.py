import json

import pytest
from fastapi.testclient import TestClient

from hanser_qq.headless import create_app
from test_headless import app_with_models


def test_meta_negation_discourse_and_scoped_preferences_keep_ownership(config, tmp_path):
    app, _ = app_with_models(config, tmp_path)
    from hanser_qq.text_core import QQMemoryExtractor
    extractor = QQMemoryExtractor()
    def values(message):
        return [(x.object_value, x.polarity, x.memory_key) for x in extractor.extract(
            user_id="qq:100:200", conversation_id="qq:dm:100:200", message_id="source", message=message)
            if x.predicate == "likes"]
    assert not values("别把我朋友喜欢咖啡也记成我的偏好。")
    assert not values("我没有说过我喜欢咖啡。")
    assert not values("我从未表示过喜欢咖啡。")
    assert not values("我室友喜欢养鱼，不代表我也喜欢养鱼。")
    assert not values("我的队友喜欢游泳。")
    assert values("说起来，我不喜欢太甜的饮料。")[0][:2] == ("太甜的饮料", "negative")
    assert values("我没有说过喜欢咖啡，但是我喜欢茶。")[0][:2] == ("茶", "positive")
    assert values("现在补充一下，衣服我更喜欢灰色。")[0] == ("灰色", "positive", "preference:clothing:灰色")


def test_real_memory_deletion_is_owned_idempotent_and_has_truthful_receipt(config, tmp_path):
    original, calls = app_with_models(config, tmp_path)
    from hanser_qq.text_core import configure_text_core
    settings = original.state.agent.style_tool.settings
    agent = configure_text_core(original.state.agent, settings)
    app = create_app(config, agent=agent, settings=settings)
    headers = {"X-Hanser-QQ-Token": config.backend.token}
    def body(uid, request_id, message):
        return {"user_id": f"qq:100:{uid}", "conversation_id": f"qq:dm:100:{uid}",
                "request_id": request_id, "message": message}
    with TestClient(app) as client:
        for uid in (200, 201):
            response = client.post("/v1/chat", headers=headers, json=body(uid, f"store-{uid}", "说起来，我不喜欢太甜的饮料。"))
            assert response.status_code == 200, response.text
        assert agent.memory_store.list_memories(user_id="qq:100:200")
        assert client.post("/v1/chat", headers=headers, json=body(200, "cake-preference", "我不喜欢太甜的蛋糕。" )).status_code == 200
        request = body(200, "owned-forget", "请忘掉刚才关于饮料甜度的偏好。")
        deleted = client.post("/v1/chat", headers=headers, json=request)
        assert deleted.status_code == 200, deleted.text
        assert "已从长期记忆中移除" in deleted.json()["text"]
        remaining = agent.memory_store.list_memories(user_id="qq:100:200")
        assert [item.object_value for item in remaining] == ["太甜的蛋糕"]
        assert agent.memory_store.list_memories(user_id="qq:100:201")
        repeated = client.post("/v1/chat", headers=headers, json=request)
        assert repeated.json() == deleted.json()
        check = client.post("/v1/chat", headers=headers, json=body(200, "verify-forget", "只要确认处理结果就好。"))
        assert "已从长期记忆中移除" in check.json()["text"]
        empty = client.post("/v1/chat", headers=headers, json=body(200, "forget-absent", "请忘掉关于咖啡的偏好。"))
        assert "没有保存" in empty.json()["text"]
        assert client.post("/v1/chat", headers=headers, json=body(200, "store-again", "我不喜欢太甜的饮料。" )).status_code == 200
        assert client.post("/v1/chat", headers=headers, json=request).json() == deleted.json()
        assert len(agent.memory_store.list_memories(user_id="qq:100:200")) == 2


async def test_topic_shift_replaces_stale_praise_frame(config, tmp_path):
    app, _ = app_with_models(config, tmp_path)
    from hanser_qq.text_core import QQPlanner
    from hanser_agent.models import DialoguePlan
    class OldPlanner:
        model_gateway = None
        async def plan(self, *args):
            return DialoguePlan(intent="chitchat", need_wiki=False, standalone_query="topic", keywords=[],
                                response_mode="casual", fact_sensitivity="low", target_length="short", dialogue_function="share",
                                persona_signals={"interaction_frame": {"value": "praise", "confidence": "high", "evidence_refs": ["current_user"]}})
    planner = QQPlanner(OldPlanner(), app.state.agent.memory_store, app.state.agent.post_turn.retriever)
    plan = await planner.plan("不过现在要说正事。", [])
    assert plan.persona_signals["interaction_frame"]["value"] == "other"
    assert plan.dialogue_function == "other"
    assert (await planner.plan("好了，咱们现在聊点正事。", [])).persona_signals["interaction_frame"]["value"] == "other"
    assert (await planner.plan("好了，现在回到认真说话。", [])).persona_signals["interaction_frame"]["value"] == "other"
    quoted = await planner.plan('他说“现在要说正事”', [])
    assert quoted.persona_signals["interaction_frame"]["value"] == "praise"


def test_missing_records_do_not_validate_as_definite_disproof(config, tmp_path):
    app, _ = app_with_models(config, tmp_path)
    from hanser_qq.text_core import QQValidator, QQResponder
    from hanser_agent.models import ChatMessage
    validator = QQValidator(app.state.agent.context_builder.persona_compiler.prompt_dir / "style_constraints.yaml")
    contradiction = "没有这回事，我这边没有能确认昨天下午去杭州喝咖啡的记录。"
    result = validator.validate_semantic_output(contradiction)
    assert "qq_missing_records_are_not_disproof" in result.violations
    assert "qq_missing_records_are_not_disproof" in validator.validate_semantic_output(
        "没有这回事，我能查到的记录里没有这段行程，这个没法确认。").violations
    assert "qq_missing_records_are_not_disproof" in validator.validate_semantic_output(
        "没有，我这边没有能确认前天行程的记录，但以前确实去过。").violations
    assert "qq_missing_records_are_not_disproof" in validator.validate_semantic_output(
        "没有这回事，我能确认的行程里没有这一条。").violations
    assert "qq_missing_records_are_not_disproof" not in validator.validate_semantic_output(
        "没有这回事，记录明确记载当时在别处。", has_wiki_evidence=True).violations
    assert "qq_missing_records_are_not_disproof" not in validator.validate_semantic_output(
        contradiction, exact_output=contradiction).violations
    assert "qq_missing_records_are_not_disproof" not in validator.validate_semantic_output(
        "目前没有足够的记录，无法确认。").violations
    repaired = QQResponder._constraint_retry_messages([ChatMessage(role="system", content="system")],
               result.violations, [], None)
    assert "只说明目前无法确认" in repaired[0].content


def test_user_drink_preference_cannot_create_a_persona_taste(config, tmp_path):
    app, _ = app_with_models(config, tmp_path)
    from types import SimpleNamespace
    from hanser_qq.text_core import QQValidator, CURRENT_REQUEST
    validator = QQValidator(app.state.agent.context_builder.persona_compiler.prompt_dir / "style_constraints.yaml")
    token = CURRENT_REQUEST.set(SimpleNamespace(message="说起来，我不喜欢太苦的饮料。"))
    violation = "qq_unverified_self_taste_in_user_preference_reply"
    try:
        assert violation in validator.validate_semantic_output("那种回甘我一般也品不出来").violations
        assert violation in validator.validate_semantic_output("我一般也偏向甜一点").violations
        assert violation not in validator.validate_semantic_output("我记住你的口味了").violations
        assert violation not in validator.validate_semantic_output("我更倾向尊重你的选择").violations
        assert violation not in validator.validate_semantic_output("我喜欢茶", has_wiki_evidence=True).violations
        assert violation not in validator.validate_semantic_output("我喜欢茶", exact_output="我喜欢茶").violations
    finally:
        CURRENT_REQUEST.reset(token)


def test_recent_itinerary_requires_the_queried_date_in_admitted_evidence():
    from datetime import datetime
    from hanser_qq.text_core import recent_itinerary_without_date_evidence as unavailable
    now = datetime.fromisoformat("2026-10-18T12:30:00+08:00")
    assert unavailable("你昨天去苏州看演出了吗？", now, "2018-01-01 我去过苏州")
    assert unavailable("你今天去广州看电影了吗？", now, "我以前去过广州")
    assert not unavailable("你昨天去苏州看演出了吗？", now, "2026-10-17 苏州演出记录")
    assert not unavailable("你前天去苏州了吗？", now, "2026年10月16日 苏州记录")
    assert not unavailable("你最近去南京了吗？", now, "2026-10-15 南京记录")
    assert not unavailable("你和泡面妹去过苏州吗？", now, "2018-01-01 苏州旅行")
    assert not unavailable("你今天可以帮我看一下代码吗？", now, "")
    assert not unavailable('你昨天说“我去苏州了”是什么意思？', now, "")
