from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path

from ..tokenizer import STOP_WORDS, ensure_userdict


_ASCII_WORD = re.compile(r"[a-z0-9][a-z0-9._+-]*", re.IGNORECASE)
_CJK_RUN = re.compile(r"[\u3400-\u9fff]+")


def lexical_tokens(
    text: str,
    userdict_path: str | Path | None = None,
) -> tuple[str, ...]:
    """Tokenize short conversational text without loading an ML model."""

    import jieba

    ensure_userdict(userdict_path)
    normalized = (text or "").casefold()
    tokens = [
        word.strip()
        for word in jieba.cut_for_search(normalized)
        if len(word.strip()) > 1 and word.strip() not in STOP_WORDS
    ]
    tokens.extend(_ASCII_WORD.findall(normalized))
    for run in _CJK_RUN.findall(normalized):
        if len(run) == 1 and run not in STOP_WORDS:
            tokens.append(run)
        elif len(run) > 1:
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tuple(token for token in tokens if any(char.isalnum() for char in token))


def bm25_scores(
    query: str | Iterable[str],
    documents: Mapping[str, tuple[str, ...]],
    *,
    userdict_path: str | Path | None = None,
) -> dict[str, float]:
    """Return normalized BM25 scores for a small in-memory corpus."""

    query_tokens = (
        lexical_tokens(query, userdict_path)
        if isinstance(query, str)
        else tuple(query)
    )
    terms = tuple(dict.fromkeys(query_tokens))
    if not terms or not documents:
        return {}
    term_set = set(terms)
    frequencies: dict[str, Counter[str]] = {}
    document_frequency: Counter[str] = Counter()
    for item_id, tokens in documents.items():
        counts = Counter(token for token in tokens if token in term_set)
        if counts:
            frequencies[item_id] = counts
            document_frequency.update(counts.keys())
    average_length = sum(len(tokens) for tokens in documents.values()) / len(documents)
    document_lengths = {item_id: len(tokens) for item_id, tokens in documents.items()}
    total = len(documents)
    raw_scores: dict[str, float] = {}
    for item_id, counts in frequencies.items():
        length = document_lengths[item_id]
        score = 0.0
        for term in terms:
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            df = document_frequency[term]
            inverse_frequency = math.log(1 + (total - df + 0.5) / (df + 0.5))
            denominator = frequency + 1.5 * (
                0.25 + 0.75 * length / average_length
            )
            score += inverse_frequency * frequency * 2.5 / denominator
        if score > 0:
            raw_scores[item_id] = score
    if not raw_scores:
        return {}
    maximum = max(raw_scores.values())
    return {item_id: round(score / maximum, 6) for item_id, score in raw_scores.items()}
