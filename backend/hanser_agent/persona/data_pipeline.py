from __future__ import annotations

import json
import hashlib
import re
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .. import db
from ..models import StyleExample
from ..retrieval.embedding import Embedder
from ..retrieval.vector_store import VectorStore


STYLE_COLLECTION = "style_examples"

_DATE = re.compile(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日")
_TIMESTAMP = re.compile(r"(?<!\d)\d{1,2}[:.：,，]\d{2}(?:[:.]\d{2})?[；;]?\s*")
_INLINE_BULLET = re.compile(
    r"[（(](?:读)?弹幕[：:]\s*(?P<prompt>[^）)]{1,120})[）)]\s*(?P<response>.*)"
)
_BULLET_LINE = re.compile(r"^(?:读)?弹幕[：:]\s*(?P<prompt>.{1,120})$")
_HANSER_PREFIX = re.compile(r"^(?:憨憨|憨色|Hanser|hanser|憨|H|h)[：:]\s*")
_HANSER_MARKER = re.compile(r"(?:憨憨|憨色|Hanser|hanser|憨|H|h)[：:]\s*")
_OTHER_SPEAKER = re.compile(
    r"(?:^|\s)(?:S|M|毛|7|嘉宾|主持|旁白|海|凉果|凉菓|于尔丹)[：:]",
    re.IGNORECASE,
)
_ANY_SPEAKER = re.compile(
    r"[（(](?:读)?弹幕[：:]|(?:读)?弹幕[：:]|"
    r"(?:憨憨|憨色|Hanser|hanser|憨|H|h|S|M|毛|7|嘉宾|主持|旁白|海|凉果|凉菓|于尔丹)[：:]|"
    r"(?:^|\s)[QA][：:]",
    re.IGNORECASE,
)
_PAREN_BULLET = re.compile(r"[（(](?:读)?弹幕[：:]\s*(?P<prompt>[^）)]{1,120})[）)]")
_PLAIN_BULLET = re.compile(r"(?:^|\s)(?:读)?弹幕[：:]\s*")
_QA_PROMPT = re.compile(r"(?:^|\s)Q[：:]\s*(?P<prompt>.{1,120}?)\s+A[：:]\s*", re.IGNORECASE)
_HTML_COMMENT = re.compile(r"<!--.*?-->")
_SPACE = re.compile(r"\s+")
_GREETING = re.compile(
    r"^(?:大家|憨憨|hanser)?\s*(?:晚上好|早上好|中午好|下午好|你好|晚安|好久不见|早安|早|嗨|哈[啰喽])"
    r"(?:呀|啊|哦|啦|哈|鸭|哇|[！!。.?？\s])*$",
    re.IGNORECASE,
)
_GREETING_PREFIX = re.compile(
    r"^(?:大家|憨憨|hanser)?\s*(?:晚上好|早上好|中午好|下午好|你好|晚安|"
    r"好久不见|早安|早呀|早啊|嗨|哈[啰喽])(?:\s|[，,！!。.?？呀啊哦啦哈鸭哇])",
    re.IGNORECASE,
)
_COMFORT = re.compile(
    r"(?:安慰(?:我|一下)|(?:我|今天|最近|现在|刚才|有点|好|太|超级|真的)"
    r".{0,8}(?:难过|伤心|委屈|想哭|哭了|好累|累死|累坏)|"
    r"(?:好累|累死|累坏|很难过|很伤心|很委屈))"
)
_COMFORT_SIGNAL = re.compile(
    r"(?:失恋|失落|孤单|撑不住|不顺|被拒绝|被误解|压力|焦虑|崩溃|"
    r"赶不完|睡不着|想听你哄|陪我说两句)"
)
_COMFORT_NEGATED = re.compile(
    r"(?:没有|并没|没觉得|不觉得|并不)(?:很|太|怎么)?(?:难过|伤心|委屈|想哭|累)"
)


@dataclass(frozen=True, slots=True)
class StyleDraft:
    prompt: str
    response: str
    context_before: str
    context_after: str
    scene: str
    speech_act: str
    tone: list[str]
    relationship_level: str | None
    energy: float
    teasing_level: float
    answer_length: str
    response_mode: str
    source_type: str
    source_ref: str
    authenticity_score: float
    quality_score: float
    metadata: dict[str, object]
    review_status: str
    source_tier: str
    source_document_id: int
    source_start_line: int
    source_end_line: int
    source_start_char: int
    source_end_char: int
    source_speaker: str
    source_user_turn: str
    source_response_turn: str
    source_raw_text: str
    cleaning_operations: list[str]
    index_generation: str | None = None


def inventory_sources(
    *,
    db_path: str | Path,
    data_dir: str | Path,
) -> list[dict[str, object]]:
    source_dir = Path(data_dir)
    with db.connect(db_path) as conn:
        db.init_db(conn)
        documents = conn.execute(
            "SELECT id, filename, filepath FROM documents ORDER BY id"
        ).fetchall()
    manifest = []
    indexed_names: set[str] = set()
    for row in documents:
        filename = str(row["filename"])
        indexed_names.add(filename.casefold())
        source_type, reliability = _source_kind(filename)
        manifest.append(
            {
                "source_id": f"document:{int(row['id'])}",
                "source_path": str(source_dir / filename),
                "type": source_type,
                "date": _date_from_name(filename),
                "reliability": reliability,
                "indexed_document_id": int(row["id"]),
            }
        )
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file() or path.name.casefold() in indexed_names:
            continue
        if path.suffix.lower() not in {".docx", ".md", ".txt"}:
            continue
        source_type, reliability = _source_kind(path.name)
        manifest.append(
            {
                "source_id": f"file:{path.relative_to(source_dir).as_posix()}",
                "source_path": str(path),
                "type": source_type,
                "date": _date_from_name(path.name),
                "reliability": reliability,
                "indexed_document_id": None,
            }
        )
    return manifest


def extract_style_drafts(
    *,
    document_id: int,
    filename: str,
    content: str,
) -> list[StyleDraft]:
    raw_lines = content.splitlines(keepends=True)
    drafts: list[StyleDraft] = []
    offset = 0
    cleaned_lines = [_clean_utterance(line) for line in raw_lines]
    for index, raw_with_ending in enumerate(raw_lines):
        raw = raw_with_ending.rstrip("\r\n")
        pairs = _explicit_pairs(raw)
        if not pairs:
            line = _clean_utterance(raw)
            bullet = _BULLET_LINE.match(line)
            if bullet and index + 1 < len(raw_lines):
                next_raw = raw_lines[index + 1].rstrip("\r\n")
                if _HANSER_PREFIX.match(_clean_utterance(next_raw)):
                    pairs = [(
                        bullet.group("prompt"),
                        next_raw,
                        0,
                        len(raw) + len(raw_lines[index + 1]),
                        "explicit_bullet_named_next_utterance",
                    )]
        for raw_prompt, raw_response, start_char, end_char, method in pairs:
            prompt, prompt_ops = _clean_with_operations(raw_prompt, response=False)
            response, response_ops = _clean_with_operations(raw_response, response=True)
            if not _usable_pair(prompt, response):
                continue
            labels = label_style(prompt, response)
            authenticity = 0.97 if "named" in method else 0.94
            quality = _quality_score(response, authenticity)
            start_line = index + 1
            end_line = start_line + (1 if "next_utterance" in method else 0)
            source_raw = raw if end_line == start_line else raw + "\n" + raw_lines[index + 1].rstrip("\r\n")
            operations = list(dict.fromkeys(prompt_ops + response_ops))
            drafts.append(
                StyleDraft(
                    prompt=prompt,
                    response=response,
                    context_before=_previous_context(cleaned_lines, index),
                    context_after=_next_context(cleaned_lines, index, response),
                    source_type="real",
                    source_ref=(
                        f"document:{document_id}:lines:{start_line}-{end_line}:"
                        f"chars:{offset + start_char}-{offset + end_char}"
                    ),
                    authenticity_score=authenticity,
                    quality_score=quality,
                    metadata={
                        "filename": filename,
                        "document_id": document_id,
                        "attribution_method": method,
                        "fact_eligible": False,
                    },
                    review_status="pending",
                    source_tier="primary" if Path(filename).suffix.lower() == ".docx" else "secondary",
                    source_document_id=document_id,
                    source_start_line=start_line,
                    source_end_line=end_line,
                    source_start_char=offset + start_char,
                    source_end_char=offset + end_char,
                    source_speaker="hanser",
                    source_user_turn=prompt,
                    source_response_turn=response,
                    source_raw_text=source_raw,
                    cleaning_operations=operations,
                    **labels,
                )
            )
        offset += len(raw_with_ending)
    return drafts


def label_style(prompt: str, response: str) -> dict[str, object]:
    if _GREETING.fullmatch(prompt.strip()) or _GREETING_PREFIX.search(prompt.strip()):
        scene, speech_act, mode = "greeting", "greet", "casual"
    elif (_COMFORT.search(prompt) or _COMFORT_SIGNAL.search(prompt)) and not _COMFORT_NEGATED.search(prompt):
        scene, speech_act, mode = "comfort", "comfort", "emotional"
    elif any(value in prompt for value in ("可爱", "喜欢你", "好看", "厉害", "夸")):
        scene, speech_act, mode = "receiving_praise", "react", "playful"
    elif any(value in prompt for value in ("哈哈", "233", "笨", "傻", "离谱", "是吧")):
        scene, speech_act, mode = "teasing", "tease", "playful"
    elif any(value in prompt for value in ("为什么", "怎么", "什么", "吗", "哪")):
        scene, speech_act, mode = "question_answer", "answer", "casual"
    else:
        scene, speech_act, mode = "casual_chat", "react", "casual"

    tone = ["conversational"]
    if any(value in response for value in ("哈哈", "233", "笑死", "笨", "傻")):
        tone.append("playful")
    if any(value in response for value in ("没事", "不要怕", "辛苦", "抱抱")):
        tone.append("gentle")
    if any(value in response for value in ("啊", "呀", "啦", "诶", "哦", "嘛")):
        tone.append("lively")

    length = len(response)
    answer_length = "short" if length <= 32 else "medium" if length <= 120 else "long"
    playful = "playful" in tone
    return {
        "scene": scene,
        "speech_act": speech_act,
        "tone": tone,
        "relationship_level": "audience",
        "energy": 0.8 if playful else 0.6,
        "teasing_level": 0.65 if playful else 0.15,
        "answer_length": answer_length,
        "response_mode": mode,
    }


def rebuild_style_examples(
    *,
    db_path: str | Path,
    min_quality: float,
    index_generation: str | None = None,
) -> list[tuple[int, StyleDraft]]:
    with db.connect(db_path) as conn:
        db.init_db(conn)
        documents = conn.execute(
            "SELECT id, filename, content FROM documents ORDER BY id"
        ).fetchall()
        conn.execute("DELETE FROM style_examples")
        inserted: list[tuple[int, StyleDraft]] = []
        seen: set[tuple[str, str]] = set()
        for row in documents:
            for draft in extract_style_drafts(
                document_id=int(row["id"]),
                filename=str(row["filename"]),
                content=str(row["content"]),
            ):
                key = (
                    _SPACE.sub("", draft.prompt).casefold(),
                    _SPACE.sub("", draft.response).casefold(),
                )
                if key in seen or draft.quality_score < min_quality:
                    continue
                seen.add(key)
                cursor = conn.execute(
                    """
                    INSERT INTO style_examples
                        (prompt, response, context_before, context_after,
                         scene, speech_act, tone_json, relationship_level,
                         energy, teasing_level, answer_length, response_mode,
                         source_type, source_ref, authenticity_score,
                         quality_score, metadata_json, review_status, source_tier,
                         source_document_id, source_start_line, source_end_line,
                         source_start_char, source_end_char, source_speaker,
                         source_user_turn, source_response_turn, source_raw_text,
                         cleaning_operations_json, index_generation)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        draft.prompt,
                        draft.response,
                        draft.context_before,
                        draft.context_after,
                        draft.scene,
                        draft.speech_act,
                        json.dumps(draft.tone, ensure_ascii=False),
                        draft.relationship_level,
                        draft.energy,
                        draft.teasing_level,
                        draft.answer_length,
                        draft.response_mode,
                        draft.source_type,
                        draft.source_ref,
                        draft.authenticity_score,
                        draft.quality_score,
                        json.dumps(draft.metadata, ensure_ascii=False),
                        draft.review_status,
                        draft.source_tier,
                        draft.source_document_id,
                        draft.source_start_line,
                        draft.source_end_line,
                        draft.source_start_char,
                        draft.source_end_char,
                        draft.source_speaker,
                        draft.source_user_turn,
                        draft.source_response_turn,
                        draft.source_raw_text,
                        json.dumps(draft.cleaning_operations, ensure_ascii=False),
                        index_generation,
                    ),
                )
                inserted.append((int(cursor.lastrowid), draft))
        conn.commit()
    return inserted


async def rebuild_style_embeddings(
    *,
    db_path: str | Path,
    rows: list[tuple[int, StyleDraft]],
    embedder: Embedder,
    vector_store: VectorStore,
    batch_size: int,
) -> int:
    if not rows:
        return 0
    source_revision = hashlib.sha256(
        "\n".join(f"{item_id}:{style_search_text(draft)}" for item_id, draft in rows).encode("utf-8")
    ).hexdigest()
    generation: str | None = None
    try:
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            vectors = await embedder.embed_documents(
                [style_search_text(draft) for _, draft in batch]
            )
            if generation is None:
                generation = vector_store.begin_generation(
                    STYLE_COLLECTION, embedder.model_name, len(vectors[0]),
                    source_revision=source_revision,
                    config_hash=hashlib.sha256(f"batch_size={batch_size}".encode()).hexdigest(),
                )
            vector_store.stage_upsert(
                STYLE_COLLECTION, generation, embedder.model_name,
                [(str(item_id), vector) for (item_id, _), vector in zip(batch, vectors, strict=True)],
            )
        assert generation is not None
        vector_store.publish_generation(STYLE_COLLECTION, generation, expected_count=len(rows))
    except Exception:
        if generation is not None:
            vector_store.fail_generation(STYLE_COLLECTION, generation)
        raise
    with db.connect(db_path) as conn:
        conn.execute(
            "UPDATE style_examples SET embedding_ref = 'style_examples:' || id"
        )
        conn.commit()
    return len(rows)


def style_search_text(draft: StyleDraft) -> str:
    return (
        f"scene={draft.scene} mode={draft.response_mode} "
        f"speech_act={draft.speech_act}\nUser: {draft.prompt}"
    )


def persona_statistics(rows: list[tuple[int, StyleDraft]]) -> dict[str, object]:
    responses = [draft.response for _, draft in rows]
    lengths = [len(value) for value in responses]
    joined = "\n".join(responses)
    normalized = re.sub(r"\s+", "", joined)
    ngrams = Counter(
        normalized[index : index + 3]
        for index in range(max(0, len(normalized) - 2))
        if not re.search(r"[，。！？；：,.!?;:]", normalized[index : index + 3])
    )
    punctuation = {
        symbol: joined.count(symbol)
        for symbol in "，。！？；：,.!?;:～~"
    }
    endings = Counter(
        response[-2:]
        for response in responses
        if len(response) >= 2
    )
    return {
        "sample_count": len(responses),
        "response_length": {
            "mean": round(statistics.mean(lengths), 2) if lengths else 0,
            "median": statistics.median(lengths) if lengths else 0,
            "p90": _percentile(lengths, 0.9),
        },
        "punctuation_counts": punctuation,
        "space_count": joined.count(" "),
        "newline_count": joined.count("\n"),
        "question_ratio": round(
            sum("?" in value or "？" in value for value in responses)
            / max(1, len(responses)),
            4,
        ),
        "parenthetical_ratio": round(
            sum("（" in value or "(" in value for value in responses)
            / max(1, len(responses)),
            4,
        ),
        "laughter_counts": {
            value: joined.count(value)
            for value in ("哈哈", "233", "www", "笑死")
        },
        "self_reference_counts": {
            value: joined.count(value)
            for value in ("我", "憨色", "憨憨")
        },
        "audience_address_counts": {
            value: joined.count(value)
            for value in ("你", "你们", "毛怪")
        },
        "top_trigrams": ngrams.most_common(30),
        "top_endings": endings.most_common(20),
    }


def style_row_to_model(row) -> StyleExample:
    metadata = json.loads(str(row["metadata_json"] or "{}"))
    return StyleExample(
        id=f"style:{int(row['id'])}",
        user_context=str(row["prompt"]),
        character_response=str(row["response"]),
        context_before=str(row["context_before"]),
        context_after=str(row["context_after"]),
        scene=str(row["scene"]),
        speech_act=str(row["speech_act"]),
        tone=json.loads(str(row["tone_json"])),
        relationship_level=(
            str(row["relationship_level"])
            if row["relationship_level"] is not None
            else None
        ),
        energy=float(row["energy"]) if row["energy"] is not None else None,
        teasing_level=(
            float(row["teasing_level"])
            if row["teasing_level"] is not None
            else None
        ),
        answer_length=str(row["answer_length"]),
        response_mode=str(row["response_mode"]),
        source_type=str(row["source_type"]),
        source_ref=str(row["source_ref"]),
        authenticity_score=float(row["authenticity_score"]),
        quality_score=float(row["quality_score"]),
        review_status=str(row["review_status"]),
        source_tier=str(row["source_tier"]),
        source_document_id=(
            int(row["source_document_id"])
            if row["source_document_id"] is not None else None
        ),
        source_start_line=(
            int(row["source_start_line"])
            if row["source_start_line"] is not None else None
        ),
        source_end_line=(
            int(row["source_end_line"])
            if row["source_end_line"] is not None else None
        ),
        source_start_char=(
            int(row["source_start_char"])
            if row["source_start_char"] is not None else None
        ),
        source_end_char=(
            int(row["source_end_char"])
            if row["source_end_char"] is not None else None
        ),
        source_speaker=(str(row["source_speaker"]) if row["source_speaker"] else None),
        source_user_turn=(str(row["source_user_turn"]) if row["source_user_turn"] else None),
        source_response_turn=(str(row["source_response_turn"]) if row["source_response_turn"] else None),
        cleaning_operations=json.loads(str(row["cleaning_operations_json"])),
        reviewer_id=(str(row["reviewer_id"]) if row["reviewer_id"] else None),
        review_notes=(str(row["review_notes"]) if row["review_notes"] else None),
        index_generation=(str(row["index_generation"]) if row["index_generation"] else None),
        provenance_kind=str(metadata.get("provenance_kind", "legacy_unknown")),
        payload_class=str(metadata.get("payload_class", "unreviewed")),
        schema_review_status=str(metadata.get("schema_review_status", "pending")),
        behavior_tags=[str(value) for value in metadata.get("behavior_tags", [])],
        expression_tags=[str(value) for value in metadata.get("expression_tags", [])],
        audience=str(metadata.get("audience", "unknown")),
        group_id=(str(metadata["group_id"]) if metadata.get("group_id") else None),
        speaker_status=str(metadata.get("speaker_status", "unverified")),
        runtime_scope=[str(value) for value in metadata.get("runtime_scope", [])],
        hidden_eval=bool(metadata.get("hidden_eval", False)),
    )


def _source_kind(filename: str) -> tuple[str, float]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".docx":
        return "transcript", 0.9
    if suffix == ".md":
        return "wiki", 1.0
    return "notes", 0.75


def _date_from_name(filename: str) -> str | None:
    match = _DATE.search(filename)
    if not match:
        return None
    return (
        f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-"
        f"{int(match.group(3)):02d}"
    )


def _clean_utterance(value: str) -> str:
    value = _HTML_COMMENT.sub(" ", value)
    value = _TIMESTAMP.sub(" ", value)
    return _SPACE.sub(" ", value).strip(" \t;；")


def _clean_with_operations(value: str, *, response: bool) -> tuple[str, list[str]]:
    operations: list[str] = []
    cleaned = value
    if _HTML_COMMENT.search(cleaned):
        operations.append("remove_html_comment")
        cleaned = _HTML_COMMENT.sub(" ", cleaned)
    if _TIMESTAMP.search(cleaned):
        operations.append("remove_timestamp")
        cleaned = _TIMESTAMP.sub(" ", cleaned)
    cleaned = _SPACE.sub(" ", cleaned).strip(" \t;；")
    if response and _HANSER_PREFIX.match(cleaned):
        operations.append("remove_hanser_prefix")
        cleaned = _HANSER_PREFIX.sub("", cleaned)
    compacted = _SPACE.sub(" ", cleaned).strip()
    if compacted != value.strip() and "normalize_whitespace" not in operations:
        operations.append("normalize_whitespace")
    return compacted, operations


def _explicit_pairs(raw: str) -> list[tuple[str, str, int, int, str]]:
    """Return only explicitly attributable user/Hanser pairs from one raw line."""
    pairs: list[tuple[str, str, int, int, str]] = []
    consumed: list[tuple[int, int]] = []

    for match in _PAREN_BULLET.finditer(raw):
        response_start = match.end()
        prefix = _HANSER_PREFIX.match(raw[response_start:].lstrip())
        named = prefix is not None
        if named:
            leading = len(raw[response_start:]) - len(raw[response_start:].lstrip())
            response_start += leading + prefix.end()
        response_end = _next_boundary(raw, response_start)
        response = raw[response_start:response_end]
        if response.strip() and not _OTHER_SPEAKER.search(response):
            pairs.append((
                match.group("prompt"), response, match.start(), response_end,
                "explicit_parenthetical_named_inline" if named else "explicit_parenthetical_inline",
            ))
            consumed.append((match.start(), response_end))

    plain_matches = list(_PLAIN_BULLET.finditer(raw))
    for marker in plain_matches:
        if any(start <= marker.start() and marker.end() <= end for start, end in consumed):
            continue
        prompt_start = marker.end()
        hanser = _HANSER_MARKER.search(raw[prompt_start:])
        if hanser is None or hanser.start() > 160:
            continue
        prompt_end = prompt_start + hanser.start()
        response_start = prompt_start + hanser.end()
        response_end = _next_boundary(raw, response_start)
        response = raw[response_start:response_end]
        if response.strip() and not _OTHER_SPEAKER.search(response):
            pairs.append((
                raw[prompt_start:prompt_end], response, marker.start(), response_end,
                "explicit_plain_bullet_named_inline",
            ))

    for match in _QA_PROMPT.finditer(raw):
        response_start = match.end()
        response_end = _next_boundary(raw, response_start)
        response = raw[response_start:response_end]
        if response.strip() and not _OTHER_SPEAKER.search(response):
            pairs.append((
                match.group("prompt"), response, match.start(), response_end,
                "explicit_qa_named_inline",
            ))

    unique: dict[tuple[str, str], tuple[str, str, int, int, str]] = {}
    for pair in pairs:
        unique[(_SPACE.sub("", pair[0]), _SPACE.sub("", pair[1]))] = pair
    return sorted(unique.values(), key=lambda item: item[2])


def _next_boundary(raw: str, start: int) -> int:
    candidates = [len(raw)]
    marker = _ANY_SPEAKER.search(raw, start)
    if marker:
        candidates.append(marker.start())
    timestamp = _TIMESTAMP.search(raw, start)
    if timestamp:
        candidates.append(timestamp.start())
    return min(candidates)


def _clean_response(value: str) -> str:
    if "应援弹幕" in value:
        return ""
    cleaned, _ = _clean_with_operations(value, response=True)
    boundary = _ANY_SPEAKER.search(cleaned, 1)
    if boundary:
        cleaned = cleaned[:boundary.start()]
    return cleaned.strip()


def _next_response(lines: list[str], index: int) -> str:
    for line in lines[index + 1 : index + 4]:
        response = _clean_response(line)
        if response and not _BULLET_LINE.match(response):
            return response
    return ""


def _previous_context(lines: list[str], index: int) -> str:
    for line in reversed(lines[max(0, index - 3) : index]):
        if line:
            return line[:240]
    return ""


def _next_context(lines: list[str], index: int, response: str) -> str:
    for line in lines[index + 1 : index + 5]:
        if line and _clean_response(line) != response:
            return line[:240]
    return ""


def _usable_pair(prompt: str, response: str) -> bool:
    return bool(
        1 <= len(prompt) <= 120
        and 2 <= len(response) <= 320
        and not _OTHER_SPEAKER.search(response)
        and not _ANY_SPEAKER.search(response)
        and not response.startswith(("Title：", "Title:", "期数", "期号"))
    )


def _quality_score(response: str, authenticity: float) -> float:
    score = authenticity
    if len(response) < 6:
        score -= 0.12
    if len(response) > 220:
        score -= 0.08
    if "//" in response or "ing" in response:
        score -= 0.08
    return round(max(0.0, min(1.0, score)), 3)


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]
