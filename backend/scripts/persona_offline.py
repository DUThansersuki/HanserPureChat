"""Strictly offline Persona v2 inventory, extraction, validation, coverage and preview.

This module intentionally imports no gateway, embedder, reranker, responder or app
builder. SQLite connections use mode=ro and every command requires an explicit
output directory whose target files must not already exist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
MODEL_IMPORT_MARKERS = (
    "model_gateway",
    "retrieval.embedding",
    "retrieval.reranker",
    "build_embedder",
    "build_reranker",
    "HanserResponder",
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_text(payload: str) -> str:
    return sha256_bytes(payload.encode("utf-8"))


def json_dump(path: Path, value: object) -> None:
    _refuse_existing(path)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def jsonl_dump(path: Path, rows: list[dict[str, object]]) -> None:
    _refuse_existing(path)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def prepare_output(value: str) -> Path:
    output = Path(value).resolve()
    output.mkdir(parents=True, exist_ok=True)
    return output


def _refuse_existing(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {path}")


def ro_connect(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def command_inventory(args: argparse.Namespace) -> None:
    db_path = Path(args.db).resolve()
    data_dir = Path(args.data_dir).resolve()
    output = prepare_output(args.output_dir)
    with ro_connect(db_path) as connection:
        documents = connection.execute(
            "SELECT id,filename,filepath,content FROM documents ORDER BY id"
        ).fetchall()
        tables = [
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        style_counts = [
            dict(row)
            for row in connection.execute(
                "SELECT review_status,source_type,source_tier,count(1) n "
                "FROM style_examples GROUP BY review_status,source_type,source_tier ORDER BY 1,2,3"
            )
        ]
        active = [
            dict(row)
            for row in connection.execute(
                "SELECT collection,generation,revision,updated_at "
                "FROM active_index_generations ORDER BY collection"
            )
        ] if "active_index_generations" in tables else []

    rows: list[dict[str, object]] = []
    seen: set[Path] = set()
    for document in documents:
        source_path = data_dir / str(document["filename"])
        seen.add(source_path.resolve())
        raw_hash = sha256_bytes(source_path.read_bytes()) if source_path.is_file() else None
        text = str(document["content"])
        rows.append(
            {
                "source_id": f"document:{int(document['id'])}",
                "relative_path": relative_path(source_path),
                "source_sha256": raw_hash,
                "text_sha256": sha256_text(text),
                "format": source_path.suffix.lower().lstrip(".") or "unknown",
                "date": _date_from_name(source_path.name),
                "date_basis": "filename" if _date_from_name(source_path.name) else "unknown",
                "episode_group": f"document:{int(document['id'])}",
                "extractor_version": "documents_db_snapshot+file_sha256_v1",
                "transcript_status": "db_extracted",
                "speaker_verification_status": "unverified",
                "indexed_document_id": int(document["id"]),
                "missing_source_file": not source_path.is_file(),
            }
        )
    for path in sorted(data_dir.rglob("*")):
        if not path.is_file() or path.resolve() in seen or path.suffix.lower() not in {".docx", ".md", ".txt"}:
            continue
        rows.append(
            {
                "source_id": f"file:{path.relative_to(data_dir).as_posix()}",
                "relative_path": relative_path(path),
                "source_sha256": sha256_bytes(path.read_bytes()),
                "text_sha256": None,
                "format": path.suffix.lower().lstrip("."),
                "date": _date_from_name(path.name),
                "date_basis": "filename" if _date_from_name(path.name) else "unknown",
                "episode_group": f"file:{path.relative_to(data_dir).as_posix()}",
                "extractor_version": "file_inventory_v1",
                "transcript_status": "not_extracted",
                "speaker_verification_status": "unverified",
                "indexed_document_id": None,
                "missing_source_file": False,
            }
        )
    jsonl_dump(output / "sources.jsonl", rows)
    json_dump(
        output / "inventory_summary.json",
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "mode": "OFFLINE_READ_ONLY",
            "database": relative_path(db_path),
            "database_sha256": sha256_bytes(db_path.read_bytes()),
            "documents": len(documents),
            "sources": len(rows),
            "missing_source_files": sum(bool(row["missing_source_file"]) for row in rows),
            "style_counts": style_counts,
            "active_index_generations": active,
            "model_operations": "NOT_EXECUTED",
        },
    )


def command_extract(args: argparse.Namespace) -> None:
    db_path = Path(args.db).resolve()
    seeds_path = Path(args.seeds).resolve()
    output = prepare_output(args.output_dir)
    raw = yaml.safe_load(seeds_path.read_text(encoding="utf-8")) or {}
    if set(raw) != {"schema_version", "seeds"} or raw.get("schema_version") != 1:
        raise ValueError("invalid episode seed schema")
    seeds = raw.get("seeds")
    if not isinstance(seeds, list):
        raise ValueError("episode seeds must be a list")
    rows: list[dict[str, object]] = []
    with ro_connect(db_path) as connection:
        for seed in seeds:
            if not isinstance(seed, dict):
                raise ValueError("episode seed must be an object")
            document = connection.execute(
                "SELECT id,filename,content FROM documents WHERE id=?",
                (int(seed["document_id"]),),
            ).fetchone()
            if document is None:
                raise ValueError(f"missing document {seed['document_id']}")
            content = str(document["content"])
            kept_lines = content.splitlines(keepends=True)
            start_line = int(seed["line_start"])
            end_line = int(seed["line_end"])
            if start_line < 1 or end_line < start_line or end_line > len(kept_lines):
                raise ValueError(f"invalid line range for {seed['episode_id']}")
            start = sum(len(value) for value in kept_lines[: start_line - 1])
            end = sum(len(value) for value in kept_lines[:end_line])
            excerpt = content[start:end].rstrip("\r\n")
            rows.append(
                {
                    "episode_id": str(seed["episode_id"]),
                    "schema_version": 1,
                    "source_id": f"document:{int(document['id'])}",
                    "source_filename": str(document["filename"]),
                    "source_text_sha256": sha256_text(content),
                    "source_span": {"unit": "unicode_codepoint", "start": start, "end": end},
                    "source_lines": {"start": start_line, "end": end_line},
                    "newline_normalization": "database_text_as_stored",
                    "continuous_context": excerpt,
                    "user_context": None,
                    "speaker_status": seed["speaker_status"],
                    "audience": seed["audience"],
                    "group_id": seed["group_id"],
                    "sampling_method": seed["sampling_method"],
                    "annotation_basis": seed["annotation_basis"],
                    "candidate_traits": seed.get("candidate_traits", []),
                    "review": {
                        "status": seed["review_status"],
                        "speaker_audio_verified": False,
                        "semantic_labels_verified": False,
                    },
                    "notes": seed.get("notes", ""),
                }
            )
    ids = [str(row["episode_id"]) for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate episode_id")
    jsonl_dump(output / "episodes.jsonl", rows)
    json_dump(
        output / "extract_summary.json",
        {
            "mode": "OFFLINE_READ_ONLY",
            "episodes": len(rows),
            "groups": len({str(row["group_id"]) for row in rows}),
            "all_pending": all(row["review"]["status"] == "pending" for row in rows),  # type: ignore[index]
            "model_operations": "NOT_EXECUTED",
        },
    )
    if args.legacy_candidate_db:
        _extract_legacy_style_preview(Path(args.legacy_candidate_db), output)


def command_validate(args: argparse.Namespace) -> None:
    package_dir = Path(args.package_dir).resolve()
    data_dir = Path(args.data_dir).resolve()
    eval_dir = Path(args.eval_dir).resolve()
    output = prepare_output(args.output_dir)
    sys.path.insert(0, str(BACKEND))
    from hanser_agent.persona import PersonaCompiler  # local import is intentionally model-free

    compiler = PersonaCompiler(package_dir)
    snapshot = compiler.compile("casual")
    required_data = [
        "sources.jsonl",
        "episodes.jsonl",
        "trait_ledger.yaml",
        "style_examples.reviewed.jsonl",
        "review_decisions.jsonl",
        "coverage_report.json",
    ]
    required_eval = [
        "cases.jsonl",
        "sequences.jsonl",
        "rubric.md",
        "contrast_pairs.jsonl",
        "retrieval_fixtures.jsonl",
        "split_manifest.json",
    ]
    missing = [str(data_dir / name) for name in required_data if not (data_dir / name).is_file()]
    missing.extend(str(eval_dir / name) for name in required_eval if not (eval_dir / name).is_file())
    jsonl_counts = {
        name: len(_read_jsonl(data_dir / name))
        for name in ("sources.jsonl", "episodes.jsonl", "style_examples.reviewed.jsonl", "review_decisions.jsonl")
        if (data_dir / name).is_file()
    }
    jsonl_counts.update(
        {
            f"eval/{name}": len(_read_jsonl(eval_dir / name))
            for name in (
                "cases.jsonl",
                "sequences.jsonl",
                "contrast_pairs.jsonl",
                "retrieval_fixtures.jsonl",
            )
            if (eval_dir / name).is_file()
        }
    )
    script_text = Path(__file__).read_text(encoding="utf-8")
    forbidden_imports = [marker for marker in MODEL_IMPORT_MARKERS if f"import {marker}" in script_text or f"from {marker}" in script_text]
    report = {
        "mode": "OFFLINE_VALIDATION",
        "package_id": snapshot.package_id,
        "package_schema_version": snapshot.schema_version,
        "compiler_version": snapshot.compiler_version,
        "persona_source_sha256": snapshot.source_sha256,
        "persona_render_sha256": snapshot.render_sha256,
        "settings_sha256": snapshot.settings_sha256,
        "missing_required_assets": missing,
        "jsonl_counts": jsonl_counts,
        "offline_entry_forbidden_imports": forbidden_imports,
        "model_operations": "NOT_EXECUTED",
        "passed": not missing and not forbidden_imports,
    }
    json_dump(output / "validation_report.json", report)
    if not report["passed"]:
        raise SystemExit(2)


def command_coverage(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir).resolve()
    eval_dir = Path(args.eval_dir).resolve()
    output = prepare_output(args.output_dir)
    episodes = _read_jsonl(data_dir / "episodes.jsonl")
    styles = _read_jsonl(data_dir / "style_examples.reviewed.jsonl")
    cases = _read_jsonl(eval_dir / "cases.jsonl")
    retrieval_fixtures = _read_jsonl(eval_dir / "retrieval_fixtures.jsonl")
    traits = yaml.safe_load((data_dir / "trait_ledger.yaml").read_text(encoding="utf-8")) or {}
    report = {
        "mode": "OFFLINE_AGGREGATION",
        "episodes": len(episodes),
        "episode_groups": len({str(item.get("group_id")) for item in episodes}),
        "episode_review_status": dict(Counter(str(item.get("review", {}).get("status")) for item in episodes)),
        "candidate_trait_mentions": dict(
            Counter(str(tag) for item in episodes for tag in item.get("candidate_traits", []))
        ),
        "traits": len(traits.get("traits", [])),
        "trait_basis": dict(Counter(str(item.get("basis")) for item in traits.get("traits", []))),
        "style_rows": len(styles),
        "style_schema_review": dict(Counter(str(item.get("schema_review_status")) for item in styles)),
        "eval_cases": len(cases),
        "retrieval_fixtures": len(retrieval_fixtures),
        "eval_scenes": dict(Counter(str(item.get("scene")) for item in cases)),
        "known_gaps": [
            "all extracted episodes remain transcript-only and pending audio/speaker verification",
            "legacy approved rows have not been upgraded to persona-v2 payload/behavior approval",
            "72-case target and multi-turn target are not yet complete",
            "embedding index build and end-to-end generations are NOT_EXECUTED",
        ],
        "model_operations": "NOT_EXECUTED",
    }
    json_dump(output / "coverage_report.json", report)


def command_preview(args: argparse.Namespace) -> None:
    package_dir = Path(args.package_dir).resolve()
    cases_path = Path(args.cases).resolve()
    output = prepare_output(args.output_dir)
    sys.path.insert(0, str(BACKEND))
    from hanser_agent.persona import PersonaCompiler, build_guidance, build_turn_signals
    from hanser_agent.persona.schemas import ExpressionObservation
    from hanser_agent.persona.style_ranking import rank_fixed_style_candidates

    compiler = PersonaCompiler(package_dir)
    if compiler.effective_settings is None:
        raise ValueError("preview requires a v2 package")
    rows: list[dict[str, object]] = []
    for case in _read_jsonl(cases_path):
        case_id = str(case["case_id"])
        message = str(case["input"])
        signals = build_turn_signals(
            message,
            current_message_ref=f"case:{case_id}:current_user",
            planner_payload=case.get("planner_signals", {}),
        )
        plan = case.get("plan", {})
        decision = build_guidance(
            signals,
            permissions=case.get("permissions", {}),
            observations=None,
            effective_persona=compiler.effective_settings,
            response_mode=str(plan.get("response_mode", "casual")),
            fact_sensitivity=str(plan.get("fact_sensitivity", "low")),
            need_wiki=bool(plan.get("need_wiki", False)),
        )
        snapshot = compiler.compile(
            str(plan.get("response_mode", "casual")),
            turn_signals=signals,
            behavior_decision=decision,
            effective_settings=compiler.effective_settings,
            fact_sensitivity=str(plan.get("fact_sensitivity", "low")),
            need_wiki=bool(plan.get("need_wiki", False)),
        )
        rows.append(
            {
                "case_id": case_id,
                "variant": "persona_v2_offline",
                "input_sha256": sha256_text(message),
                "input_scope": [f"case:{case_id}:current_user"],
                "turn_signals": signals.model_dump(mode="json"),
                "behavior_decision": decision.model_dump(mode="json"),
                "package_id": snapshot.package_id,
                "settings_sha256": snapshot.settings_sha256,
                "persona_source_sha256": snapshot.source_sha256,
                "context_sha256": snapshot.render_sha256,
                "raw_text": None,
                "final_text": None,
                "generation_status": "NOT_EXECUTED",
                "model_call_count": 0,
                "embedding_call_count": 0,
                "reranker_call_count": 0,
                "judge_call_count": 0,
                "tts_call_count": 0,
            }
        )
    jsonl_dump(output / "pipeline_traces.jsonl", rows)
    json_dump(
        output / "preview_summary.json",
        {
            "mode": "OFFLINE_COMPILE_PREVIEW",
            "cases": len(rows),
            "package_id": compiler.package_id,
            "unique_context_hashes": len({str(row["context_sha256"]) for row in rows}),
            "model_operations": "NOT_EXECUTED",
        },
    )
    if args.retrieval_fixtures:
        retrieval_rows: list[dict[str, object]] = []
        for fixture in _read_jsonl(Path(args.retrieval_fixtures).resolve()):
            case_id = str(fixture["case_id"])
            signals = build_turn_signals(
                str(fixture["input"]),
                current_message_ref=f"retrieval:{case_id}:current_user",
                planner_payload=fixture.get("planner_signals", {}),
            )
            plan = fixture.get("plan", {})
            observations = (
                ExpressionObservation.model_validate(fixture["observations"])
                if fixture.get("observations")
                else None
            )
            decision = build_guidance(
                signals,
                permissions=fixture.get("permissions", {}),
                observations=observations,
                effective_persona=compiler.effective_settings,
                response_mode=str(plan.get("response_mode", "casual")),
                fact_sensitivity=str(plan.get("fact_sensitivity", "low")),
                need_wiki=bool(plan.get("need_wiki", False)),
            )
            ranked = rank_fixed_style_candidates(
                fixture.get("candidates", []),
                decision,
                response_mode=str(plan.get("response_mode", "casual")),
                active_generation=str(fixture["active_generation"]),
                hidden_groups=set(str(value) for value in fixture.get("hidden_groups", [])),
                observations=observations,
                top_k=int(fixture.get("top_k", 3)),
            )
            retrieval_rows.append(
                {
                    "case_id": case_id,
                    "turn_signals": signals.model_dump(mode="json"),
                    "behavior_decision": decision.model_dump(mode="json"),
                    **ranked,
                    "embedding_status": "NOT_EXECUTED_FIXED_SCORE_FIXTURE",
                    "model_operations": "NOT_EXECUTED",
                }
            )
        jsonl_dump(output / "retrieval_traces.jsonl", retrieval_rows)


def _extract_legacy_style_preview(database: Path, output: Path) -> None:
    rows: list[dict[str, object]] = []
    decisions: list[dict[str, object]] = []
    with ro_connect(database) as connection:
        columns = {
            str(item["name"])
            for item in connection.execute("PRAGMA table_info(style_examples)")
        }
        selected = connection.execute(
            "SELECT * FROM style_examples WHERE review_status='approved' ORDER BY id"
        ).fetchall()
        for item in selected:
            item_id = int(item["id"])
            source_document_id = (
                int(item["source_document_id"])
                if "source_document_id" in columns and item["source_document_id"] is not None
                else None
            )
            source_ref = str(item["source_ref"] or "")
            source_type = str(item["source_type"])
            prior_reviewer = (
                str(item["reviewer_id"])
                if "reviewer_id" in columns and item["reviewer_id"]
                else "unknown"
            )
            start_char = (
                int(item["source_start_char"])
                if "source_start_char" in columns and item["source_start_char"] is not None
                else None
            )
            end_char = (
                int(item["source_end_char"])
                if "source_end_char" in columns and item["source_end_char"] is not None
                else None
            )
            example_id = f"legacy-candidate-style:{item_id}"
            rows.append(
                {
                    "example_id": example_id,
                    "schema_version": 2,
                    "source_episode_id": None,
                    "source_ref": source_ref or None,
                    "source_span": (
                        {"unit": "unicode_codepoint", "start": start_char, "end": end_char}
                        if start_char is not None and end_char is not None
                        else None
                    ),
                    "span_status": "legacy_recorded" if start_char is not None else "pending",
                    "speaker_status": (
                        "transcript_only"
                        if "source_speaker" in columns and item["source_speaker"] == "hanser"
                        else "unknown"
                    ),
                    "provenance_kind": "verbatim" if source_type == "real" else "generated",
                    "user_context": str(item["prompt"]),
                    "character_response": str(item["response"]),
                    "interaction": {
                        "legacy_scene": str(item["scene"]),
                        "legacy_speech_act": str(item["speech_act"]),
                        "behavior_tags": [],
                        "label_status": "pending_v2_review",
                    },
                    "expression": {"tags": [], "intensity_status": "pending_v2_review"},
                    "claims": {"payload": "unreviewed", "fact_eligible": False},
                    "audience": "unknown",
                    "runtime_scope": [],
                    "review_status": "approved" if str(item["review_status"]) == "approved" else str(item["review_status"]),
                    "schema_review_status": "pending",
                    "prior_review": {
                        "status": str(item["review_status"]),
                        "reviewer_id": prior_reviewer,
                        "notes": str(item["review_notes"] or "") if "review_notes" in columns else "",
                    },
                    "group_id": (
                        f"document:{source_document_id}"
                        if source_document_id is not None
                        else f"synthetic:{item_id}"
                    ),
                    "index_generation": None,
                }
            )
            decisions.append(
                {
                    "item_id": example_id,
                    "schema_version": 1,
                    "dimension": "persona_v2_payload_behavior_migration",
                    "decision": "pending",
                    "reason": "legacy approval is preserved but does not establish v2 payload, audience, behavior or expression labels",
                    "reviewer_kind": "migration_rule",
                    "reviewer_id": "persona_offline.extract_legacy_style_preview.v1",
                    "prior_reviewer_id": prior_reviewer,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "replacement_of": None,
                }
            )
    jsonl_dump(output / "style_examples.reviewed.jsonl", rows)
    jsonl_dump(output / "review_decisions.jsonl", decisions)
    json_dump(
        output / "legacy_style_migration_summary.json",
        {
            "database": relative_path(database),
            "legacy_approved_rows": len(rows),
            "v2_schema_approved_rows": 0,
            "v2_schema_pending_rows": len(rows),
            "runtime_eligible_rows": 0,
            "model_operations": "NOT_EXECUTED",
        },
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{number} is not an object")
        rows.append(value)
    return rows


def _date_from_name(filename: str) -> str | None:
    match = re.search(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日", filename)
    if not match:
        return None
    year, month, day = (int(value) for value in match.groups())
    return f"{year:04d}-{month:02d}-{day:02d}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inventory = commands.add_parser("inventory")
    inventory.add_argument("--db", required=True)
    inventory.add_argument("--data-dir", required=True)
    inventory.add_argument("--output-dir", required=True)
    inventory.set_defaults(func=command_inventory)

    extract = commands.add_parser("extract")
    extract.add_argument("--db", required=True)
    extract.add_argument("--seeds", required=True)
    extract.add_argument("--output-dir", required=True)
    extract.add_argument("--legacy-candidate-db")
    extract.set_defaults(func=command_extract)

    validate = commands.add_parser("validate")
    validate.add_argument("--package-dir", required=True)
    validate.add_argument("--data-dir", required=True)
    validate.add_argument("--eval-dir", required=True)
    validate.add_argument("--output-dir", required=True)
    validate.set_defaults(func=command_validate)

    coverage = commands.add_parser("coverage")
    coverage.add_argument("--data-dir", required=True)
    coverage.add_argument("--eval-dir", required=True)
    coverage.add_argument("--output-dir", required=True)
    coverage.set_defaults(func=command_coverage)

    preview = commands.add_parser("preview")
    preview.add_argument("--package-dir", required=True)
    preview.add_argument("--cases", required=True)
    preview.add_argument("--retrieval-fixtures")
    preview.add_argument("--output-dir", required=True)
    preview.set_defaults(func=command_preview)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
