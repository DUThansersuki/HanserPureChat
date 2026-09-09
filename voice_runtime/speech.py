from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .contracts import (
    AllowedPerformance,
    PerformanceIntent,
    SegmentFeatures,
    SourceMapEntry,
    SpeechPlan,
    SpeechSegment,
)


_SPECIAL = re.compile(
    r"(?P<fence>```[\s\S]*?```)"
    r"|(?P<link>\[(?P<link_text>[^\]\n]+)\]\((?P<link_url>https?://[^)\s]+)\))"
    r"|(?P<url>https?://[^\s<>()]+)"
    r"|(?P<inline>`[^`\n]+`)"
    r"|(?P<path>(?:[A-Za-z]:\\|/)(?:[^\s/\\]+[/\\]){2,}[^\s]*)"
    r"|(?P<hash>\b[0-9a-fA-F]{32,}\b)"
)
_DATE = re.compile(r"(?<!\d)(?P<year>\d{4})[-/.](?P<month>\d{1,2})[-/.](?P<day>\d{1,2})(?!\d)")
_MARKDOWN_MARKER = re.compile(r"(?m)(?<!\\)(?:\*\*|__|~~|(?<=^)# {1,3}|(?<=\n)# {1,3})")
_SENTENCE_END = set("。！？!?；;\n")
_SOFT_END = set("，,、：:")


@dataclass(slots=True)
class _Builder:
    semantic_text: str
    speech_parts: list[str]
    mapping: list[SourceMapEntry]
    display_only: list[tuple[int, int]]
    speech_length: int = 0

    def add(
        self,
        start: int,
        end: int,
        output: str,
        *,
        operation: str,
        precision: str,
        rule_id: str,
    ) -> None:
        speech_start = self.speech_length
        self.speech_parts.append(output)
        self.speech_length += len(output)
        self.mapping.append(
            SourceMapEntry(
                semantic_span=(start, end),
                speech_span=(speech_start, self.speech_length),
                operation=operation,
                precision=precision,
                rule_id=rule_id,
                rule_version="speech_rules_1",
            )
        )
        if operation == "omit":
            self.display_only.append((start, end))


class SpeechPlanner:
    """Deterministic semantic-to-speech compiler; never generates new dialogue."""

    mapping_revision = "speech_mapper_1"

    def __init__(
        self,
        *,
        preferred_segment_chars: int = 60,
        soft_max_chars: int = 80,
        hard_max_chars: int = 120,
    ):
        self.preferred_segment_chars = preferred_segment_chars
        self.soft_max_chars = soft_max_chars
        self.hard_max_chars = hard_max_chars

    def plan(
        self,
        reply_id: str,
        semantic_text: str,
        allowed: AllowedPerformance,
    ) -> SpeechPlan:
        builder = _Builder(semantic_text, [], [], [])
        cursor = 0
        for match in _SPECIAL.finditer(semantic_text):
            self._plain(builder, cursor, match.start())
            kind = match.lastgroup
            if kind == "link":
                anchor_start = match.start("link_text")
                anchor_end = match.end("link_text")
                builder.add(
                    match.start(),
                    anchor_start,
                    "",
                    operation="omit",
                    precision="exact",
                    rule_id="markdown_link_syntax",
                )
                builder.add(
                    anchor_start,
                    anchor_end,
                    match.group("link_text"),
                    operation="copy",
                    precision="exact",
                    rule_id="markdown_link_anchor",
                )
                builder.add(
                    anchor_end,
                    match.end(),
                    "",
                    operation="omit",
                    precision="exact",
                    rule_id="markdown_link_target",
                )
            elif kind == "url":
                builder.add(
                    match.start(),
                    match.end(),
                    "链接",
                    operation="replace",
                    precision="rule_based",
                    rule_id="bare_url_placeholder",
                )
            else:
                builder.add(
                    match.start(),
                    match.end(),
                    "",
                    operation="omit",
                    precision="exact",
                    rule_id=f"display_only_{kind}",
                )
            cursor = match.end()
        self._plain(builder, cursor, len(semantic_text))

        speech_text = "".join(builder.speech_parts)
        degraded: list[str] = []
        if builder.display_only:
            degraded.append("display_only_content_omitted")
        if not speech_text.strip():
            return SpeechPlan(
                reply_id=reply_id,
                semantic_text=semantic_text,
                speech_text="",
                source_map=builder.mapping,
                segments=[],
                display_only_spans=builder.display_only,
                degraded_reasons=[*degraded, "no_speakable_content"],
                mapping_revision=self.mapping_revision,
            )

        ranges = self._segment_ranges(speech_text)
        segments: list[SpeechSegment] = []
        for index, (start, end) in enumerate(ranges):
            text = speech_text[start:end]
            semantic_spans = self._semantic_spans(builder.mapping, start, end)
            segments.append(
                SpeechSegment(
                    reply_id=reply_id,
                    segment_id=f"{reply_id}:s{index}",
                    index=index,
                    semantic_spans=semantic_spans,
                    speech_span=(start, end),
                    speech_text=text,
                    language=self._language(text),
                    allowed_performance=PerformanceIntent(
                        delivery=allowed.delivery,
                        intensity=allowed.intensity,
                    ),
                    features=SegmentFeatures(
                        codepoint_length=len(text),
                        utterance_type=self._utterance_type(text),
                        rhythm_type="unknown",
                    ),
                    target_gap_ms=(0 if index == len(ranges) - 1 else self._gap(text)),
                    mapping_revision=self.mapping_revision,
                )
            )
        return SpeechPlan(
            reply_id=reply_id,
            semantic_text=semantic_text,
            speech_text=speech_text,
            source_map=builder.mapping,
            segments=segments,
            display_only_spans=builder.display_only,
            degraded_reasons=degraded,
            mapping_revision=self.mapping_revision,
        )

    def _plain(self, builder: _Builder, start: int, end: int) -> None:
        cursor = start
        text = builder.semantic_text
        for date in _DATE.finditer(text, start, end):
            self._plain_without_dates(builder, cursor, date.start())
            spoken = self._date_text(
                date.group("year"), date.group("month"), date.group("day")
            )
            builder.add(
                date.start(),
                date.end(),
                spoken,
                operation="replace",
                precision="rule_based",
                rule_id="date_ymd_zh",
            )
            cursor = date.end()
        self._plain_without_dates(builder, cursor, end)

    def _plain_without_dates(self, builder: _Builder, start: int, end: int) -> None:
        cursor = start
        text = builder.semantic_text
        for marker in _MARKDOWN_MARKER.finditer(text, start, end):
            if marker.start() < cursor:
                continue
            self._plain_characters(builder, cursor, marker.start())
            builder.add(
                marker.start(),
                marker.end(),
                "",
                operation="omit",
                precision="exact",
                rule_id="markdown_marker",
            )
            cursor = marker.end()
        self._plain_characters(builder, cursor, end)

    @staticmethod
    def _plain_characters(builder: _Builder, start: int, end: int) -> None:
        run_start = start
        text = builder.semantic_text
        for index in range(start, end):
            character = text[index]
            if SpeechPlanner._is_emoji(character):
                if run_start < index:
                    builder.add(
                        run_start,
                        index,
                        text[run_start:index],
                        operation="copy",
                        precision="exact",
                        rule_id="plain_text",
                    )
                builder.add(
                    index,
                    index + 1,
                    "",
                    operation="omit",
                    precision="rule_based",
                    rule_id="emoji_omit",
                )
                run_start = index + 1
        if run_start < end:
            builder.add(
                run_start,
                end,
                text[run_start:end],
                operation="copy",
                precision="exact",
                rule_id="plain_text",
            )

    def _segment_ranges(self, text: str) -> list[tuple[int, int]]:
        ranges: list[tuple[int, int]] = []
        start = 0
        length = len(text)
        while start < length:
            remaining = length - start
            if remaining <= self.soft_max_chars:
                ranges.append((start, length))
                break
            search_end = min(length, start + self.soft_max_chars)
            preferred = min(search_end, start + self.preferred_segment_chars)
            end = self._last_boundary(text, start + 1, search_end, _SENTENCE_END)
            if end <= start:
                end = self._last_boundary(text, start + 1, search_end, _SOFT_END)
            if end <= start:
                end = self._last_boundary(text, start + 1, search_end, {" ", "\t"})
            if end <= start:
                end = max(preferred, min(length, start + self.hard_max_chars))
                end = self._safe_boundary(text, end)
            ranges.append((start, end))
            start = end
        return ranges

    @staticmethod
    def _last_boundary(text: str, start: int, end: int, markers: set[str]) -> int:
        for index in range(end - 1, start - 1, -1):
            if text[index] in markers:
                return index + 1
        return -1

    @staticmethod
    def _safe_boundary(text: str, boundary: int) -> int:
        while boundary > 0 and boundary < len(text):
            character = text[boundary]
            previous = text[boundary - 1]
            if unicodedata.combining(character) or previous == "\u200d":
                boundary -= 1
                continue
            break
        return boundary

    @staticmethod
    def _semantic_spans(
        mapping: list[SourceMapEntry], speech_start: int, speech_end: int
    ) -> list[tuple[int, int]]:
        spans = [
            item.semantic_span
            for item in mapping
            if item.operation != "omit"
            and item.speech_span[0] < speech_end
            and item.speech_span[1] > speech_start
        ]
        return spans or [(0, 0)]

    @staticmethod
    def _utterance_type(text: str) -> str:
        has_question = any(marker in text for marker in "?？")
        has_statement = any(marker in text for marker in "。.!！")
        if has_question and has_statement:
            return "mixed"
        return "question" if has_question else "statement"

    @staticmethod
    def _gap(text: str) -> int:
        stripped = text.rstrip()
        if stripped and stripped[-1] in _SENTENCE_END:
            return 300
        return 180

    @staticmethod
    def _language(text: str) -> str:
        ja = any("\u3040" <= char <= "\u30ff" for char in text)
        zh = any("\u4e00" <= char <= "\u9fff" for char in text)
        en = any(char.isascii() and char.isalpha() for char in text)
        if sum((ja, zh, en)) > 1:
            return "mixed"
        if ja:
            return "ja"
        if zh:
            return "zh"
        if en:
            return "en"
        return "unknown"

    @staticmethod
    def _is_emoji(character: str) -> bool:
        codepoint = ord(character)
        return (
            0x1F000 <= codepoint <= 0x1FAFF
            or 0x2600 <= codepoint <= 0x27BF
        )

    @staticmethod
    def _date_text(year: str, month: str, day: str) -> str:
        digits = "零一二三四五六七八九"

        def small(value: str) -> str:
            number = int(value)
            if number < 10:
                return digits[number]
            if number < 20:
                return "十" + (digits[number - 10] if number > 10 else "")
            tens, ones = divmod(number, 10)
            return digits[tens] + "十" + (digits[ones] if ones else "")

        return "".join(digits[int(value)] for value in year) + "年" + small(month) + "月" + small(day) + "日"
