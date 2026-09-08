from __future__ import annotations

import re


_EXACT_INTENT_MARKERS = (
    "原样",
    "一字不改",
    "逐字",
    "照抄",
    "不要改",
    "别改",
    "保持不变",
)

_VERBATIM_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"```[\s\S]{1,512}?```",
        r"`[^`\n]{1,512}`",
        r"https?://[^\s，。！？；：]{1,512}",
        r"\[[^\]\n]{1,256}\]\([^\)\n]{1,512}\)",
        r"(?<!\d)\d{4}\s*[-–—]\s*\d{4}(?!\d)",
        r"(?<!\d)\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?(?!\d)",
        r"《[^》\n]{1,256}》",
        r"“[^”\n]{1,512}”",
        r"「[^」\n]{1,512}」",
        r"『[^』\n]{1,512}』",
    )
)

_COMMAND_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"(?:命令|就写)\s+([A-Za-z][A-Za-z0-9._/\\-]*(?:\s+(?:--?[A-Za-z0-9._/\\-]+|[A-Za-z0-9._/\\-]+)){1,8})",
    )
)

_EXACT_OUTPUT_COMMAND = re.compile(
    r"(?:只(?:输出|回复|写)|就写)\s+"
    r"([A-Za-z][A-Za-z0-9._/\\-]*(?:\s+(?:--?[A-Za-z0-9._/\\-]+|[A-Za-z0-9._/\\-]+)){1,8})"
)


def extract_required_verbatim_spans(message: str) -> list[str]:
    """Return bounded, explicitly requested spans that can be checked exactly.

    This intentionally does not try to infer arbitrary semantic preservation.
    It only activates on explicit copy/preserve wording and on formal spans whose
    byte-for-byte preservation is a documented output contract.
    """

    if not any(marker in message for marker in _EXACT_INTENT_MARKERS):
        return []

    matches: list[tuple[int, int, str]] = []
    for pattern in _VERBATIM_PATTERNS:
        for match in pattern.finditer(message):
            matches.append((match.start(), match.end(), match.group(0)))
    for pattern in _COMMAND_PATTERNS:
        for match in pattern.finditer(message):
            matches.append((match.start(1), match.end(1), match.group(1)))
    matches.sort(key=lambda item: (item[0], -(item[1] - item[0])))

    selected: list[tuple[int, int, str]] = []
    for start, end, value in matches:
        if any(start >= old_start and end <= old_end for old_start, old_end, _ in selected):
            continue
        selected.append((start, end, value))
        if len(selected) == 8:
            break
    return [value for _, _, value in selected]


def extract_exact_output(message: str) -> str | None:
    """Return a narrow exact-output contract for explicit command-only requests."""

    match = _EXACT_OUTPUT_COMMAND.search(message)
    return match.group(1).strip() if match else None
