from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_GENERATION = "desktop-style-v3-pilot-repair-20260914-002"


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def sqlite_backup(source_path: Path, target_path: Path) -> None:
    source = sqlite3.connect(f"file:{source_path.resolve().as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(target_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def finalize(args: argparse.Namespace) -> None:
    output, parent = args.output.resolve(), args.parent.resolve()
    generation = str(args.generation)
    adjudications = read_jsonl(output / "adjudication_v2.jsonl")
    parent_units = {row["unit_id"]: row for row in read_jsonl(parent / "approved_units.jsonl")}
    approved: list[dict[str, object]] = []
    for adjudication in adjudications:
        if adjudication["decision"] != "approved" or not adjudication["candidate_eligible"]:
            continue
        parent_id = str(adjudication["parent_unit_id"])
        record = {**parent_units[parent_id], **dict(adjudication.get("overrides") or {})}
        record.update({
            "unit_id": str(adjudication["unit_id"]), "parent_unit_id": parent_id,
            "source_candidate_id": adjudication.get("source_candidate_id"), "final_decision": "approved",
            "reviewer_id": "codex_agent_pilot_repair_adjudication:2026-09-15",
            "review_basis": adjudication["review_basis"], "repair_rationale": adjudication["rationale"],
        })
        record.pop("database_id", None)
        approved.append(record)

    candidate_path = output / "candidate_pilot.db"
    if candidate_path.exists():
        raise FileExistsError(f"candidate already exists: {candidate_path}")
    sqlite_backup(args.database, candidate_path)
    con = sqlite3.connect(candidate_path)
    con.row_factory = sqlite3.Row
    next_id = int(con.execute("SELECT COALESCE(MAX(id),0)+1 FROM style_examples").fetchone()[0])
    now = datetime.now(timezone.utc).isoformat()
    with con:
        active = con.execute("SELECT generation,revision FROM active_index_generations WHERE collection='style_examples'").fetchone()
        for offset, record in enumerate(approved):
            row_id = next_id + offset
            response = str(record["character_response"])
            metadata = {
                "filename": record["filename"], "document_id": record["document_id"],
                "attribution_method": "pilot_repair_adjudicated_verbatim", "fact_eligible": False,
                "audience": "mixed", "behavior_tags": record["behavior_tags"], "expression_tags": [],
                "group_id": f"pilot.repair:{record['window_id']}", "hidden_eval": False,
                "payload_class": record["payload_class"], "provenance_kind": "verbatim",
                "runtime_scope": ["style_runtime"], "schema_review_status": "approved",
                "speaker_status": record["speaker_status"], "transfer_scope": record["transfer_scope"],
                "style_record_version": 3, "unit_id": record["unit_id"],
                "parent_unit_id": record["parent_unit_id"], "source_candidate_id": record.get("source_candidate_id"),
                "source_episode_id": f"episode:document:{record['document_id']}:window:{record['window_id']}",
                "trigger_basis": record["trigger_basis"],
                "trigger_evidence_refs": [f"document:{record['document_id']}:window:{record['window_id']}:verbatim-trigger"],
                "retrieval_description": record["retrieval_description"],
                "applicability": {"requires_all": record["requires_all"], "forbids_any": record["forbids_any"]},
                "runtime_payload_kind": record["runtime_payload_kind"],
                "runtime_reaction_fragment": record.get("runtime_reaction_fragment"),
                "runtime_payload_reviewed": True,
                "pilot_review": {"version": 2, "candidate_eligible": True},
            }
            playful = bool(set(record["behavior_tags"]).intersection({"playful_reframe", "light_contextual_tease"}))
            con.execute(
                """INSERT INTO style_examples (
                    id,prompt,response,context_before,context_after,scene,speech_act,tone_json,
                    relationship_level,energy,teasing_level,answer_length,response_mode,source_type,
                    source_ref,authenticity_score,quality_score,metadata_json,embedding_ref,review_status,
                    source_tier,source_document_id,source_start_line,source_end_line,source_start_char,
                    source_end_char,source_speaker,source_user_turn,source_response_turn,source_raw_text,
                    cleaning_operations_json,reviewer_id,review_notes,reviewed_at,index_generation
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (row_id, record["source_user_turn"], response, "", "", "casual_chat", "react",
                 json.dumps(["conversational"], ensure_ascii=False), "audience", 0.65, 0.55 if playful else 0.15,
                 "short" if len(response) <= 32 else "medium", "playful" if playful else "casual", "real",
                 f"document:{record['document_id']}:chars:{record['source_start_char']}-{record['source_end_char']}",
                 0.97, 0.92, json.dumps(metadata, ensure_ascii=False, separators=(",", ":")), None, "approved",
                 "primary", record["document_id"], record["source_start_line"], record["source_end_line"],
                 record["source_start_char"], record["source_end_char"], "hanser", record["source_user_turn"],
                 response, record["source_span"], "[]", record["reviewer_id"], record["review_basis"], now, generation),
            )
            record["database_id"] = str(row_id)
        con.execute("UPDATE active_index_generations SET generation=?,revision=revision+1 WHERE collection='style_examples'", (generation,))
    con.close()
    write_jsonl(output / "approved_units.jsonl", approved)
    manifest = {
        "generation": generation, "candidate_database": str(candidate_path),
        "source_database": str(args.database.resolve()), "parent_pilot": parent.name,
        "adjudication_counts": dict(Counter(str(row["decision"]) for row in adjudications)),
        "candidate_units": len(approved), "candidate_unit_ids": [row["unit_id"] for row in approved],
        "candidate_active_pointer_before_backup": dict(active) if active else None,
        "production_pointer_modified": False,
    }
    (output / "candidate_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--database", type=Path, default=root / "pure_chat_desktop" / "resources" / "database" / "documents.dev.db")
    parser.add_argument("--generation", default=DEFAULT_GENERATION)
    finalize(parser.parse_args())


if __name__ == "__main__":
    main()
