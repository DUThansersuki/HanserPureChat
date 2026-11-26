from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from hanser_agent.persona.data_pipeline import STYLE_COLLECTION, style_row_to_model
from hanser_agent.persona.style_eligibility import style_exclusion_reason


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: list[object]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n" for value in values),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _recoverable(raw_text: str, value: str, operations: list[str]) -> bool:
    if value in raw_text:
        return True
    if "normalize_whitespace" in operations:
        return " ".join(value.split()) in " ".join(raw_text.split())
    return False


def build(spec_path: Path, output_dir: Path) -> None:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    project_root = Path(__file__).resolve().parents[3]
    source_db = (project_root / spec["source_database"]).resolve()
    candidate_db = output_dir / "candidate.db"
    if candidate_db.exists():
        raise FileExistsError(f"candidate already exists: {candidate_db}")
    output_dir.mkdir(parents=True, exist_ok=True)

    source = sqlite3.connect(f"file:{source_db.as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    candidate = sqlite3.connect(candidate_db)
    source.backup(candidate)
    candidate.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc).isoformat()
    generation = str(spec["generation"])
    reviewer_id = str(spec["reviewer_id"])
    columns = [
        str(row[1])
        for row in candidate.execute("PRAGMA table_info(style_examples)")
        if str(row[1]) != "id"
    ]
    placeholders = ",".join("?" for _ in columns)
    insert_sql = (
        f"INSERT INTO style_examples ({','.join(columns)}) VALUES ({placeholders})"
    )

    reviewed: list[dict[str, object]] = []
    reaction_units: list[dict[str, object]] = []
    annotations: list[dict[str, object]] = []
    decisions: list[dict[str, object]] = []
    source_documents: dict[int, dict[str, object]] = {}
    behavior_counts: Counter[str] = Counter()
    transfer_counts: Counter[str] = Counter()

    with candidate:
        for item_spec in spec["items"]:
            source_id = int(item_spec["source_row_id"])
            row = candidate.execute(
                "SELECT * FROM style_examples WHERE id = ?", (source_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"missing source Style row: {source_id}")
            raw_text = str(row["source_raw_text"] or "")
            source_user = str(row["source_user_turn"] or "")
            source_response = str(row["source_response_turn"] or "")
            cleaning_operations = json.loads(str(row["cleaning_operations_json"] or "[]"))
            if not source_user or not _recoverable(raw_text, source_user, cleaning_operations):
                raise ValueError(f"trigger is not recoverable from source row {source_id}")
            if not source_response or not _recoverable(raw_text, source_response, cleaning_operations):
                raise ValueError(f"reaction is not recoverable from source row {source_id}")

            document_id = int(row["source_document_id"])
            unit_id = (
                f"reaction:document:{document_id}:chars:"
                f"{int(row['source_start_char'])}-{int(row['source_end_char'])}"
            )
            episode_id = (
                f"episode:document:{document_id}:lines:"
                f"{int(row['source_start_line'])}-{int(row['source_end_line'])}"
            )
            metadata = json.loads(str(row["metadata_json"] or "{}"))
            metadata.pop("required_context_tags", None)
            metadata.update({
                "style_record_version": int(spec["style_record_version"]),
                "unit_id": unit_id,
                "source_episode_id": episode_id,
                "trigger_basis": "explicit",
                "trigger_evidence_refs": [f"{row['source_ref']}:external-trigger"],
                "retrieval_description": str(item_spec["retrieval_description"]),
                "transfer_scope": str(item_spec["transfer_scope"]),
                "applicability": {
                    "requires_all": item_spec.get("requires_all", []),
                    "forbids_any": item_spec.get("forbids_any", []),
                },
            })
            values = {column: row[column] for column in columns}
            values.update({
                "metadata_json": json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
                "review_status": "approved",
                "reviewer_id": reviewer_id,
                "review_notes": str(spec["review_basis"]),
                "reviewed_at": now,
                "index_generation": generation,
            })
            cursor = candidate.execute(insert_sql, tuple(values[column] for column in columns))
            new_id = int(cursor.lastrowid)
            projected_row = candidate.execute(
                "SELECT * FROM style_examples WHERE id = ?", (new_id,)
            ).fetchone()
            model = style_row_to_model(projected_row)
            reason = style_exclusion_reason(
                model, active_generation=generation, min_quality=0.68
            )
            if reason is not None:
                raise ValueError(f"new row {new_id} failed runtime contract: {reason}")

            model_data = model.model_dump(mode="json")
            reviewed.append(model_data)
            behavior_counts.update(model.behavior_tags)
            transfer_counts[model.transfer_scope or "missing"] += 1
            reaction_units.append({
                "unit_id": unit_id,
                "source_episode_id": episode_id,
                "source_row_id": source_id,
                "candidate_row_id": new_id,
                "source_ref": row["source_ref"],
                "source_document_id": document_id,
                "source_span": {
                    "unit": "unicode_codepoint",
                    "start": int(row["source_start_char"]),
                    "end": int(row["source_end_char"]),
                },
                "source_raw_text": raw_text,
                "source_user_turn": source_user,
                "source_response_turn": source_response,
            })
            annotations.append({
                "unit_id": unit_id,
                "trigger_basis": "explicit",
                "trigger_evidence_refs": metadata["trigger_evidence_refs"],
                "retrieval_description": metadata["retrieval_description"],
                "payload_class": metadata["payload_class"],
                "behavior_tags": metadata["behavior_tags"],
                "transfer_scope": metadata["transfer_scope"],
                "applicability": metadata["applicability"],
            })
            decisions.append({
                "unit_id": unit_id,
                "decision": "approved_for_isolated_v3_candidate",
                "reviewer_id": reviewer_id,
                "reviewed_at": now,
                "inherited_review": {
                    "reviewer_id": row["reviewer_id"],
                    "review_notes": row["review_notes"],
                },
                "limitations": ["agent_review_not_human_gold", "transcript_not_audio_verified"],
            })
            if document_id not in source_documents:
                document = candidate.execute(
                    "SELECT id,filename,filepath,content,mtime,size,indexed_at FROM documents WHERE id = ?",
                    (document_id,),
                ).fetchone()
                source_documents[document_id] = {
                    "document_id": document_id,
                    "filename": document["filename"],
                    "filepath": document["filepath"],
                    "mtime": document["mtime"],
                    "size": document["size"],
                    "indexed_at": document["indexed_at"],
                    "content_sha256": hashlib.sha256(
                        str(document["content"]).encode("utf-8")
                    ).hexdigest(),
                }

        for rejected in spec.get("rejected_source_rows", []):
            decisions.append({
                "source_row_id": int(rejected["source_row_id"]),
                "decision": "rejected_on_v3_re_review",
                "reason": str(rejected["reason"]),
                "reviewer_id": reviewer_id,
                "reviewed_at": now,
            })
        candidate.execute(
            """INSERT INTO active_index_generations(collection,generation,revision,updated_at)
               VALUES(?,?,1,?)
               ON CONFLICT(collection) DO UPDATE SET generation=excluded.generation,
                   revision=excluded.revision,updated_at=excluded.updated_at""",
            (STYLE_COLLECTION, generation, now),
        )

    _write_json(output_dir / "source_manifest.json", {
        "source_database": str(source_db),
        "source_database_sha256": _sha256(source_db),
        "documents": list(source_documents.values()),
    })
    _write_jsonl(output_dir / "reaction_units.jsonl", reaction_units)
    _write_jsonl(output_dir / "annotations.jsonl", annotations)
    _write_jsonl(output_dir / "review_decisions.jsonl", decisions)
    _write_jsonl(output_dir / "style_examples.reviewed.jsonl", reviewed)
    _write_json(output_dir / "coverage.json", {
        "approved_reactions": len(reviewed),
        "rejected_on_v3_re_review": len(spec.get("rejected_source_rows", [])),
        "independent_source_groups": len({item["group_id"] for item in reviewed}),
        "behavior_counts": dict(sorted(behavior_counts.items())),
        "transfer_scope_counts": dict(sorted(transfer_counts.items())),
    })
    _write_json(output_dir / "build_summary.json", {
        "generation": generation,
        "candidate_database": str(candidate_db),
        "candidate_database_sha256": _sha256(candidate_db),
        "active_pointer_changed_in_candidate_only": True,
        "production_database_modified": False,
        "approved_rows": len(reviewed),
        "built_at": now,
    })
    source.close()
    candidate.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    build(args.spec.resolve(), args.output_dir.resolve())


if __name__ == "__main__":
    main()
