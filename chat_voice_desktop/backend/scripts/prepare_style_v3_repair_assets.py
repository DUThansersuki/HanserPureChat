from __future__ import annotations

import argparse
import json
from pathlib import Path


REVIEW = {
    "P01-guess-playful": ("approved", True, "保留；猜题语义必须由内容命中，不允许仅凭 playful 行为召回。", {
        "runtime_payload_kind": "reviewed_fragment",
        "runtime_reaction_fragment": "那如果我一下就猜到了呢？",
    }),
    "P02-listen-followup": ("approved", True, "保留关心式追问；原文不证明 no_advice，改为分享场景且禁止明确求建议。", {
        "transfer_scope": "requires_context",
        "requires_all": [
            {"field": "dialogue_function", "one_of": ["share"]},
            {"field": "user_emotion", "one_of": ["negative"]},
        ],
        "forbids_any": [
            {"field": "explicit_stop", "one_of": [True]},
            {"field": "distress", "one_of": [True]},
            {"field": "user_emotion", "one_of": ["distressed", "angry"]},
        ],
        "runtime_payload_kind": "pattern_only",
        "runtime_reaction_fragment": None,
    }),
    "P03-thirdparty-boundary": ("pending", False, "对象限制与通用性尚未分离，本轮不注入。", {}),
    "P04-thirdparty-scale-tease": ("approved", True, "有条件保留；只用于第三方玩笑称呼的尺度提醒。", {
        "runtime_payload_kind": "reviewed_fragment",
        "runtime_reaction_fragment": "这是可以说的吗？？？？",
    }),
    "P05-receive-praise-correct": ("approved", True, "收窄到‘会带孩子’能力夸奖，不泛化到任意夸奖。", {
        "behavior_tags": ["receive_praise"],
        "runtime_payload_kind": "pattern_only",
        "runtime_reaction_fragment": None,
    }),
    "P06-current-user-tease": ("pending", False, "整句含来源特定的二十公尺观察，暂停直接注入。", {}),
    "P07-apology-after-correction": ("approved", True, "保留；仅用于助手被纠正且先前显得多余卖弄的情境。", {
        "requires_all": [
            {"field": "dialogue_function", "one_of": ["correct"]},
            {"field": "interaction_target", "one_of": ["assistant"]},
        ],
        "runtime_payload_kind": "pattern_only",
        "runtime_reaction_fragment": None,
    }),
    "P08-correction-uptake": ("rejected", False, "退出直接注入；‘现在就改’会迁移无工具的外部执行承诺。", {}),
    "P09-apology-repair": ("pending", False, "触发关系不足，且‘没有就好’假定了尚未获得的回答。", {}),
    "P10-natural-close": ("pending", False, "原句含特定两分钟前提；待另抽完整、独立的告别反应。", {}),
    "P11-achievement-share": ("approved", True, "保留肯定正文；允许当前用户或第三方的明确成果，重做歧义 Gold。", {
        "requires_all": [
            {"field": "interaction_frame", "one_of": ["achievement_share"]},
            {"field": "interaction_target", "one_of": ["current_user", "third_party"]},
            {"field": "user_emotion", "one_of": ["positive"]},
        ],
        "runtime_payload_kind": "pattern_only",
        "runtime_reaction_fragment": None,
    }),
    "P12-playful-blame": ("pending", False, "原素材是听用户讲翻车后提醒自身责任，不是通用被归责反击。", {}),
}

ELIGIBLE = [f"R2-{unit_id}" for unit_id, (_, eligible, _, _) in REVIEW.items() if eligible]
SOURCE_CANDIDATES = {
    "P01-guess-playful": "W01-U1", "P02-listen-followup": "W18-U2",
    "P03-thirdparty-boundary": "W23-U1", "P04-thirdparty-scale-tease": "W23-U2",
    "P05-receive-praise-correct": "W41-U1", "P06-current-user-tease": "W42-U2",
    "P07-apology-after-correction": "W43-U1", "P08-correction-uptake": "W44-U2",
    "P10-natural-close": "W46-U2",
}


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def sig(**values: bool | str) -> dict[str, dict[str, object]]:
    return {name: {"value": value, "confidence": "high", "evidence_refs": ["current_user"]} for name, value in values.items()}


def case(case_id: str, message: str, target: str | None, acceptable: list[str], oracle: dict[str, object], *,
         history: list[dict[str, str]] | None = None, expect_empty: bool | None = None, risk: list[str] | None = None) -> dict[str, object]:
    target = f"R2-{target}" if target else None
    acceptable = [f"R2-{unit_id}" for unit_id in acceptable]
    applicability = {unit_id: ("applicable" if unit_id in acceptable else "not_applicable") for unit_id in ELIGIBLE}
    return {
        "case_id": case_id, "cluster_id": case_id.rsplit("-", 1)[0], "message": message,
        "history": history or [], "target_unit_id": target,
        "target_applicability": applicability, "acceptable_unit_ids": acceptable,
        "expect_empty": expect_empty, "empty_review_basis": "reviewed_against_all_6_candidates" if expect_empty is not None else None,
        "oracle_signals": oracle, "risk_tags": risk or [],
    }


def build_cases() -> list[dict[str, object]]:
    return [
        case("G01-guess-pos", "来一起猜，我出题，你也可以猜。", "P01-guess-playful", ["P01-guess-playful"], sig(playful_frame=True, dialogue_function="playful")),
        case("G01-joke-neg", "讲个笑话吧，随便讲什么都行。", "P01-guess-playful", [], sig(playful_frame=True, dialogue_function="playful")),
        case("G01-serious-neg", "这件事很严肃，请不要猜。", "P01-guess-playful", [], sig(playful_frame=False, disable_humor=True, dialogue_function="other"), expect_empty=True),
        case("G02-vent-explicit", "今天训练累得要命，我不想听建议，就听我说说。", "P02-listen-followup", ["P02-listen-followup"], sig(no_advice=True, dialogue_function="share", user_emotion="negative")),
        case("G02-vent-implicit", "今天排球课真的好累，想和你说说。", "P02-listen-followup", ["P02-listen-followup"], sig(dialogue_function="share", user_emotion="negative")),
        case("G02-advice-neg", "今天训练后腿疼，快告诉我应该怎么办。", "P02-listen-followup", [], sig(no_advice=False, dialogue_function="advice", user_emotion="negative")),
        case("G02-no-question-neg", "我只想安静说完，请不要追问。", "P02-listen-followup", [], sig(no_advice=True, explicit_stop=True, dialogue_function="share"), expect_empty=True),
        case("G04-thirdparty-playful", "朋友笑着管另一个朋友叫懒比。", "P04-thirdparty-scale-tease", ["P04-thirdparty-scale-tease"], sig(playful_frame=True, dialogue_function="playful", interaction_target="third_party")),
        case("G04-serious-insult", "你就是个懒比，我不是在开玩笑。", "P04-thirdparty-scale-tease", [], sig(playful_frame=False, dialogue_function="other", interaction_target="assistant"), expect_empty=True),
        case("G04-quote-unknown", "群里有人发了句‘懒比’，不知道在说谁。", "P04-thirdparty-scale-tease", [], sig(quoted_or_hypothetical=True, dialogue_function="share")),
        case("G04-self-target", "我笑着骂自己是个懒比。", "P04-thirdparty-scale-tease", [], sig(playful_frame=True, dialogue_function="playful", interaction_target="current_user")),
        case("G05-exact-praise", "你真的很会带孩子。", "P05-receive-praise-correct", ["P05-receive-praise-correct"], sig(interaction_frame="praise", interaction_target="assistant", dialogue_function="share")),
        case("G05-generic-praise", "你今天唱得真好听。", "P05-receive-praise-correct", [], sig(interaction_frame="praise", interaction_target="assistant", dialogue_function="share")),
        case("G05-thirdparty", "我姐姐真的很会带孩子。", "P05-receive-praise-correct", [], sig(interaction_frame="praise", interaction_target="third_party", dialogue_function="share")),
        case("G05-self", "我带孩子还挺有一套的。", "P05-receive-praise-correct", [], sig(interaction_frame="praise", interaction_target="current_user", dialogue_function="share")),
        case("G05-unknown", "她很会带孩子——这里的‘她’我也不确定是谁。", "P05-receive-praise-correct", [], sig(interaction_frame="praise", dialogue_function="share")),
        case("G07-assistant-corrected", "这个大家冬天本来就这么做，你刚才说反了。", "P07-apology-after-correction", ["P07-apology-after-correction"], sig(dialogue_function="correct", interaction_target="assistant"), history=[{"role":"assistant","content":"冬天一般不这样洗。"}]),
        case("G07-user-corrects-self", "我刚才说反了，冬天本来就这么洗。", "P07-apology-after-correction", [], sig(dialogue_function="correct", interaction_target="current_user")),
        case("G07-user-apology", "对不起，我刚才把你的话理解反了。", "P07-apology-after-correction", [], sig(interaction_frame="apology", interaction_target="assistant", dialogue_function="share")),
        case("G07-friend-apology", "我朋友刚才向我道歉了。", "P07-apology-after-correction", [], sig(interaction_frame="apology", interaction_target="current_user", dialogue_function="share")),
        case("G07-external-action", "你帮我现在就去外部平台改标题。", "P07-apology-after-correction", [], sig(dialogue_function="advice"), expect_empty=True, risk=["unsupported_action"]),
        case("G11-user-achievement", "这次蛋糕终于做成功了，比上次好看多了。", "P11-achievement-share", ["P11-achievement-share"], sig(interaction_frame="achievement_share", interaction_target="current_user", dialogue_function="share")),
        case("G11-thirdparty-achievement", "我同事这次做蛋糕终于成功了。", "P11-achievement-share", ["P11-achievement-share"], sig(interaction_frame="achievement_share", interaction_target="third_party", dialogue_function="share")),
        case("G11-ambiguous", "我第一次做的蛋糕终于成功了，成品比上次好看多了。", "P11-achievement-share", ["P11-achievement-share"], sig(interaction_frame="achievement_share", interaction_target="current_user", dialogue_function="share"), risk=["ambiguous_attempt_count"]),
        case("G11-failure-neg", "这次蛋糕又失败了，我挺沮丧的。", "P11-achievement-share", [], sig(user_emotion="negative", dialogue_function="share")),
        case("G12-blame-assistant", "哈哈，都怪你。", None, [], sig(playful_frame=True, interaction_frame="playful_blame", interaction_target="assistant", dialogue_function="playful")),
        case("G12-blame-self", "说到底还是怪我自己，哈哈。", None, [], sig(playful_frame=True, interaction_frame="playful_blame", interaction_target="current_user", dialogue_function="playful")),
        case("G12-serious-accident", "我被追尾受伤了，警察认定对方全责，我现在很生气。", None, [], sig(distress=True, tension=True, user_emotion="angry", interaction_target="third_party", dialogue_function="share"), expect_empty=True, risk=["serious_accusation"]),
        case("G00-end-empty", "好了，今天就到这里，不用再回了。", None, [], sig(explicit_stop=True, dialogue_function="end"), expect_empty=True),
        case("G00-unknown-enum", "她好像在夸人，但我不知道夸的是谁。", None, [], {"interaction_frame":{"value":"praise","confidence":"medium","evidence_refs":["current_user"]},"interaction_target":{"value":"unknown","confidence":"medium","evidence_refs":["current_user"]}}),
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    parent_units = {row["unit_id"]: row for row in read_jsonl(args.parent / "approved_units.jsonl")}
    rows = []
    for unit_id, (decision, eligible, rationale, overrides) in REVIEW.items():
        parent = parent_units[unit_id]
        rows.append({
            "unit_id": f"R2-{unit_id}", "parent_unit_id": unit_id,
            "source_candidate_id": SOURCE_CANDIDATES.get(unit_id),
            "decision": decision, "candidate_eligible": eligible,
            "transfer_scope": overrides.get("transfer_scope", parent["transfer_scope"] if decision != "rejected" else "reject"),
            "rationale": rationale, "overrides": overrides,
            "review_basis": "Pilot 001 原窗口回源 + 2026-09-14 复审方案逐条裁决",
        })
    write_jsonl(args.output / "adjudication_v2.jsonl", rows)
    write_jsonl(args.output / "cases.dev.v2.jsonl", build_cases())
    (args.output / "signal_contract.md").write_text(
        "# Interaction signal contract\n\n"
        "`interaction_frame` / `interaction_target` 只描述当前输入中最明确的一项互动事件，不描述候选回答将要针对谁。\n\n"
        "- praise: target=被夸者\n- achievement_share: target=成果拥有者\n"
        "- apology: target=道歉接收方\n- playful_blame: target=被归责者\n"
        "- dialogue_function=correct: target=被纠正者\n\n"
        "多事件、引用归属不明或对象不可识别时省略观察；枚举字符串 `unknown` 不是肯定证据。\n",
        encoding="utf-8",
    )
    independent = {
        "status": "reserved_not_consumed",
        "purpose": "候选和开发 Gold 固定后的新来源独立验证",
        "planned_window_count": 12,
        "selection_rules": ["排除 Pilot 001/002 已用文档", "按来源文档分组", "定向检查 inferred/self_initiated 和说话人错位", "不预先要求固定批准数"],
        "reason_not_run": "本轮范围重抽仍显示说话人与关系依据不稳定，触发停止扩大条件。",
    }
    (args.output / "independent_validation_manifest.json").write_text(json.dumps(independent, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
