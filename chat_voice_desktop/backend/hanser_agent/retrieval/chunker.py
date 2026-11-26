from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DocumentChunkDraft:
    chunk_index: int
    text: str
    start_char: int
    end_char: int


@dataclass(frozen=True, slots=True)
class _TextUnit:
    start: int
    end: int


_BOUNDARY = re.compile(r"[。！？!?；;：:\n]\s*")


def chunk_document(
    text: str,
    *,
    target_chars: int = 800,
    max_chars: int = 1000,
    overlap_chars: int = 120,
) -> list[DocumentChunkDraft]:
    """Split text at line/sentence boundaries while retaining source offsets."""
    if not text.strip():
        return []

    units = _text_units(text, max_chars)
    chunks: list[DocumentChunkDraft] = []
    current: list[_TextUnit] = []

    def flush() -> None:
        if not current:
            return
        start = current[0].start
        end = current[-1].end
        value = text[start:end].strip()
        if value:
            left_trim = len(text[start:end]) - len(text[start:end].lstrip())
            right_trimmed = text[start:end].rstrip()
            actual_start = start + left_trim
            actual_end = start + len(right_trimmed)
            chunks.append(
                DocumentChunkDraft(
                    chunk_index=len(chunks),
                    text=value,
                    start_char=actual_start,
                    end_char=actual_end,
                )
            )

    for unit in units:
        if current and unit.end - current[0].start > max_chars:
            previous = list(current)
            flush()
            current.clear()
            overlap: list[_TextUnit] = []
            for item in reversed(previous):
                if previous[-1].end - item.start > overlap_chars:
                    break
                overlap.append(item)
            current.extend(reversed(overlap))

        current.append(unit)
        if current[-1].end - current[0].start >= target_chars:
            flush()
            previous = list(current)
            current.clear()
            overlap = []
            for item in reversed(previous):
                if previous[-1].end - item.start > overlap_chars:
                    break
                overlap.append(item)
            current.extend(reversed(overlap))

    if current:
        start = current[0].start
        end = current[-1].end
        if not chunks or (start, end) != (
            chunks[-1].start_char,
            chunks[-1].end_char,
        ):
            flush()
    return chunks


def _text_units(text: str, max_chars: int) -> list[_TextUnit]:
    units: list[_TextUnit] = []
    for match in re.finditer(r"[^\n]+(?:\n|$)", text):
        start, end = match.span()
        while end - start > max_chars:
            window_end = start + max_chars
            boundaries = [
                item.end()
                for item in _BOUNDARY.finditer(text, start, window_end)
                if item.end() >= start + max_chars // 2
            ]
            split_at = boundaries[-1] if boundaries else window_end
            units.append(_TextUnit(start=start, end=split_at))
            start = split_at
        if text[start:end].strip():
            units.append(_TextUnit(start=start, end=end))
    return units
