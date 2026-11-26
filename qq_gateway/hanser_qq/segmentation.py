"""Deterministic semantic boundaries; preserve words, literals and source order."""
from __future__ import annotations

import random
import re

from .config import SegmentedReplySettings


_LITERALS = re.compile(r"```[\s\S]*?(?:```|$)|`[^`\n]*`|https?://[^\s<>，。！？；]+")
_PAIRS = {"（": "）", "(": ")", "[": "]", "【": "】", "{": "}",
          "“": "”", "「": "」", "『": "』", "《": "》", '"': '"'}
_DELIMITERS = " \t\r\n，,。！？!?；;：:.…~～"
_SHORT_ACK = re.compile(r"(?:好[的呀啊哇哦]?|可以[的呀啊]?|行[呀啊吧]?|嗯+|嗯哼|没问题|没事[的儿]?|"
    r"别急|不急|确实|也是|那倒是|对[啊呀的]?|是[啊呀的]|收到|知道了|懂了|明白了|哈哈+|嘿嘿|"
    r"哎呀|啊这|诶|欸|辛苦了|厉害[呀啊]?|太好了|我觉得(?:可以|行))")


def _split_short(text, boundaries):
    clauses, start = [], 0
    for cut, strength in [*boundaries, (len(text), 0)]:
        if text[start:cut].strip(_DELIMITERS):
            clauses.append([cut, strength])
        elif clauses:
            clauses[-1][0] = cut
        start = cut
    if len(clauses) < 3:
        return [text]
    cut, strength = clauses[0]
    lead = text[:cut].strip(_DELIMITERS)
    # Short acknowledgement + a coherent explanation. Weak boundaries need
    # a complete lead; never separate “如果…” or an unfinished noun phrase.
    dependent = re.match(r"^(?:如果|虽然|因为|只要|假如|为了|当|既然)", lead)
    complete = strength < 0 or _SHORT_ACK.fullmatch(lead) or re.search(r"[了啦呢吧呀啊哦哇]$|(?:不错|有意思|好玩|好累|开心)$", lead)
    if 2 <= len(lead) <= 12 and len(text[cut:].strip()) >= 8 and complete and not dependent:
        return [text[:cut], text[cut:]]
    return [text]


def split_semantic(text: str, settings: SegmentedReplySettings, *, protected_spans=()) -> list[str]:
    """Return exact contiguous slices; max_chars is a soft limit for intact units."""
    if settings.max_segments == 1 and not settings.strict_short_bubbles:
        return [text]
    protected = [(m.start(), m.end()) for m in _LITERALS.finditer(text)]
    for span in protected_spans:
        if span:
            protected.extend((m.start(), m.end()) for m in re.finditer(re.escape(span), text))
    protected.sort()
    boundaries, stack = [], []
    pos = 0
    while pos < len(text):
        covering = next((end for start, end in protected if start <= pos < end), None)
        if covering is not None:
            pos = covering
            continue
        char = text[pos]
        if stack and char == stack[-1]:
            stack.pop()
        elif char in _PAIRS:
            stack.append(_PAIRS[char])
        if not stack:
            if char in "。！？!?；;\n" or (char == "." and (pos + 1 == len(text) or text[pos + 1].isspace())):
                boundaries.append((pos + 1, -8 if char == "\n" else -4))
            elif char == "…" and (pos + 1 == len(text) or text[pos + 1] != "…"):
                boundaries.append((pos + 1, -4))
            elif char in "，,：:":
                boundaries.append((pos + 1, 5))
            elif char.isspace() and pos and pos + 1 < len(text):
                # Legacy sparse Chinese clauses may already contain spaces.
                # English phrases and code spaces are not candidate boundaries.
                before = text[:pos].rstrip()
                after = text[pos:].lstrip()
                if before and after and '\u4e00' <= before[-1] <= '\u9fff' and '\u4e00' <= after[0] <= '\u9fff':
                    boundaries.append((pos + 1, 8))
        pos += 1
    if settings.strict_short_bubbles:
        return _short_bubbles(text, boundaries)
    if len(text) < settings.min_reply_chars or len(text) <= settings.target_chars + settings.min_segment_chars // 2:
        return _split_short(text, boundaries) if settings.split_short_clauses else [text]
    pieces, start = [], 0
    while len(pieces) < settings.max_segments - 1:
        if len(text) - start <= settings.target_chars + settings.min_segment_chars // 2:
            break
        candidates = [(cut, penalty) for cut, penalty in boundaries
            if settings.min_segment_chars <= cut - start <= settings.max_chars
            and len(text) - cut >= settings.min_segment_chars]
        if not candidates:
            # A long unbroken quote/code block stays intact. Its closing
            # sentence boundary can still separate it from following prose.
            candidates = [(cut, penalty) for cut, penalty in boundaries
                if cut - start > settings.max_chars and len(text) - cut >= settings.min_segment_chars]
            if not candidates:
                break
            cut = candidates[0][0]
        else:
            sentences = [item for item in candidates if item[1] <= 0]
            if sentences:
                candidates = sentences
            cut = min(candidates, key=lambda item: (abs(item[0] - start - settings.target_chars) + item[1], item[0]))[0]
        pieces.append(text[start:cut])
        start = cut
    pieces.append(text[start:])
    return pieces


def _short_bubbles(text, boundaries):
    # A long indivisible clause stands alone. Never merge a third clause or
    # exceed 15 visible characters by combining clauses. Preserve exact slices.
    units, start = [], 0
    for cut in sorted({cut for cut, _ in boundaries} | {len(text)}):
        value = text[start:cut]
        if value.strip(_DELIMITERS + "…~～"):
            units.append(value)
        elif units:
            units[-1] += value
        else:
            continue  # Keep leading whitespace/punctuation in the first slice.
        start = cut
    pieces, current, count = [], "", 0
    def size(value):
        return len(re.sub(r"[\s，,。.!！?？;；：:、…~～]", "", value))
    for unit in units:
        if current and (count == 2 or size(current + unit) > 15):
            pieces.append(current)
            current, count = "", 0
        current += unit
        count += 1
    if current:
        pieces.append(current)
    return pieces or [text]


def render_reply_segments(snapshot, request_message, display_text, settings, display_adapter):
    """Render only after the complete reply has passed the existing validator."""
    from hanser_agent.persona.output_contract import extract_exact_output, extract_required_verbatim_spans
    if extract_exact_output(request_message) is not None:
        return [display_text]
    spans = extract_required_verbatim_spans(request_message)
    raw = split_semantic(snapshot.semantic_text, settings, protected_spans=spans)
    if len(raw) == 1:
        return [display_text]
    persona = snapshot.persona_trace.get("effective_persona") or {}
    punctuation = persona.get("display_punctuation", "legacy_sparse")
    parts = [display_adapter.render(part, required_verbatim_spans=spans,
                                   punctuation_mode=punctuation).text for part in raw]
    return [part for part in parts if part.strip()]


def send_delays(parts: list[str], settings: SegmentedReplySettings) -> list[float]:
    return [0.0] + [max(settings.min_interval_seconds, min(settings.max_interval_seconds,
        len(part) * settings.seconds_per_char + random.uniform(-0.1, 0.1))) for part in parts[1:]]
