from __future__ import annotations

from collections.abc import Sequence

from ..models import ChatMessage
from .schemas import ExpressionObservation, FeatureObservation


DETECTOR_VERSION = "persona_lexical_v1"
_TOKENS: dict[str, tuple[str, ...]] = {
    "meme": ("233", "www", "笑死", "绷不住", "草"),
    "profanity": ("卧槽", "我操", "靠", "妈的", "操蛋"),
    "cutesy": ("人家", "呜呜", "害羞羞", "主人", "撒娇", "捏"),
}


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
        present = [any(token in text for token in tokens) for text in newest_first]
        rate = _weighted_rate(present, recency_decay)
        features[feature] = FeatureObservation(
            feature=feature,  # type: ignore[arg-type]
            value=("present" if any(present) else "absent"),
            weighted_rate=rate,
            observed_turns=len(present),
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
        any(token in text for tokens in _TOKENS.values() for token in tokens)
        for text in newest_first
    ]
    features["strong_marker"] = FeatureObservation(
        feature="strong_marker",
        value=("present" if any(strong_present) else "absent"),
        weighted_rate=_weighted_rate(strong_present, recency_decay),
        observed_turns=len(strong_present),
        detector_version=DETECTOR_VERSION,
    )
    return ExpressionObservation(
        window_turns=len(newest_first),
        features=features,
    )


def _weighted_rate(values: list[bool], decay: float) -> float:
    if not values:
        return 0.0
    weights = [decay**index for index in range(len(values))]
    return round(
        sum(weight for weight, present in zip(weights, values, strict=True) if present)
        / sum(weights),
        6,
    )
