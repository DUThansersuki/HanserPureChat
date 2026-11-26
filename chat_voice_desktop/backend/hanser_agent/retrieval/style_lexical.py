from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..tokenizer import STOP_WORDS, ensure_userdict


_ASCII_WORD = re.compile(r"[a-z0-9][a-z0-9._+-]*", re.IGNORECASE)
_CJK_RUN = re.compile(r"[\u3400-\u9fff]+")


@dataclass(frozen=True, slots=True)
class StyleLexicalMatch:
    raw_score: float
    meaningful_terms: tuple[str, ...]
    auxiliary_bigrams: tuple[str, ...]
    coverage: float
    field_scores: dict[str, float]


@dataclass(frozen=True, slots=True)
class StyleLexicalIndex:
    """Revision-scoped immutable lexical index for the Style corpus."""

    tokenized: dict[str, dict[str, tuple[str, ...]]]
    document_frequency: Counter[str]
    field_average_lengths: dict[str, float]
    candidate_bigrams: dict[str, frozenset[str]]
    total: int

    @classmethod
    def build(
        cls,
        documents: Mapping[str, Mapping[str, str]],
        *,
        userdict_path: str | Path | None = None,
    ) -> "StyleLexicalIndex":
        ensure_userdict(userdict_path)
        tokenized = {
            item_id: {
                field: style_terms(text)
                for field, text in fields.items()
                if text
            }
            for item_id, fields in documents.items()
        }
        document_frequency: Counter[str] = Counter()
        for fields in tokenized.values():
            present = set().union(*(set(tokens) for tokens in fields.values())) if fields else set()
            document_frequency.update(present)
        field_average_lengths: dict[str, float] = {}
        for field in {field for fields in tokenized.values() for field in fields}:
            lengths = [len(fields[field]) for fields in tokenized.values() if field in fields]
            field_average_lengths[field] = sum(lengths) / len(lengths)
        candidate_bigrams = {
            item_id: frozenset().union(*(
                auxiliary_bigrams(text) for text in fields.values() if text
            ))
            for item_id, fields in documents.items()
        }
        return cls(
            tokenized=tokenized,
            document_frequency=document_frequency,
            field_average_lengths=field_average_lengths,
            candidate_bigrams=candidate_bigrams,
            total=len(documents),
        )

    def score(self, query: str) -> dict[str, StyleLexicalMatch]:
        query_terms = style_terms(query)
        if not query_terms or not self.tokenized:
            return {}
        query_set = set(query_terms)
        query_bigrams = set(auxiliary_bigrams(query))
        results: dict[str, StyleLexicalMatch] = {}
        weights = {"trigger_text": 1.0, "retrieval_description": 0.7}
        for item_id, fields in self.tokenized.items():
            matched: set[str] = set()
            field_scores: dict[str, float] = {}
            for field, tokens in fields.items():
                counts = Counter(tokens)
                score = 0.0
                average_length = self.field_average_lengths[field]
                for term in query_terms:
                    frequency = counts.get(term, 0)
                    if not frequency:
                        continue
                    matched.add(term)
                    df = self.document_frequency[term]
                    inverse_frequency = math.log(
                        1 + (self.total - df + 0.5) / (df + 0.5)
                    )
                    denominator = frequency + 1.5 * (
                        0.25 + 0.75 * len(tokens) / average_length
                    )
                    score += inverse_frequency * frequency * 2.5 / denominator
                if score:
                    field_scores[field] = score * weights.get(field, 0.25)
            auxiliary = tuple(sorted(query_bigrams.intersection(
                self.candidate_bigrams.get(item_id, frozenset())
            )))
            raw = sum(field_scores.values())
            if raw or auxiliary:
                results[item_id] = StyleLexicalMatch(
                    raw_score=raw,
                    meaningful_terms=tuple(term for term in query_terms if term in matched),
                    auxiliary_bigrams=auxiliary,
                    coverage=len(matched) / len(query_set),
                    field_scores=field_scores,
                )
        return results


def style_terms(text: str, userdict_path: str | Path | None = None) -> tuple[str, ...]:
    """Content words for Style matching, without search-mode substring expansion."""

    import jieba

    ensure_userdict(userdict_path)
    normalized = (text or "").casefold()
    terms = [
        word.strip()
        for word in jieba.cut(normalized, cut_all=False)
        if len(word.strip()) > 1 and word.strip() not in STOP_WORDS
    ]
    terms.extend(_ASCII_WORD.findall(normalized))
    return tuple(dict.fromkeys(term for term in terms if any(char.isalnum() for char in term)))


def auxiliary_bigrams(text: str) -> tuple[str, ...]:
    normalized = (text or "").casefold()
    return tuple(dict.fromkeys(
        run[index:index + 2]
        for run in _CJK_RUN.findall(normalized)
        for index in range(len(run) - 1)
    ))


def score_style_fields(
    query: str,
    documents: Mapping[str, Mapping[str, str]],
    *,
    userdict_path: str | Path | None = None,
) -> dict[str, StyleLexicalMatch]:
    """Return raw, field-aware lexical evidence for Style candidates."""

    if not documents:
        return {}
    return StyleLexicalIndex.build(
        documents, userdict_path=userdict_path
    ).score(query)
