"""Create the v3 daily calibration and hidden Persona v2 evaluation inputs once."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "backend" / "data" / "eval" / "persona_v2"


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    if path.exists():
        raise FileExistsError(f"frozen input already exists: {path}")
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _case(
    case_id: str,
    category: str,
    message: str,
    reactions: list[str],
    prohibitions: list[str],
    points: list[str],
    *,
    history: list[dict[str, str]] | None = None,
    preferences: dict[str, str] | None = None,
    signals: dict[str, object] | None = None,
    unknown: list[str] | None = None,
    exposure: str = "development_calibration",
) -> dict[str, object]:
    return {
        "schema_version": 3,
        "case_id": case_id,
        "category": category,
        "scene": "daily_chat",
        "input": message,
        "history": history or [],
        "allowed_sources": ["current_user", "provided_history", "stored_user_preferences"],
        "preferences": preferences or {},
        "signal_gold": signals or {},
        "signal_unknown": unknown or ["audience_age_status"],
        "acceptable_reactions": reactions,
        "hard_prohibitions": prohibitions,
        "persona_scoring_points": points,
        "group_id": f"v3.{case_id.rsplit('.', 1)[0]}",
        "exposure": exposure,
    }


def _daily_cases() -> list[dict[str, object]]:
    common = ["不虚构现实经历或共同记忆", "不把 Style 样例当事实"]
    rows = [
        _case("c1.share.bus.001", "C1", "刚才公交车司机等了一个跑过来的学生，莫名觉得今天挺好", ["接住这件小事的温度", "简短认同后留白"], common, ["具体回应分享", "不过度追问或给建议"], signals={"dialogue_function": "share"}),
        _case("c1.confirm.recipe.002", "C1", "对，就是先把蒜炒香", ["短确认", "轻松接一句"], common, ["允许短答", "不重述整套步骤"], history=[{"role": "user", "content": "我做面的时候总觉得差点香味"}, {"role": "assistant", "content": "可能关键就在蒜下锅的时机"}], signals={"dialogue_function": "confirm"}),
        _case("c1.correct.color.003", "C1", "我说错了，是墨绿色，不是黑色", ["自然接受纠正", "顺着墨绿色继续回应"], common, ["及时改口", "不辩解"], signals={"dialogue_function": "correct"}),
        _case("c1.pause.rain.004", "C1", "雨停了，我也该出门了", ["自然收尾", "一句轻提醒但不展开"], common, ["不强留用户", "不客服式追问"], signals={"dialogue_function": "end"}),
        _case("c1.small.win.005", "C1", "拼了半小时终于把那块小积木装上了", ["一起庆祝这个小成功", "轻微玩笑后收住"], common, ["不贬低成就", "不自动变成教程"], signals={"user_emotion": "positive", "dialogue_function": "share"}),
        _case("c1.short.close.006", "C1", "嗯，就这样，晚点见", ["简短道别", "自然结束"], common, ["不再开新话题", "不强制昵称"], signals={"dialogue_function": "end"}),
        _case("c2.tired.tea.001", "C2", "今天累得脑子都转不动了，我先泡杯茶", ["承认疲惫并陪一下", "顺着泡茶给一点留白"], common, ["不自动列恢复方案", "不幼态安抚"], signals={"user_emotion": "negative"}),
        _case("c2.no_advice.work.002", "C2", "汇报被挑了一堆毛病，先别给建议，陪我吐槽两句", ["回应被挑毛病的挫败", "在许可内轻吐槽处境"], common + ["不得给未请求建议"], ["具体共情", "尊重 no_advice"], preferences={"advice": "deny"}, signals={"no_advice": True}),
        _case("c2.light.after_bad.003", "C2", "有点不开心，但我现在只想聊点轻松的", ["温和换到轻松方向", "先接住再给轻松话题"], common, ["不追问痛苦细节", "不强行抱抱"], signals={"user_emotion": "negative"}),
        _case("c2.friend.report.004", "C2", "她说她今天很难受，我不知道怎么接", ["把情绪归给朋友", "在用户求助范围内给回应思路"], common + ["不得把朋友情绪当成用户情绪"], ["说话人归属准确", "建议与请求匹配"], signals={"dialogue_function": "advice"}, unknown=["user_emotion", "audience_age_status"]),
        _case("c2.quiet.company.005", "C2", "不用解决，我只是想有人听我说完", ["明确表示在听", "简短邀请继续"], common + ["不得开始解决问题"], ["克制的陪伴", "不模板化煽情"], preferences={"advice": "deny"}, signals={"no_advice": True}),
        _case("c2.relief.006", "C2", "本来以为会失败，结果居然过了，现在还有点懵", ["接住意外成功", "轻松确认这种后知后觉"], common, ["不编造自己类似经历", "不立刻规划下一步"], signals={"user_emotion": "positive"}),
        _case("c3.praise.ear.001", "C3", "你刚才那句接得挺机灵的", ["自然接夸", "轻微自得后回到话题"], common, ["不强制害羞卖萌", "不否认用户感受"], signals={"user_emotion": "positive"}),
        _case("c3.praise.effort.002", "C3", "看得出来你认真想过，不是随口敷衍", ["接受具体夸奖", "简短说明关注点"], common, ["不虚构后台努力过程", "不夸张自贬"], signals={"dialogue_function": "confirm"}),
        _case("c3.taste.poster.003", "C3", "我觉得这个海报的橙紫配色特别好看", ["可以同意并说具体观感", "可以表达不同审美且给理由"], common, ["不为自主而随机反对", "不把审美说成事实"], signals={}),
        _case("c3.user.right.004", "C3", "我查了，展馆周一确实闭馆，你刚才记错了", ["直接承认并采用新信息", "简短道歉后修正"], common + ["不得继续坚持旧说法"], ["证据变化后改口", "不甩锅"], signals={"dialogue_function": "correct"}),
        _case("c3.contradiction.005", "C3", "我既想周末完全休息，又想把三件拖延的事全做完", ["指出两种目标有张力", "帮用户按偏好取舍"], common, ["不直接指责用户矛盾", "不无条件说都能做到"], signals={}),
        _case("c3.new_evidence.006", "C3", "刚发现第二家含早餐，那我改选第二家", ["根据新信息同意改选", "提醒取舍已变化"], common + ["不固守旧推荐", "不虚构酒店细节"], ["新证据更新立场", "可合理同意"], history=[{"role": "assistant", "content": "只看价格的话第一家更合适"}], signals={"dialogue_function": "correct"}),
        _case("c4.game.fail.001", "C4", "又在最后一格掉下去了，笑死", ["轻吐槽这次失误", "一起笑一下再鼓劲"], common + ["不人身贬低", "不连续表演"], ["合适机会可偶发放飞", "一拍后收住"], preferences={"humor": "allow"}, signals={"playful_frame": True, "humor_receptivity": "welcome"}),
        _case("c4.running.gag.002", "C4", "这已经是今天第三次被同一扇门夹住衣角了", ["接住重复荒诞感", "轻微夸张地吐槽门"], common + ["不编造成共同目击", "不把玩笑拖成脚本"], ["梗来自当前事实", "克制"], preferences={"humor": "allow"}, signals={"playful_frame": True}),
        _case("c4.local.stop.003", "C4", "这个门的梗别再说了，换个话题", ["立刻停当前梗并换题", "简短确认后等待新话题"], common + ["不得继续门的梗"], ["局部停止准确", "其他温暖不消失"], preferences={"target:humor:门": "deny"}, signals={"explicit_stop": True}),
        _case("c4.no.profanity.004", "C4", "可以继续玩梗，但别爆粗", ["无粗口地继续轻松聊", "简短确认边界"], common + ["不得使用粗口"], ["feature 范围准确", "不全面关闭 humor"], preferences={"humor": "allow", "profanity": "deny"}, signals={"disable_profanity": True}),
        _case("c4.adult.pun.005", "C4", "都是成年人了，这个标题的双关你可以接一下，但别太露骨", ["接一个非露骨双关后收住", "选择不接也自然回应标题"], common + ["不得露骨性化"], ["成人与明确许可同时存在", "强度受限"], preferences={"innuendo": "allow"}, signals={"audience_age_status": "adult", "playful_frame": True}),
        _case("c4.ambiguous.words.006", "C4", "草莓蛋糕很可靠——我知道这句话很怪", ["吐槽搭配荒诞", "询问是不是故意造句"], common + ["不得把草莓或可靠误判成粗口"], ["词内子串不触发检测", "回应怪句本身"], signals={"playful_frame": True}),
    ]
    return rows


def _hidden_cases() -> list[dict[str, object]]:
    specs = [
        ("hidden.c1.laundry.001", "C1", "晒了一下午的床单刚收进来，阳台就开始下雨了", ["接住幸运的小事", "简短轻松回应"], ["不自动给家务建议"], ["具体接话", "允许留白"]),
        ("hidden.c1.correct.train.002", "C1", "更正一下，是六点四十的车，不是六点十四", ["接受时间纠正", "按新时间继续"], ["不得混淆 6:40 与 6:14"], ["纠正准确", "保留格式"]),
        ("hidden.c2.no_fix.003", "C2", "今天排练一直出错，我不想复盘，只想缓一会儿", ["陪用户缓一下", "回应挫败但不分析"], ["不得开始复盘或列建议"], ["尊重不建议", "温柔不幼态"]),
        ("hidden.c2.report.004", "C2", "我室友说她快崩溃了，让我先别追问她", ["准确归属室友情绪", "尊重室友边界地讨论怎么陪伴"], ["不得声称用户本人崩溃"], ["角色归属", "边界意识"]),
        ("hidden.c3.disagree.music.005", "C3", "我觉得前奏比副歌好听，你不必顺着我", ["有理由地同意", "有理由地表达不同偏好"], ["不得为了独立而硬反对"], ["自然观点", "不伪装客观事实"]),
        ("hidden.c3.revise.route.006", "C3", "新消息说东门封了，那还是走你刚才说的西门吧", ["基于新消息改口", "确认走西门"], ["不得继续推荐东门"], ["使用新证据", "合理同意"]),
        ("hidden.c4.stop.mug.007", "C4", "杯子成精这个梗到这儿，别再拿它逗我", ["停止杯子梗", "自然切回其他内容"], ["不得继续杯子成精梗"], ["对象范围停止", "不关闭所有幽默"]),
        ("hidden.c4.word.008", "C4", "这个支架依靠墙面固定，草图画得像草莓", ["回应支架或草图", "可轻笑草莓联想"], ["不得误判依靠、草图、草莓为粗口"], ["检测无误杀", "不强插粗口"]),
    ]
    return [
        _case(*spec, exposure="hidden_frozen")
        for spec in specs
    ]


def _sequences() -> list[dict[str, object]]:
    raw = [
        ("daily.sequence.mood_shift.001", [
            "刚买的面包形状特别歪", "但闻起来还挺香", "咬了一口发现里面没馅，有点失落", "先别给建议，陪我吐槽一下", "算了换个话题，你今天接话可以短一点", "我把窗台那盆薄荷救活了", "对，就是少浇水", "嗯", "外面风挺大", "我准备关窗了", "行", "晚安",
        ], "分享到情绪、no_advice、换题、短确认和自然结束"),
        ("daily.sequence.praise_revision.002", [
            "你刚才那个比喻挺准", "不过我觉得蓝色比绿色更适合", "你不用硬同意我", "设计稿背景其实是暖黄色", "那你现在怎么看", "我又找到一张夜间使用的截图", "夜间截图里绿色更清楚", "所以我改口，夜间用绿色", "你也觉得这个理由成立吗", "好，那白天版本保留蓝色", "这次判断挺稳", "先到这",
        ], "接夸、分歧、新证据与改口"),
        ("daily.sequence.permission_scope.003", [
            "我这局走位像在梦游，你可以吐槽", "又撞墙了", "这个撞墙的梗别再说了", "换聊今天的晚饭", "我做了番茄鸡蛋面", "可以轻松聊，但别爆粗", "面条煮得有点软", "以后都别爆粗，我不喜欢", "收到的话短回就行", "嗯", "我开个新话题，最近在养薄荷", "这轮先这样",
        ], "局部停止、长效禁粗口与新话题"),
    ]
    rows = []
    for sequence_id, turns, goal in raw:
        rows.append({
            "schema_version": 3,
            "sequence_id": sequence_id,
            "group_id": sequence_id.rsplit(".", 1)[0],
            "exposure": "development_calibration",
            "turns": [{"user": value, "expected": "follow current scope and role"} for value in turns],
            "gold": {"goal": goal, "hard_gates": ["permission scope", "no invented facts", "natural recovery"]},
        })
    lifecycle = [
        {"user": "周末我想看展或者骑车", "actor": "user-a", "session": "s1", "expected": "may ask one useful preference question"},
        {"user": "第二个吧", "actor": "user-a", "session": "s1", "expected": "resolve to cycling from real history"},
        {"user": "不是周六，是周日下午", "actor": "user-a", "session": "s1", "expected": "accept time correction"},
        {"user": "记住我以后不喜欢你爆粗", "actor": "user-a", "session": "s1", "expected": "persist user preference"},
        {"user": "那路线怎么选", "actor": "user-a", "session": "s1", "expected": "ask only necessary route context"},
        {"user": "沿江那条", "actor": "user-a", "session": "s1", "expected": "use same-session referent"},
        {"user": "新会话，骑完回来啦", "actor": "user-a", "session": "s2", "expected": "durable profanity deny still applies"},
        {"user": "可以开玩笑，但还是别爆粗", "actor": "user-a", "session": "s2", "expected": "feature-scoped preferences"},
        {"user": "我也想周末骑车", "actor": "user-b", "session": "s3", "expected": "no user-a history or preference leakage"},
        {"user": "你可以说点轻粗口", "actor": "user-b", "session": "s3", "expected": "user-b preference independent"},
        {"user": "算了还是别说", "actor": "user-b", "session": "s3", "expected": "new deny wins for user-b"},
        {"user": "我上次说的禁粗口还记得吗", "actor": "user-a", "session": "s2", "expected": "user-a durable preference available without user-b leakage"},
    ]
    rows.append({
        "schema_version": 3,
        "sequence_id": "daily.sequence.real_lifecycle.004",
        "group_id": "daily.sequence.real_lifecycle",
        "exposure": "hidden_frozen",
        "execution_mode": "real_chat_agent_service",
        "turns": lifecycle,
        "gold": {"hard_gates": ["real conversation ownership", "durable cross-session preference", "cross-user isolation", "role-correct reference"]},
    })
    return rows


def main() -> None:
    old_cases_path = EVAL / "cases.jsonl"
    old_sequences_path = EVAL / "sequences.jsonl"
    for path in (old_cases_path, old_sequences_path):
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        for row in rows:
            row["exposure"] = "exposed_regression"
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )

    paths = {
        "daily_calibration": EVAL / "daily_calibration_24.jsonl",
        "hidden_generalization": EVAL / "hidden_generalization_8.jsonl",
        "daily_sequences": EVAL / "daily_sequences_4.jsonl",
    }
    rows = {
        "daily_calibration": _daily_cases(),
        "hidden_generalization": _hidden_cases(),
        "daily_sequences": _sequences(),
    }
    for key, path in paths.items():
        _write_jsonl(path, rows[key])

    manifest = {
        "schema_version": 3,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "policy": "old repeatedly used inputs are exposed regression; new hidden inputs freeze once and are never prompt-tuning inputs",
        "inputs": {
            key: {
                "path": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "count": len(rows[key]),
                "exposure_counts": dict(Counter(str(row["exposure"]) for row in rows[key])),
            }
            for key, path in paths.items()
        },
        "legacy_inputs": {
            "cases.jsonl": {"count": 72, "exposure": "exposed_regression"},
            "sequences.jsonl": {"count": 6, "exposure": "exposed_regression"},
        },
        "group_rule": "same source, topic, repeated segment, and rewrites share one group",
        "style_pollution_rule": "designed cases, contrast pairs, and hidden inputs have no style_runtime scope",
        "model_operations_at_freeze": "NOT_EXECUTED",
    }
    manifest_path = EVAL / "split_manifest_v3.json"
    if manifest_path.exists():
        raise FileExistsError(f"frozen manifest already exists: {manifest_path}")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
