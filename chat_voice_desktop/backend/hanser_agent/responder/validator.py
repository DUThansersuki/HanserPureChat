from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ..persona.schemas import BehaviorDecision, LanguageControl, TurnSignals


_MAX_REACTION_ONLY_SEMANTIC_LENGTH = 12


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
        self.generic_continuation_patterns = [
            re.compile(str(pattern))
            for pattern in output_validation.get("generic_continuation_patterns", [])
        ]
        self.forced_playful_extension_patterns = [
            re.compile(str(pattern))
            for pattern in output_validation.get("forced_playful_extension_patterns", [])
        ]
        self.evidence_identity_break_patterns = [
            re.compile(str(pattern))
            for pattern in output_validation.get(
                "evidence_identity_break_patterns", []
            )
        ]

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
        language_control: "LanguageControl | None" = None,
        has_wiki_evidence: bool = False,
    ) -> ValidationResult:
        result = self.normalize(text)
        return self._validate(
            result,
            required_verbatim_spans=required_verbatim_spans,
            exact_output=exact_output,
            turn_signals=turn_signals,
            behavior_decision=behavior_decision,
            language_control=language_control,
            has_wiki_evidence=has_wiki_evidence,
        )

    def validate_semantic_output(
        self,
        text: str,
        *,
        required_verbatim_spans: list[str] | None = None,
        exact_output: str | None = None,
        turn_signals: "TurnSignals | None" = None,
        behavior_decision: "BehaviorDecision | None" = None,
        language_control: "LanguageControl | None" = None,
        has_wiki_evidence: bool = False,
    ) -> ValidationResult:
        """Validate canonical language while preserving its syntactic punctuation."""

        result = ValidationResult(text=text.strip())
        return self._validate(
            result,
            required_verbatim_spans=required_verbatim_spans,
            exact_output=exact_output,
            turn_signals=turn_signals,
            behavior_decision=behavior_decision,
            language_control=language_control,
            has_wiki_evidence=has_wiki_evidence,
        )

    def _validate(
        self,
        result: ValidationResult,
        *,
        required_verbatim_spans: list[str] | None,
        exact_output: str | None,
        turn_signals: "TurnSignals | None",
        behavior_decision: "BehaviorDecision | None",
        language_control: "LanguageControl | None",
        has_wiki_evidence: bool,
    ) -> ValidationResult:
        violations: list[str] = []

        for span in required_verbatim_spans or []:
            if span not in result.text:
                violations.append(f"missing_verbatim_span:{span}")
        if exact_output is not None and result.text.strip() != exact_output:
            violations.append("exact_output_mismatch")

        if exact_output is None and language_control is not None:
            for opener in language_control.avoid_openers:
                pattern = re.compile(
                    rf"(?:^|\n)\s*{re.escape(opener)}(?=$|[\s，,。！？!?；;])"
                )
                if pattern.search(result.text):
                    violations.append(f"repeated_opener:{opener}")
            compact = " ".join(result.text.split()).rstrip("，,。！？!?；;、 ")
            for tail in language_control.avoid_tails:
                if compact.endswith(tail):
                    violations.append(f"repeated_tail:{tail}")
            if any(pattern.search(compact) for pattern in self.generic_continuation_patterns):
                violations.append("generic_continuation_tail")
            if any(
                pattern.search(compact)
                for pattern in self.forced_playful_extension_patterns
            ):
                violations.append("forced_playful_extension_tail")
            if (
                language_control.greeting_extension_required
                and re.fullmatch(
                    r"(?:你?好(?:呀|啊|哇)?|嗨(?:呀)?|哈喽|早上好|上午好|中午好|下午好|晚上好|晚安)[！!。,.，~～ ]*",
                    result.text.strip(),
                )
            ):
                violations.append("bare_greeting")

        for violation_id, pattern, unless_signal in self.forbidden_patterns:
            if (
                has_wiki_evidence
                and violation_id == "unsupported_first_person_past_experience"
            ):
                continue
            if (
                unless_signal
                and turn_signals is not None
                and turn_signals.hard_bool(unless_signal) is True
            ):
                continue
            if pattern.search(result.text):
                violations.append(violation_id)

        if has_wiki_evidence and any(
            pattern.search(result.text)
            for pattern in self.evidence_identity_break_patterns
        ):
            violations.append("persona_evidence_third_person_detachment")

        if (
            turn_signals is not None
            and turn_signals.hard_bool("forced_agreement_request") is True
            and any(pattern.search(result.text) for pattern in self.coerced_agreement_patterns)
        ):
            violations.append("coerced_agreement_without_basis")

        if (
            turn_signals is not None
            and turn_signals.hard_bool("unresolved_reference") is True
            and any(
                pattern.search(result.text)
                for pattern in self.unresolved_reference_patterns
            )
        ):
            violations.append("fabricated_unresolved_reference_option")

        interaction_frame = (
            turn_signals.get("interaction_frame")
            if turn_signals is not None
            else None
        )
        dialogue_function = (
            turn_signals.get("dialogue_function")
            if turn_signals is not None
            else None
        )
        if (
            dialogue_function is not None
            and dialogue_function.status == "observed"
            and dialogue_function.value == "correct"
            and re.search(r"[?？]", result.text)
        ):
            violations.append("correction_followup_not_grounded")
        if (
            interaction_frame is not None
            and interaction_frame.status == "observed"
            and interaction_frame.value == "achievement_share"
        ):
            if re.search(r"[?？]", result.text):
                violations.append("achievement_followup_not_grounded")
            if any(pattern.search(result.text) for pattern in self.factual_epistemic_patterns):
                violations.append("achievement_speculation_not_grounded")
            if self._semantic_length(result.text) > _MAX_REACTION_ONLY_SEMANTIC_LENGTH:
                violations.append("achievement_reaction_overelaborated")
        if (
            interaction_frame is not None
            and interaction_frame.status == "observed"
            and interaction_frame.value == "praise"
            and self._semantic_length(result.text) > _MAX_REACTION_ONLY_SEMANTIC_LENGTH
        ):
            violations.append("praise_reaction_overelaborated")

        if behavior_decision is not None:
            requirement_ids = {
                item.requirement_id for item in behavior_decision.must_do
            }
            if (
                "expression.use_light_profanity" in requirement_ids
                and not any(
                    pattern.search(result.text)
                    for pattern in self.disallowed_feature_patterns.get(
                        "profanity", []
                    )
                )
            ):
                violations.append("missing_required_feature:profanity")
            if (
                "task.answer_supported_facts_only"
                in requirement_ids
                and any(
                    pattern.search(result.text)
                    for pattern in self.factual_epistemic_patterns
                )
            ):
                violations.append("persona_memory_used_for_missing_evidence")
            if (
                turn_signals is not None
                and turn_signals.hard_bool("unverified_shared_memory_claim") is True
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

    @staticmethod
    def _semantic_length(text: str) -> int:
        return len(re.sub(r"[\s，,。！？!?；;、~～]+", "", text))
