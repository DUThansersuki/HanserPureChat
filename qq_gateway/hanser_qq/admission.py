"""Bounded literal-payload review, separate from the A13 classifier.

Only a first-clause reaction in the enumerated language below is approved.
The rest of the source A stays unreviewed. This is a local, inspectable review
vocabulary, not a claim that keyword absence proves arbitrary text safe.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


REVIEW_VERSION = "qq-literal-reaction-review-v2"


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: str
    behavior: str
    scene: str
    requires: tuple[tuple[str, bool | str], ...] = ()
    expressions: tuple[str, ...] = ()


RULES = (
    Rule("greeting", r"(?:喂|嗨|哈喽)?(?:大家|各位)?(?:你好|你们好|晚上好|早上好|中午好|下午好|早安)(?:呀|啊|哇|哦|喔|鸭)?",
         "greet_current_partner", "你好 晚上好 早上好 对方直接问候 开场招呼"),
    Rule("closing", r"(?:真的要)?(?:拜拜|晚安|再见|走了|下次见){1,5}(?:啦|呀|啊|咯)?",
         "natural_close", "晚安 拜拜 再见 对方正在结束交流 自然收尾", (("dialogue_function", "end"),)),
    Rule("uptake", r"(?:嗯|好|行|对|是|哦|噢|喔){1,6}(?:呀|啊|吧|哦|啦)?",
         "brief_contextual_uptake", "好的 嗯 知道了 对方确认或短接 接住后不强制追问", (("dialogue_function", "confirm"),)),
    Rule("thanks", r"(?:谢谢|多谢|感谢){1,3}(?:你|你们|啦|呀|啊|哦|夸奖)?",
         "receive_current_praise", "对方夸你 你真好 谢谢你 辛苦你了 接住当前感谢或夸奖"),
    Rule("playful_reaction", r"(?:哈{2,12}|笑死我了|笑死了|好家伙|真的假的|真的吗|你认真的吗|太过分了|不至于吧|怎么会这样|救命|我的天呐|我的天哪|哎哟喂|离谱|离谱了)",
         "light_contextual_tease", "玩笑 调侃 离谱 好笑 对方明确在玩梗 接一拍轻反应",
         (("playful_frame", True),), ("meme",)),
    Rule("low_profanity", r"(?:卧槽|我靠)", "playful_reframe",
         "对方明确允许低强度粗口的轻松玩笑 只感叹事情 不攻击人",
         (("playful_frame", True),), ("profanity",)),
    Rule("listening", r"(?:我听着|你慢慢说|慢慢说|你说吧|你说)", "listen_without_forcing_advice",
         "难过 想说说 听我说 不要建议 只想倾诉 认真倾听",
         (("distress", True),)),
)


def admit(row: dict) -> tuple[dict | None, str]:
    if row.get("label_number") == 13:
        return None, "label_not_recommended"
    if row.get("speaker_candidate") != "hanser":
        return None, "speaker_not_hanser"
    if row.get("attribution_basis") not in {"source_prior", "explicit_marker"}:
        return None, "source_basis_unresolved"
    if row.get("risk_flags"):
        return None, "source_risk_requires_review"
    # No sentence paraphrasing, generated Q, or negative-list approval.
    first = re.match(r"\s*([^，,。.!！?？\n;；~～]{1,40}[，,。.!！?？;；~～]*)", row["text"])
    if first is None:
        return None, "no_bounded_literal_payload"
    fragment = first.group(1).strip()
    normalized = re.sub(r"[\s，,。.!！?？;；~～]", "", fragment)
    rule = next((r for r in RULES if re.fullmatch(r.pattern, normalized)), None)
    if rule is None:
        return None, "payload_requires_semantic_review"
    if rule.id == "uptake" and re.search(r"[?？]", fragment):
        return None, "question_is_not_confirmation"
    span = row["source_spans"][0]
    offset = span["text"].find(fragment)
    if offset < 0:
        return None, "fragment_not_in_single_exact_source_span"
    start = span["start"] + offset
    return {
        "unit_id": row["unit_id"], "source_id": row["source_id"],
        "document_id": row["document_id"], "source_revision": row["source_revision"],
        "fragment": fragment, "start": start, "end": start + len(fragment),
        "rule_id": rule.id, "review_version": REVIEW_VERSION,
        "behavior": rule.behavior, "scene": rule.scene,
        "requires": list(rule.requires), "expressions": list(rule.expressions),
        "attribution_basis": row["attribution_basis"],
        "review_scope": "literal_fragment_only_not_original_A_or_A13_label",
        "limitations": ["agent_defined_bounded_literal_review", "transcript_not_audio_verified"],
    }, "approved_literal_fragment"


def current_role_allows(rule: str, message: str, signals) -> bool:
    # These roles are narrower than the shared signal vocabulary. Quoted or
    # negated mentions of a greeting/thanks are not direct greeting events.
    if rule == "greeting":
        return bool(re.fullmatch(r"[\s喂嗨哈喽呀啊哇哦喔~～!！。,.，]*?(?:你好|晚上好|早上好|早安|中午好|下午好)[呀啊哇哦喔~～!！。,.，\s]*", message))
    if rule == "thanks":
        if re.search(r"[“”\"「」]|别|不要|不想|不用|他说|她说|转述", message):
            return False
        direct = bool(re.search(r"谢谢你|多谢你|辛苦你|夸你|你.{0,8}(?:真好|厉害|可爱|温柔|聪明|好听|会说话)", message))
        frame = signals.get("interaction_frame") if signals else None
        target = signals.get("interaction_target") if signals else None
        return direct or bool(frame and target and frame.status == target.status == "observed"
                              and frame.value == "praise" and target.value == "assistant")
    return True
