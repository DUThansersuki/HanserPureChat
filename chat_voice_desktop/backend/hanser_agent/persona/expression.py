from __future__ import annotations

import re
from collections.abc import Sequence

from ..models import ChatMessage
from .schemas import (
    ExpressionObservation,
    FeatureObservation,
    LanguageControl,
    TurnSignals,
)


DETECTOR_VERSION = "persona_lexical_v4"
_TOKENS: dict[str, tuple[re.Pattern[str], ...]] = {
    "meme": tuple(re.compile(value) for value in (
        r"233", r"www", r"笑死", r"绷不住",
        r"(?:^|[\s，。！？!?])草(?:$|[\s，。！？!?])",
    )),
    "profanity": tuple(re.compile(value) for value in (
        r"卧槽", r"我操", r"我靠(?=$|[\s，。！？!?])", r"妈的", r"他妈", r"他娘的", r"操蛋",
        r"牛逼", r"傻逼", r"艹",
        r"(?:^|[\s，。！？!?])靠(?:$|[\s，。！？!?])",
        r"(?:^|[\s，。！？!?“”])老(?:子|娘)(?=(?:我|今天|现在|就|偏|还|都|真|可|要|不|是|也|给|跟|没|才|最|直接|先|让|这|那|受|当然|愿意|想|觉得|必须|绝不|懒得|服了|$|[\s，。！？!?“”]))",
    )),
    "cutesy": tuple(re.compile(re.escape(value)) for value in (
        "人家", "呜呜", "害羞羞", "主人", "撒娇", "捏",
    )),
}

_OPENER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("诶呀", re.compile(r"^诶呀(?=$|[\s，,。！？!?；;])")),
    ("行吧", re.compile(r"^行吧(?=$|[\s，,。！？!?；;])")),
    ("行了", re.compile(r"^行了(?=$|[\s，,。！？!?；;])")),
    ("好吧", re.compile(r"^好吧(?=$|[\s，,。！？!?；;])")),
    ("那就", re.compile(r"^那就(?=$|[\s，,。！？!?；;])")),
    ("行", re.compile(r"^行(?=$|[\s，,。！？!?；;])")),
)
_TAIL_MARKERS = (
    "你还想聊点什么",
    "还想聊点什么",
    "下一个话题 你说吧",
    "下一个话题你说吧",
    "你说吧",
    "满意没",
)


def observe_recent_expressions(
    history: Sequence[ChatMessage],
    *,
    window_turns: int = 6,
    recency_decay: float = 0.7,
) -> ExpressionObservation:
    """Observe reliable lexical markers in persisted assistant turns only.

    Innuendo stays unknown because lexical matches cannot establish the semantic
    category. Missing semantic coverage is reported, never treated as absence.
    """

    assistant_turns = [item.content for item in history if item.role == "assistant"][-window_turns:]
    newest_first = list(reversed(assistant_turns))
    features: dict[str, FeatureObservation] = {}
    for feature, tokens in _TOKENS.items():
        present = [any(token.search(text) for token in tokens) for text in newest_first]
        rate = _weighted_rate(present, recency_decay)
        features[feature] = FeatureObservation(
            feature=feature,  # type: ignore[arg-type]
            value=("present" if any(present) else "absent"),
            weighted_rate=rate,
            observed_turns=len(present),
            last_seen_turns_ago=(present.index(True) if any(present) else None),
            detector_version=DETECTOR_VERSION,
        )

    features["innuendo"] = FeatureObservation(
        feature="innuendo",
        value="unknown",
        weighted_rate=None,
        observed_turns=0,
        unknown_turns=len(newest_first),
        detector_version=DETECTOR_VERSION,
    )
    strong_present = [
        any(token.search(text) for tokens in _TOKENS.values() for token in tokens)
        for text in newest_first
    ]
    features["strong_marker"] = FeatureObservation(
        feature="strong_marker",
        value=("present" if any(strong_present) else "absent"),
        weighted_rate=_weighted_rate(strong_present, recency_decay),
        observed_turns=len(strong_present),
        last_seen_turns_ago=(
            strong_present.index(True) if any(strong_present) else None
        ),
        detector_version=DETECTOR_VERSION,
    )
    recent_openers = [
        opener
        for text in assistant_turns
        for opener in _turn_openers(text)
    ]
    recent_tails = [
        marker
        for text in assistant_turns
        for marker in _turn_tails(text)
    ]
    recent_four_openers = [
        opener
        for text in assistant_turns[-4:]
        for opener in _turn_openers(text)
    ]
    xing_family_count = sum(
        opener in {"行", "行吧", "行了"} for opener in recent_four_openers
    )
    avoid_openers: list[str] = []
    if xing_family_count >= 2:
        avoid_openers.extend(["行", "行吧", "行了"])
    for marker in ("好吧", "那就", "诶呀"):
        if recent_four_openers.count(marker) >= 2:
            avoid_openers.append(marker)

    return ExpressionObservation(
        window_turns=len(newest_first),
        features=features,
        recent_example_ids=[
            example_id
            for item in history
            if item.role == "assistant"
            for example_id in item.style_example_ids
        ][-window_turns * 3:],
        recent_openers=recent_openers,
        recent_tails=recent_tails,
        avoid_openers=avoid_openers,
        avoid_tails=list(dict.fromkeys(recent_tails))[-6:],
    )


def build_language_control(
    observations: ExpressionObservation,
    signals: TurnSignals,
    *,
    planner_dialogue_function: str = "unknown",
    planner_greeting_turn: bool = False,
) -> LanguageControl:
    function = planner_dialogue_function
    observed_function = signals.get("dialogue_function")
    if (
        observed_function is not None
        and observed_function.status == "observed"
        and isinstance(observed_function.value, str)
    ):
        function = observed_function.value

    if signals.hard_bool("unresolved_reference") is True:
        continuation_policy = "clarification_required"
    elif function in {"share", "confirm", "correct", "end"}:
        continuation_policy = "stop"
    elif function == "playful":
        continuation_policy = "continue_playful"
    else:
        continuation_policy = "optional_coda"
    return LanguageControl(
        continuation_policy=continuation_policy,
        greeting_extension_required=planner_greeting_turn,
        avoid_openers=observations.avoid_openers,
        avoid_tails=observations.avoid_tails,
    )


def _turn_openers(text: str) -> list[str]:
    openers: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        for marker, pattern in _OPENER_PATTERNS:
            if pattern.search(line):
                openers.append(marker)
                break
    return list(dict.fromkeys(openers))


def _turn_tails(text: str) -> list[str]:
    compact = " ".join(text.split()).rstrip("，,。！？!?；;、 ")
    return [marker for marker in _TAIL_MARKERS if compact.endswith(marker)]


def _weighted_rate(values: list[bool], decay: float) -> float:
    if not values:
        return 0.0
    weights = [decay**index for index in range(len(values))]
    return round(
        sum(weight for weight, present in zip(weights, values, strict=True) if present)
        / sum(weights),
        6,
    )
