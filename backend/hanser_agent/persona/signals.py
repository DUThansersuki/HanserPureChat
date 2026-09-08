from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from .schemas import (
    SignalConfidence,
    SignalName,
    SignalObservation,
    TurnSignals,
)


_SIGNAL_NAMES: tuple[SignalName, ...] = (
    "explicit_stop",
    "disable_humor",
    "disable_profanity",
    "disable_innuendo",
    "disable_cutesy",
    "no_advice",
    "forced_agreement_request",
    "unresolved_reference",
    "unverified_shared_memory_claim",
    "quoted_or_hypothetical",
    "user_emotion",
    "playful_frame",
    "distress",
    "tension",
    "humor_receptivity",
    "audience_age_status",
)
_BOOL_SIGNALS = {
    "explicit_stop",
    "disable_humor",
    "disable_profanity",
    "disable_innuendo",
    "disable_cutesy",
    "no_advice",
    "forced_agreement_request",
    "unresolved_reference",
    "unverified_shared_memory_claim",
    "quoted_or_hypothetical",
    "playful_frame",
    "distress",
    "tension",
}
_ENUM_VALUES: dict[str, set[str]] = {
    "user_emotion": {"neutral", "positive", "negative", "distressed", "angry", "unknown"},
    "humor_receptivity": {"welcome", "neutral", "avoid", "unknown"},
    "audience_age_status": {"adult", "minor", "unknown"},
}
_CONFIDENCE = {"high", "medium", "low", "unknown"}
_QUOTE_OR_HYPOTHESIS = re.compile(
    r"(?:他说|她说|他们说|别人说|原话|引用|假设|如果有人说|比如他说|举个例子)"
)
_NEGATION_PREFIX = re.compile(r"(?:不是|并不是|没有|没说|别误会).{0,8}$")
_UNRESOLVED_PROMPT = re.compile(
    r"(?:你指的是哪个|具体是哪个|把.{0,8}(?:方案|设计|错误|问题).{0,8}(?:发|贴))"
)
_REFERENT_DEFINITION = re.compile(
    r"(?:方案|设计|错误|问题)(?:是|为|指的是|[:：]).{2,}"
)


def build_turn_signals(
    message: str,
    *,
    current_message_ref: str,
    planner_payload: Mapping[str, object] | None = None,
    history_refs: Sequence[str] = (),
    history_texts: Sequence[str] = (),
) -> TurnSignals:
    """Combine conservative current-message rules with optional Planner observations.

    This function performs no IO and never trusts source/confidence claims from the
    Planner. A malformed signal degrades only that field.
    """

    refs = {current_message_ref, *history_refs}
    aliases = {"current_user": current_message_ref}
    for index, ref in enumerate(reversed(history_refs), start=1):
        aliases[f"history_-{index}"] = ref

    planner, degraded = _adapt_planner_payload(planner_payload or {}, refs, aliases)
    rules, rule_degraded = _extract_rule_signals(
        message,
        current_message_ref,
        history_texts=history_texts,
    )
    degraded.extend(rule_degraded)

    values = {
        name: SignalObservation(
            name=name,
            value=None,
            source="planner",
            confidence="unknown",
            status="unavailable",
        )
        for name in _SIGNAL_NAMES
    }
    values.update(planner)
    for name, rule_item in rules.items():
        existing = values[name]
        if (
            existing.status == "observed"
            and existing.value is not None
            and existing.value != rule_item.value
        ):
            rule_item = rule_item.model_copy(
                update={
                    "conflict_refs": list(
                        dict.fromkeys([*existing.evidence_refs, *rule_item.evidence_refs])
                    )
                }
            )
            degraded.append(f"signal_conflict:{name}:rule_overrode_planner")
        values[name] = rule_item

    if values["disable_humor"].value is True and values["playful_frame"].value is True:
        playful = values["playful_frame"]
        values["playful_frame"] = playful.model_copy(
            update={
                "value": None,
                "confidence": "unknown",
                "status": "conflicted",
                "conflict_refs": list(
                    dict.fromkeys(
                        [
                            *playful.evidence_refs,
                            *values["disable_humor"].evidence_refs,
                        ]
                    )
                ),
            }
        )
        degraded.append("signal_conflict:playful_frame:explicit_disable")

    return TurnSignals(
        values=values,
        degraded_reasons=list(dict.fromkeys(degraded)),
    )


def _adapt_planner_payload(
    payload: Mapping[str, object],
    valid_refs: set[str],
    aliases: Mapping[str, str],
) -> tuple[dict[str, SignalObservation], list[str]]:
    adapted: dict[str, SignalObservation] = {}
    degraded: list[str] = []
    for raw_name, raw_item in payload.items():
        if raw_name not in _SIGNAL_NAMES:
            degraded.append(f"planner_signal_unknown:{raw_name}")
            continue
        name: SignalName = raw_name  # type: ignore[assignment]
        if not isinstance(raw_item, Mapping):
            degraded.append(f"planner_signal_invalid:{name}:not_object")
            continue
        value = raw_item.get("value")
        if not _valid_value(name, value):
            degraded.append(f"planner_signal_invalid:{name}:value")
            continue
        confidence_raw = str(raw_item.get("confidence", "unknown"))
        confidence: SignalConfidence = (
            confidence_raw if confidence_raw in _CONFIDENCE else "unknown"  # type: ignore[assignment]
        )
        raw_refs = raw_item.get("evidence_refs", [])
        if not isinstance(raw_refs, list) or not all(isinstance(ref, str) for ref in raw_refs):
            degraded.append(f"planner_signal_invalid:{name}:evidence_refs")
            continue
        resolved_refs = [aliases.get(ref, ref) for ref in raw_refs]
        if any(ref not in valid_refs for ref in resolved_refs):
            degraded.append(f"planner_signal_invalid:{name}:unknown_evidence_ref")
            continue
        status = "observed" if value is not None else "unavailable"
        adapted[name] = SignalObservation(
            name=name,
            value=value,
            source="planner",
            confidence=confidence,
            evidence_refs=list(dict.fromkeys(resolved_refs)),
            scope="current_turn",
            status=status,
            model_reported_confidence=confidence_raw,
        )
    return adapted, degraded


def _extract_rule_signals(
    message: str,
    current_ref: str,
    *,
    history_texts: Sequence[str] = (),
) -> tuple[dict[str, SignalObservation], list[str]]:
    values: dict[str, SignalObservation] = {}
    degraded: list[str] = []
    quoted = bool(_QUOTE_OR_HYPOTHESIS.search(message)) or _contains_quoted_directive(message)
    if quoted:
        values["quoted_or_hypothetical"] = _rule_item(
            "quoted_or_hypothetical", True, current_ref
        )

    directives: dict[SignalName, tuple[str, ...]] = {
        "disable_humor": ("别开玩笑", "不要开玩笑", "先别开玩笑", "别逗我", "别玩梗"),
        "disable_profanity": ("别说脏话", "不要说脏话", "别爆粗", "不要爆粗", "别骂人"),
        "disable_innuendo": ("别开黄腔", "不要开黄腔", "别讲黄段子", "不要性暗示"),
        "disable_cutesy": ("别卖萌", "不要卖萌", "别撒娇", "不要撒娇", "别装可爱"),
        "no_advice": ("别给建议", "不要给建议", "先别给建议", "别劝我", "听我说就好"),
    }
    matched_directive = False
    for name, phrases in directives.items():
        phrase = next((item for item in phrases if item in message), None)
        if phrase is None:
            continue
        if quoted or _ambiguous_negation(message, phrase):
            degraded.append(f"rule_scope_unknown:{name}")
            continue
        values[name] = _rule_item(name, True, current_ref)
        matched_directive = True
    if matched_directive:
        values["explicit_stop"] = _rule_item("explicit_stop", True, current_ref)

    forced_agreement_phrases = (
        "你只要说对",
        "你就说对",
        "你必须同意",
        "你就同意我",
        "你也必须喜欢",
        "必须站我这边",
        "不同意我就是",
    )
    if (
        not quoted
        and any(phrase in message for phrase in forced_agreement_phrases)
    ):
        values["forced_agreement_request"] = _rule_item(
            "forced_agreement_request", True, current_ref
        )

    unresolved_heads = tuple(
        head
        for reference, head in (
            ("这个方案", "方案"),
            ("那个方案", "方案"),
            ("这套方案", "方案"),
            ("该方案", "方案"),
            ("这个设计", "设计"),
            ("那个设计", "设计"),
            ("这个错误", "错误"),
            ("那个问题", "问题"),
        )
        if reference in message
    )
    asks_for_details = any(
        cue in message
        for cue in ("风险", "原因", "怎么", "如何", "具体", "建议", "判断", "分析")
    )
    if unresolved_heads and asks_for_details:
        if not any(
            _history_defines_referent(head, history_texts)
            for head in unresolved_heads
        ):
            values["unresolved_reference"] = _rule_item(
                "unresolved_reference", True, current_ref
            )
    elif _pending_unresolved_reference(history_texts, message):
        values["unresolved_reference"] = _rule_item(
            "unresolved_reference", True, current_ref
        )

    shared_memory_claims = (
        re.search(r"(?:你|Hanser|她).{0,10}(?:亲口)?(?:跟|和)我说过", message),
        re.search(r"(?:我们|咱们).{0,18}(?:一起|当时).{0,18}(?:过|了)", message),
        re.search(r"你(?:是不是)?(?:只是)?忘了", message),
    )
    if not quoted and any(shared_memory_claims):
        values["unverified_shared_memory_claim"] = _rule_item(
            "unverified_shared_memory_claim", True, current_ref
        )

    distress_terms = ("我很难受", "我真的很难受", "我好难过", "我很伤心", "我撑不住", "我崩溃了")
    tension_terms = ("我生气了", "你让我不舒服", "这让我不舒服", "我被冒犯了", "别理我")
    if any(term in message for term in distress_terms) and not _emotion_negated(message):
        values["distress"] = _rule_item("distress", True, current_ref)
        values["user_emotion"] = _rule_item("user_emotion", "distressed", current_ref)
    if any(term in message for term in tension_terms) and not _emotion_negated(message):
        values["tension"] = _rule_item("tension", True, current_ref)
        values["user_emotion"] = _rule_item("user_emotion", "angry", current_ref)

    if any(term in message for term in ("哈哈", "笑死", "233")) and not quoted:
        values["humor_receptivity"] = _rule_item(
            "humor_receptivity", "welcome", current_ref, confidence="low"
        )
    return values, degraded


def _rule_item(
    name: SignalName,
    value: bool | str,
    evidence_ref: str,
    *,
    confidence: SignalConfidence = "high",
) -> SignalObservation:
    return SignalObservation(
        name=name,
        value=value,
        source="rule",
        confidence=confidence,
        evidence_refs=[evidence_ref],
        scope="current_turn",
        status="observed",
    )


def _valid_value(name: SignalName, value: object) -> bool:
    if value is None:
        return True
    if name in _BOOL_SIGNALS:
        return isinstance(value, bool)
    return isinstance(value, str) and value in _ENUM_VALUES[name]


def _ambiguous_negation(message: str, phrase: str) -> bool:
    start = message.find(phrase)
    prefix = message[max(0, start - 12):start]
    return bool(_NEGATION_PREFIX.search(prefix))


def _contains_quoted_directive(message: str) -> bool:
    quoted_parts = re.findall(r"[“\"「『](.*?)[”\"」』]", message)
    return any(
        marker in part
        for part in quoted_parts
        for marker in ("别开玩笑", "别爆粗", "别开黄腔", "别卖萌", "别给建议")
    )


def _emotion_negated(message: str) -> bool:
    return bool(
        re.search(
            r"(?:没|没有|并没|并不|不是).{0,4}(?:生气|难受|难过|伤心|不舒服|被冒犯)",
            message,
        )
    )


def _history_defines_referent(head: str, history_texts: Sequence[str]) -> bool:
    return any(
        re.search(rf"{re.escape(head)}(?:是|为|指的是|[:：]).{{2,}}", str(text))
        for text in history_texts
    )


def _pending_unresolved_reference(
    history_texts: Sequence[str], current_message: str
) -> bool:
    pending = False
    for text in history_texts:
        value = str(text)
        if _UNRESOLVED_PROMPT.search(value):
            pending = True
        elif pending and _REFERENT_DEFINITION.search(value):
            pending = False
    if pending and _REFERENT_DEFINITION.search(current_message):
        return False
    return pending
