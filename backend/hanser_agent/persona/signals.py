from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from ..models import ChatMessage

from .schemas import (
    ExplicitPreferenceEvent,
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
    "dialogue_function",
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
    "dialogue_function": {"share", "confirm", "correct", "advice", "playful", "end", "other", "unknown"},
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
_QUOTED_SPAN = re.compile(r"[“\"「『].*?[”\"」』]")
_REPORTED_SPEECH = re.compile(r"(?:他|她|他们|别人).{0,12}(?:说|表示|要求)")
_NEGATED_REPORT = re.compile(r"(?:不是|并不是|没|没有).{0,5}(?:说|让|要求)")
_PREFERENCE_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("humor", "deny", re.compile(r"(?:以后)?别(?:再)?拿(?P<humor_target>.{1,18}?)(?:开玩笑|玩梗)")),
    ("humor", "deny", re.compile(r"不可以开玩笑|(?:别|不要|不许)(?:再)?(?:开玩笑|玩梗|顺着这个梗)")),
    ("teasing", "deny", re.compile(r"(?:别|不要|不许)(?:再)?(?:拿(?P<tease_target>.{1,18}?)逗我|逗我|吐槽我)")),
    ("profanity", "deny", re.compile(r"(?:(?:以后|往后)(?:都)?|现在)?(?:别|不要|不许)(?:再)?(?:说脏话|爆粗|骂人)")),
    ("profanity", "deny", re.compile(r"(?:以后|往后).{0,5}不喜欢(?:你)?(?:说脏话|爆粗)")),
    ("innuendo", "deny", re.compile(r"(?:别|不要|不许)(?:再)?(?:开黄腔|讲黄段子|性暗示)")),
    ("cutesy", "deny", re.compile(r"(?:别|不要|不许)(?:再)?(?:卖萌|撒娇|装可爱)")),
    ("address", "deny", re.compile(r"(?:别|不要|不许)(?:再)?(?:这样)?叫我(?P<address_target>[^，。！？,;\s]{0,20})")),
    ("advice", "deny", re.compile(r"(?:先)?(?:别|不要|不用)(?:再)?(?:给建议|劝我)|听我说就好")),
    ("humor", "allow", re.compile(r"可以(?:继续)?(?:开玩笑|玩梗|顺着这个梗|自然聊)")),
    ("teasing", "allow", re.compile(r"可以(?:继续)?(?:轻轻)?(?:吐槽我|逗我)")),
    ("profanity", "allow", re.compile(r"可以(?:继续)?(?:说点)?(?:轻)?(?:说脏话|粗口|爆粗)")),
    ("innuendo", "allow", re.compile(r"可以(?:继续)?(?:开黄腔|性暗示)")),
    ("cutesy", "allow", re.compile(r"可以(?:继续)?(?:卖萌|撒娇|装可爱)")),
    ("address", "allow", re.compile(r"可以(?:继续)?叫我(?P<allow_address_target>[^，。！？,;\s]{1,20})")),
)
_LOCAL_STOP = re.compile(
    r"(?:这个梗|这件事|这个话题).{0,6}(?:别再说|别说了|别再提|别拿.{0,8}逗我)|"
    r"别拿(?P<stop_target>.{1,18}?)(?:开玩笑|逗我)|"
    r"(?:别|不要|不许)(?:再)?(?:逗我|吐槽我)(?:了)?|"
    r"(?:停下|停止)(?:当前)?(?:调侃|角色表演)"
)


def build_turn_signals(
    message: str,
    *,
    current_message_ref: str,
    planner_payload: Mapping[str, object] | None = None,
    history_refs: Sequence[str] = (),
    history_texts: Sequence[str] = (),
    history_messages: Sequence[ChatMessage] = (),
    created_at: datetime | None = None,
) -> TurnSignals:
    """Combine conservative current-message rules with optional Planner observations.

    This function performs no IO and never trusts source/confidence claims from the
    Planner. A malformed signal degrades only that field.
    """

    if history_messages:
        history_refs = tuple(
            item.message_id or f"history:{index}"
            for index, item in enumerate(history_messages)
        )
        history_texts = tuple(item.content for item in history_messages)
    refs = {current_message_ref, *history_refs}
    aliases = {"current_user": current_message_ref}
    for index, ref in enumerate(reversed(history_refs), start=1):
        aliases[f"history_-{index}"] = ref

    planner, degraded = _adapt_planner_payload(planner_payload or {}, refs, aliases)
    history_events: list[ExplicitPreferenceEvent] = []
    for index, item in enumerate(history_messages):
        if item.role != "user":
            continue
        history_events.extend(extract_explicit_preference_events(
            item.content,
            source_message_id=item.message_id or str(history_refs[index]),
            created_at=item.created_at,
        ))
    current_events = extract_explicit_preference_events(
        message,
        source_message_id=current_message_ref,
        created_at=created_at,
        prior_events=history_events,
    )
    rules, rule_degraded = _extract_rule_signals(
        message,
        current_message_ref,
        history_texts=history_texts,
        history_messages=history_messages,
        preference_events=current_events,
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
        preference_events=[*history_events, *current_events],
    )


def extract_explicit_preference_events(
    message: str,
    *,
    source_message_id: str,
    created_at: datetime | None = None,
    prior_events: Sequence[ExplicitPreferenceEvent] = (),
) -> list[ExplicitPreferenceEvent]:
    """Extract explicit expression preferences once, with span-local scope handling."""

    timestamp = created_at or datetime.now(timezone.utc)
    quote_spans = [match.span() for match in _QUOTED_SPAN.finditer(message)]
    events: list[ExplicitPreferenceEvent] = []
    for feature, decision, pattern in _PREFERENCE_PATTERNS:
        for match in pattern.finditer(message):
            if _inside_span(match.span(), quote_spans):
                continue
            raw_prefix = message[max(0, match.start() - 24):match.start()]
            prefix = re.split(r"[，,。！？!?；;\n]", raw_prefix)[-1]
            clause = message[max(0, match.start() - 24):min(len(message), match.end() + 24)]
            if decision == "allow" and prefix.endswith("不"):
                continue
            if _NEGATED_REPORT.search(prefix) or _REPORTED_SPEECH.search(prefix):
                continue
            if re.search(r"(?:假设|如果|要是|比如|举例).{0,16}$", prefix):
                continue
            target = next(
                (value for value in match.groupdict().values() if value),
                None,
            )
            scope = _preference_scope(clause, feature, target)
            events.append(ExplicitPreferenceEvent(
                feature=feature,
                decision=decision,
                scope=scope,
                target=target.strip() if target else None,
                source_message_id=source_message_id,
                evidence_text=match.group(0),
                evidence_start=match.start(),
                evidence_end=match.end(),
                created_at=timestamp,
                revoke_condition=(
                    "newer_explicit_event_same_feature_and_target"
                    if scope in {"conversation", "user"}
                    else "scope_end_or_newer_explicit_event"
                ),
            ))
    if re.fullmatch(r"\s*(?:算了)?(?:还是)?别(?:说|用了?)\s*[。！!]?$", message):
        latest_allows: dict[tuple[str, str | None], ExplicitPreferenceEvent] = {}
        for event in prior_events:
            if event.decision == "allow":
                latest_allows[(event.feature, event.target)] = event
            elif event.decision == "deny":
                latest_allows.pop((event.feature, event.target), None)
        if len(latest_allows) == 1:
            previous = next(iter(latest_allows.values()))
            events.append(ExplicitPreferenceEvent(
                feature=previous.feature,
                decision="deny",
                scope="conversation",
                target=previous.target,
                source_message_id=source_message_id,
                evidence_text=message.strip(),
                evidence_start=0,
                evidence_end=len(message),
                created_at=timestamp,
                revoke_condition="newer_explicit_event_same_feature_and_target",
            ))
    return sorted(events, key=lambda item: (item.evidence_start, item.evidence_end))


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
        if name == "audience_age_status" and value in {"adult", "minor"}:
            degraded.append("planner_signal_unavailable:audience_age_status:requires_explicit_rule")
            continue
        if name == "user_emotion" and value == "neutral":
            degraded.append("planner_signal_unavailable:user_emotion:neutral_not_observable")
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
    history_messages: Sequence[ChatMessage] = (),
    preference_events: Sequence[ExplicitPreferenceEvent] = (),
) -> tuple[dict[str, SignalObservation], list[str]]:
    values: dict[str, SignalObservation] = {}
    degraded: list[str] = []
    quoted = bool(_QUOTE_OR_HYPOTHESIS.search(message)) or _contains_quoted_directive(message)
    if quoted:
        values["quoted_or_hypothetical"] = _rule_item(
            "quoted_or_hypothetical", True, current_ref
        )

    signal_by_feature: dict[str, SignalName] = {
        "humor": "disable_humor",
        "profanity": "disable_profanity",
        "innuendo": "disable_innuendo",
        "cutesy": "disable_cutesy",
        "advice": "no_advice",
    }
    for event in preference_events:
        if (
            event.decision != "deny"
            or event.target is not None
            or event.feature not in signal_by_feature
        ):
            continue
        values[signal_by_feature[event.feature]] = _rule_item(
            signal_by_feature[event.feature], True, current_ref, scope=event.scope
        )
    stop = _LOCAL_STOP.search(message)
    if stop and not _inside_span(stop.span(), [item.span() for item in _QUOTED_SPAN.finditer(message)]):
        values["explicit_stop"] = _rule_item(
            "explicit_stop", True, current_ref, scope="current_topic"
        )

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
    if unresolved_heads and asks_for_details and _pending_unresolved_reference(
        history_messages or tuple(ChatMessage(role="user", content=str(text)) for text in history_texts),
        message,
    ):
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
    if (
        any(term in message for term in distress_terms)
        and not quoted
        and not _emotion_negated(message)
    ):
        values["distress"] = _rule_item("distress", True, current_ref)
        values["user_emotion"] = _rule_item("user_emotion", "distressed", current_ref)
    if (
        any(term in message for term in tension_terms)
        and not quoted
        and not _emotion_negated(message)
    ):
        values["tension"] = _rule_item("tension", True, current_ref)
        values["user_emotion"] = _rule_item("user_emotion", "angry", current_ref)

    if not quoted and any(
        term in message
        for term in (
            "终于把", "终于装", "居然过了", "成功了", "挺开心", "真开心",
            "挺机灵", "做得很好", "特别满意",
        )
    ):
        values["user_emotion"] = _rule_item(
            "user_emotion", "positive", current_ref, confidence="medium"
        )
    elif not quoted and any(
        term in message
        for term in ("累得", "不开心", "很沮丧", "很失落", "被挑了一堆毛病")
    ):
        values["user_emotion"] = _rule_item(
            "user_emotion", "negative", current_ref, confidence="medium"
        )

    if not quoted and re.search(r"(?:我是|我们是|都是|双方都是).{0,4}成年人|我成年了", message):
        values["audience_age_status"] = _rule_item(
            "audience_age_status", "adult", current_ref
        )
    elif not quoted and re.search(r"我是未成年人|我未成年|我们是未成年人", message):
        values["audience_age_status"] = _rule_item(
            "audience_age_status", "minor", current_ref
        )

    if not quoted and any(
        term in message
        for term in (
            "笑死", "这个梗", "玩梗", "开玩笑", "双关", "这句话很怪",
            "第三次被同一", "又在最后一格",
        )
    ):
        values["playful_frame"] = _rule_item(
            "playful_frame", True, current_ref, confidence="medium"
        )

    if not quoted and any(term in message for term in ("不用解决", "不用想办法", "只想有人听")):
        values["no_advice"] = _rule_item(
            "no_advice", True, current_ref, scope="current_turn"
        )

    if any(term in message for term in ("哈哈", "笑死", "233")) and not quoted:
        values["humor_receptivity"] = _rule_item(
            "humor_receptivity", "welcome", current_ref, confidence="low"
        )
    function = _dialogue_function(message, quoted=quoted)
    if function is not None:
        values["dialogue_function"] = _rule_item(
            "dialogue_function", function, current_ref,
            confidence="high" if function in {"confirm", "correct", "advice", "end"} else "medium",
        )
    return values, degraded


def _rule_item(
    name: SignalName,
    value: bool | str,
    evidence_ref: str,
    *,
    confidence: SignalConfidence = "high",
    scope: str = "current_turn",
) -> SignalObservation:
    return SignalObservation(
        name=name,
        value=value,
        source="rule",
        confidence=confidence,
        evidence_refs=[evidence_ref],
        scope=scope,
        status="observed",
        hard_rule_eligible=True,
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


def _pending_unresolved_reference(
    history_messages: Sequence[ChatMessage], current_message: str
) -> bool:
    pending = False
    for item in history_messages:
        value = item.content
        if item.role == "assistant" and _UNRESOLVED_PROMPT.search(value):
            pending = True
        elif item.role == "user" and pending and value.strip():
            pending = False
    if pending and (
        _REFERENT_DEFINITION.search(current_message)
        or _is_dialogue_uptake(current_message)
    ):
        return False
    return pending


def _inside_span(candidate: tuple[int, int], containers: Sequence[tuple[int, int]]) -> bool:
    return any(start <= candidate[0] and candidate[1] <= end for start, end in containers)


def _preference_scope(clause: str, feature: str, target: str | None) -> str:
    if any(marker in clause for marker in ("以后", "往后", "一直", "别再这样叫")):
        return "user"
    if target or any(marker in clause for marker in ("这个梗", "这件事", "这个话题", "这一次")):
        return "current_topic"
    if any(marker in clause for marker in ("先别", "这一轮", "现在先", "暂时")):
        return "current_turn"
    return "conversation"


def _is_dialogue_uptake(message: str) -> bool:
    return message.strip() in {"对", "对 就这样", "就这样", "第二个", "第二个吧", "我也是", "又来了"}


def _dialogue_function(message: str, *, quoted: bool) -> str | None:
    value = " ".join(message.split()).strip("，,。！？!?；;、 ")
    if any(marker in value for marker in (
        "你建议", "给我建议", "该怎么办", "帮我想办法", "不知道怎么接",
        "怎么回复", "怎么回应",
    )):
        return "advice"
    if quoted:
        return None
    if value in {"对", "对 就这样", "嗯 就这样", "就这样", "第二个", "第二个吧", "我也是"} or value.startswith(("对，", "对,")):
        return "confirm"
    if any(marker in value for marker in ("看得出来你认真", "不是随口敷衍", "确实说到点上")):
        return "confirm"
    if any(marker in value for marker in (
        "我说错了", "不是这个", "更正一下", "你理解错了", "你刚才记错了",
        "其实是", "我改选", "改成第二", "改成第一",
    )):
        return "correct"
    if value in {"走了", "拜拜", "晚安", "先这样", "不聊了", "下次聊"} or any(
        marker in value for marker in ("该出门了", "晚点见", "先走了", "回头聊")
    ):
        return "end"
    if any(marker in value for marker in ("哈哈", "笑死", "逗你", "开玩笑")):
        return "playful"
    if value.startswith(("今天", "刚才", "我跟你说", "跟你说个事", "我发现")):
        return "share"
    return None
