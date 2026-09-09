from __future__ import annotations

from .validator import StyleValidator, ValidationResult


class DisplayAdapter:
    """Derive legacy display text without mutating canonical semantic text."""

    revision = "display_legacy_sparse_1"

    def __init__(self, validator: StyleValidator):
        self.validator = validator

    def render(
        self,
        semantic_text: str,
        *,
        required_verbatim_spans: list[str] | None = None,
        exact_output: str | None = None,
    ) -> ValidationResult:
        if exact_output is not None:
            return ValidationResult(text=semantic_text)

        protected: list[str] = []
        working = semantic_text
        for span in sorted(required_verbatim_spans or [], key=len, reverse=True):
            if not span or span not in working:
                continue
            token = f"\ue100{len(protected)}\ue101"
            protected.append(span)
            working = working.replace(span, token)
        result = self.validator.normalize(working)
        for index, span in enumerate(protected):
            result.text = result.text.replace(f"\ue100{index}\ue101", span)
        return result
