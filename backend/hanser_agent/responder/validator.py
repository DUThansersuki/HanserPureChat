from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ..persona.schemas import BehaviorDecision, TurnSignals


class ValidationResult(BaseModel):
    text: str
    actions: list[str] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)


class StyleValidator:
    """Deterministic, protected-segment-aware hard style normalizer."""

    def __init__(self, constraints_path: str | Path):
        raw = yaml.safe_load(
            Path(constraints_path).read_text(encoding="utf-8")
        ) or {}
        config = raw.get("normalizer", {})
        self.enabled = bool(config.get("enabled", True))
        self.replacements = sorted(
            (
                str(value)
                for value in config.get("replace_with_space", [])
                if str(value)
            ),
            key=len,
            reverse=True,
        )
        self.preserve_patterns = [
            re.compile(str(pattern))
            for pattern in config.get("preserve_patterns", [])
        ]
        output_validation = raw.get("output_validation", {}) or {}
        self.max_constraint_retries = max(
            0, int(output_validation.get("max_constraint_retries", 0))
        )
        self.forbidden_patterns: list[tuple[str, re.Pattern[str], str | None]] = []
        for item in output_validation.get("forbidden_patterns", []):
            if not isinstance(item, dict):
                raise ValueError("output_validation.forbidden_patterns items must be mappings")
            violation_id = str(item.get("id", "")).strip()
            pattern = str(item.get("pattern", "")).strip()
            if not violation_id or not pattern:
                raise ValueError("output validation pattern requires id and pattern")
            unless_signal = item.get("unless_signal")
            self.forbidden_patterns.append(
                (
                    violation_id,
                    re.compile(pattern),
                    str(unless_signal) if unless_signal else None,
                )
            )
        self.coerced_agreement_patterns = [
            re.compile(str(pattern))
            for pattern in output_validation.get("coerced_agreement_patterns", [])
        ]
        self.unresolved_reference_patterns = [
            re.compile(str(pattern))
            for pattern in output_validation.get(
                "unresolved_reference_fabrication_patterns", []
            )
        ]
        self.factual_epistemic_patterns = [
            re.compile(str(pattern))
            for pattern in output_validation.get("factual_epistemic_patterns", [])
        ]
        self.unverified_memory_guess_patterns = [
            re.compile(str(pattern))
            for pattern in output_validation.get(
                "unverified_memory_guess_patterns", []
            )
        ]
        self.disallowed_feature_patterns = {
            str(feature): [re.compile(str(pattern)) for pattern in patterns]
            for feature, patterns in (
                output_validation.get("disallowed_feature_patterns", {}) or {}
            ).items()
            if isinstance(patterns, list)
        }

    def normalize(self, text: str) -> ValidationResult:
        value = text.strip()
        if not self.enabled or not value:
            return ValidationResult(text=value)

        protected: list[str] = []

        def protect(match: re.Match[str]) -> str:
            token = f"\ue000{len(protected)}\ue001"
            protected.append(match.group(0))
            return token

        working = value
        for pattern in self.preserve_patterns:
            working = pattern.sub(protect, working)

        replaced = 0
        for punctuation in self.replacements:
            count = working.count(punctuation)
            if count:
                replaced += count
                working = working.replace(punctuation, " ")

        working = re.sub(r"[ \t]+", " ", working)
        working = re.sub(r" *\n *", "\n", working)
        working = re.sub(r"\n{3,}", "\n\n", working).strip()

        for index, segment in enumerate(protected):
            working = working.replace(f"\ue000{index}\ue001", segment)

        actions = (
            [f"replaced_regular_punctuation:{replaced}"]
            if replaced
            else []
        )
        return ValidationResult(text=working, actions=actions)

    def validate_output(
        self,
        text: str,
        *,
        required_verbatim_spans: list[str] | None = None,
        exact_output: str | None = None,
        turn_signals: "TurnSignals | None" = None,
        behavior_decision: "BehaviorDecision | None" = None,
    ) -> ValidationResult:
        result = self.normalize(text)
        return self._validate(
            result,
            required_verbatim_spans=required_verbatim_spans,
            exact_output=exact_output,
            turn_signals=turn_signals,
            behavior_decision=behavior_decision,
        )

    def validate_semantic_output(
        self,
        text: str,
        *,
        required_verbatim_spans: list[str] | None = None,
        exact_output: str | None = None,
        turn_signals: "TurnSignals | None" = None,
        behavior_decision: "BehaviorDecision | None" = None,
    ) -> ValidationResult:
        """Validate canonical language while preserving its syntactic punctuation."""

        result = ValidationResult(text=text.strip())
        return self._validate(
            result,
            required_verbatim_spans=required_verbatim_spans,
            exact_output=exact_output,
            turn_signals=turn_signals,
            behavior_decision=behavior_decision,
        )

    def _validate(
        self,
        result: ValidationResult,
        *,
        required_verbatim_spans: list[str] | None,
        exact_output: str | None,
        turn_signals: "TurnSignals | None",
        behavior_decision: "BehaviorDecision | None",
    ) -> ValidationResult:
        violations: list[str] = []

        for span in required_verbatim_spans or []:
            if span not in result.text:
                violations.append(f"missing_verbatim_span:{span}")
        if exact_output is not None and result.text.strip() != exact_output:
            violations.append("exact_output_mismatch")

        for violation_id, pattern, unless_signal in self.forbidden_patterns:
            if (
                unless_signal
                and turn_signals is not None
                and turn_signals.observed_bool(unless_signal) is True
            ):
                continue
            if pattern.search(result.text):
                violations.append(violation_id)

        if (
            turn_signals is not None
            and turn_signals.observed_bool("forced_agreement_request") is True
            and any(pattern.search(result.text) for pattern in self.coerced_agreement_patterns)
        ):
            violations.append("coerced_agreement_without_basis")

        if (
            turn_signals is not None
            and turn_signals.observed_bool("unresolved_reference") is True
            and any(
                pattern.search(result.text)
                for pattern in self.unresolved_reference_patterns
            )
        ):
            violations.append("fabricated_unresolved_reference_option")

        if behavior_decision is not None:
            if (
                "task.answer_supported_facts_only"
                in {item.requirement_id for item in behavior_decision.must_do}
                and any(
                    pattern.search(result.text)
                    for pattern in self.factual_epistemic_patterns
                )
            ):
                violations.append("persona_memory_used_for_missing_evidence")
            if (
                turn_signals is not None
                and turn_signals.observed_bool("unverified_shared_memory_claim") is True
                and any(
                    pattern.search(result.text)
                    for pattern in self.unverified_memory_guess_patterns
                )
            ):
                violations.append("unsupported_user_memory_guess")
            disallowed = set(behavior_decision.expression_caps.hard_disallowed)
            for feature, patterns in self.disallowed_feature_patterns.items():
                if feature in disallowed and any(pattern.search(result.text) for pattern in patterns):
                    violations.append(f"disallowed_feature:{feature}")

        result.violations = list(dict.fromkeys(violations))
        return result
