from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from typing import Iterable

from .models import SearchResult
from .tokenizer import tokenize

SNIPPET_RADIUS = 40
K1 = 1.5
B = 0.75


def make_snippet(text: str, keyword: str, radius: int = SNIPPET_RADIUS) -> str:
    idx = text.find(keyword)
    if idx < 0:
        return ""
    start = max(0, idx - radius)
    end = min(len(text), idx + len(keyword) + radius)
    return ("…" if start else "") + text[start:end].replace("\n", " ") + ("…" if end < len(text) else "")


def query_tokens(keywords: Iterable[str], userdict_path=None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for kw in keywords:
        for token in tokenize(kw, userdict_path):
            if token not in seen:
                seen.add(token)
                out.append(token)
    return out


def search_documents(
    conn: sqlite3.Connection,
    keywords: Iterable[str],
    *,
    top_n: int | None = None,
    userdict_path=None,
) -> list[SearchResult]:
    qtokens = query_tokens(keywords, userdict_path)
    if not qtokens:
        return []

    total_docs = int(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
    if total_docs == 0:
        return []

    avg_row = conn.execute(
        "SELECT AVG(dl) FROM (SELECT SUM(tf) AS dl FROM doc_tokens GROUP BY doc_id)"
    ).fetchone()
    avg_len = float(avg_row[0] or 0.0)

    tf_by_doc: dict[int, dict[str, int]] = defaultdict(dict)
    matched_by_doc: dict[int, list[str]] = defaultdict(list)
    hits_by_doc: dict[int, int] = defaultdict(int)
    df: dict[str, int] = {}

    for qt in qtokens:
        rows = conn.execute("SELECT doc_id, tf FROM doc_tokens WHERE token = ?", (qt,)).fetchall()
        df[qt] = len(rows)
        for row in rows:
            doc_id = int(row["doc_id"])
            tf = int(row["tf"])
            tf_by_doc[doc_id][qt] = tf
            matched_by_doc[doc_id].append(qt)
            hits_by_doc[doc_id] += tf

    if not tf_by_doc:
        return []

    doc_ids = list(tf_by_doc)
    placeholders = ",".join("?" for _ in doc_ids)
    doc_len = {
        int(r["doc_id"]): int(r["dl"])
        for r in conn.execute(
            f"SELECT doc_id, SUM(tf) AS dl FROM doc_tokens WHERE doc_id IN ({placeholders}) GROUP BY doc_id",
            doc_ids,
        )
    }
    docs = {
        int(r["id"]): r
        for r in conn.execute(
            f"SELECT id, filename, filepath, content FROM documents WHERE id IN ({placeholders})",
            doc_ids,
        )
    }

    scored: list[tuple[float, int]] = []
    for doc_id, tfs in tf_by_doc.items():
        dl = doc_len.get(doc_id, 0)
        score = 0.0
        for qt, tf in tfs.items():
            idf = math.log(1 + (total_docs - df[qt] + 0.5) / (df[qt] + 0.5))
            denom = tf + K1 * (1 - B + B * dl / avg_len) if avg_len > 0 else tf + K1
            score += idf * tf * (K1 + 1) / denom
        scored.append((score, doc_id))

    scored.sort(reverse=True)
    if top_n is not None:
        scored = scored[:top_n]

    results: list[SearchResult] = []
    for score, doc_id in scored:
        row = docs[doc_id]
        first = matched_by_doc[doc_id][0]
        snippet = make_snippet(f"{row['filename']}\n{row['content']}", first)
        results.append(
            SearchResult(
                id=doc_id,
                filename=str(row["filename"]),
                filepath=str(row["filepath"]),
                hits=hits_by_doc[doc_id],
                matched=matched_by_doc[doc_id],
                snippet=snippet,
                score=round(score, 4),
            )
        )
    return results


def context_snippets(content: str, keywords: Iterable[str], *, radius: int = 300, max_snippets: int = 3, userdict_path=None) -> list[str]:
    positions: list[int] = []
    for kw in keywords:
        for token in tokenize(kw, userdict_path):
            start = 0
            while True:
                idx = (content or "").find(token, start)
                if idx < 0:
                    break
                positions.append(idx)
                start = idx + len(token)

    if not positions:
        return []

    spans = sorted({(max(0, p - radius), min(len(content), p + radius)) for p in positions})
    merged: list[list[int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    out: list[str] = []
    for start, end in merged[:max_snippets]:
        out.append(("…" if start else "") + content[start:end] + ("…" if end < len(content) else ""))
    return out
