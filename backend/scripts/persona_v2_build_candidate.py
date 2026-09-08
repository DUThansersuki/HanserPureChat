"""Build an isolated Persona v2 candidate DB from frozen semantic reviews.

The source DB is never modified. Existing vectors are reused only because the
review step does not alter prompt/response text; a new atomic Style generation
contains exactly the deterministically eligible item IDs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from hanser_agent import db  # noqa: E402
from hanser_agent.persona.data_pipeline import STYLE_COLLECTION, style_row_to_model  # noqa: E402


SAFE_PAYLOADS = {"reaction_only", "turn_local_stance"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _review_map(path: Path | None) -> dict[int, dict[str, object]]:
    if path is None:
        return {}
    rows = _read_jsonl(path)
    result = {int(row["item_id"]): row for row in rows}
    if len(result) != len(rows):
        raise ValueError(f"duplicate item_id in {path}")
    return result


def _calibration_metrics(
    primary: dict[int, dict[str, object]],
    secondary: dict[int, dict[str, object]],
) -> dict[str, object]:
    shared = sorted(set(primary).intersection(secondary))
    if not shared:
        return {
            "items": 0,
            "decision_agreement": None,
            "payload_agreement": None,
            "behavior_exact_agreement": None,
            "expression_exact_agreement": None,
        }
    decision = sum(primary[item]["decision"] == secondary[item]["decision"] for item in shared)
    payload = sum(primary[item]["payload_class"] == secondary[item]["payload_class"] for item in shared)
    behavior = sum(
        set(primary[item].get("behavior_tags", []))
        == set(secondary[item].get("behavior_tags", []))
        for item in shared
    )
    expression = sum(
        set(primary[item].get("expression_tags", []))
        == set(secondary[item].get("expression_tags", []))
        for item in shared
    )
    count = len(shared)
    return {
        "items": count,
        "decision_agreement": round(decision / count, 6),
        "payload_agreement": round(payload / count, 6),
        "behavior_exact_agreement": round(behavior / count, 6),
        "expression_exact_agreement": round(expression / count, 6),
    }


def _semantic_approval(
    item_id: int,
    primary: dict[int, dict[str, object]],
    secondary: dict[int, dict[str, object]],
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    first = primary.get(item_id)
    if first is None:
        return False, ["missing_primary_semantic_review"]
    if first.get("decision") != "approved":
        reasons.append(f"primary_{first.get('decision', 'invalid')}")
    if first.get("payload_class") not in SAFE_PAYLOADS:
        reasons.append("primary_payload_not_runtime_safe")
    if first.get("confidence") not in {"high", "medium"}:
        reasons.append("primary_confidence_low")
    second = secondary.get(item_id)
    if second is not None:
        if second.get("decision") != "approved":
            reasons.append("independent_review_not_approved")
        if second.get("payload_class") not in SAFE_PAYLOADS:
            reasons.append("independent_payload_not_runtime_safe")
    return not reasons, reasons


def run(args: argparse.Namespace) -> None:
    source_db = args.source_db.resolve()
    primary_path = args.reviews.resolve()
    secondary_path = args.calibration_reviews.resolve() if args.calibration_reviews else None
    package_dir = args.package_dir.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    candidate_db = output / "candidate.db"
    shutil.copy2(source_db, candidate_db)

    primary = _review_map(primary_path)
    secondary = _review_map(secondary_path)
    calibration = _calibration_metrics(primary, secondary)
    if secondary and float(calibration["decision_agreement"] or 0.0) < args.min_decision_agreement:
        raise ValueError(
            "independent review decision agreement is below the configured gate: "
            f"{calibration['decision_agreement']} < {args.min_decision_agreement}"
        )

    manifest_hash = _sha256(package_dir / "manifest.yaml")
    source_hash = _sha256(source_db)
    primary_hash = _sha256(primary_path)
    secondary_hash = _sha256(secondary_path) if secondary_path else "none"
    generation_basis = json.dumps(
        {
            "package_manifest": manifest_hash,
            "primary_reviews": primary_hash,
            "secondary_reviews": secondary_hash,
            "builder": "persona_v2_candidate_builder_v1",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    generation_hash = hashlib.sha256(generation_basis.encode("utf-8")).hexdigest()
    generation = f"persona-v2-style-{generation_hash[:16]}"
    now = datetime.now(timezone.utc).isoformat()

    with db.connect(candidate_db) as conn:
        db.init_db(conn)
        db.apply_slice5_ownership_index_migration(conn)

    decisions: list[dict[str, object]] = []
    eligible_ids: list[int] = []
    with db.connect(candidate_db) as conn:
        rows = conn.execute(
            "SELECT * FROM style_examples WHERE review_status='approved' ORDER BY id"
        ).fetchall()
        for row in rows:
            item_id = int(row["id"])
            semantic_ok, reasons = _semantic_approval(item_id, primary, secondary)
            deterministic_checks = {
                "old_review_approved": str(row["review_status"]) == "approved",
                "real_primary": (
                    str(row["source_type"]) == "real"
                    and str(row["source_tier"]) == "primary"
                ),
                "speaker_hanser": str(row["source_speaker"] or "") == "hanser",
                "source_turns_present": bool(row["source_user_turn"] and row["source_response_turn"]),
                "source_span_present": all(
                    row[name] is not None
                    for name in (
                        "source_document_id",
                        "source_start_line",
                        "source_end_line",
                        "source_start_char",
                        "source_end_char",
                    )
                ),
                "embedding_present": bool(row["embedding_ref"]),
                "response_length_safe": 0 < len(str(row["response"])) <= 240,
                "prompt_present": bool(str(row["prompt"]).strip()),
            }
            failed_checks = [name for name, passed in deterministic_checks.items() if not passed]
            eligible = semantic_ok and not failed_checks
            first = primary.get(item_id, {})
            metadata = json.loads(str(row["metadata_json"] or "{}"))
            metadata.update(
                {
                    "provenance_kind": "verbatim" if str(row["source_type"]) == "real" else "designed",
                    "payload_class": str(first.get("payload_class", "unreviewed")),
                    "schema_review_status": "approved" if eligible else "rejected",
                    "behavior_tags": [str(value) for value in first.get("behavior_tags", [])],
                    "expression_tags": [str(value) for value in first.get("expression_tags", [])],
                    "audience": str(first.get("audience", "unknown")),
                    "group_id": (
                        f"source.document:{int(row['source_document_id'])}"
                        if row["source_document_id"] is not None
                        else f"source_ref:{row['source_ref']}"
                    ),
                    "speaker_status": (
                        "transcript_verified"
                        if deterministic_checks["speaker_hanser"]
                        and deterministic_checks["source_span_present"]
                        else "unverified"
                    ),
                    "runtime_scope": ["style_runtime"] if eligible else [],
                    "hidden_eval": False,
                    "semantic_review": {
                        "primary_reviewer": first.get("reviewer_id"),
                        "primary_prompt_sha256": first.get("review_prompt_sha256"),
                        "primary_confidence": first.get("confidence"),
                        "independent_reviewed": item_id in secondary,
                    },
                }
            )
            conn.execute(
                "UPDATE style_examples SET metadata_json=?, index_generation=? WHERE id=?",
                (
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    generation if eligible else None,
                    item_id,
                ),
            )
            if eligible:
                eligible_ids.append(item_id)
            decisions.append(
                {
                    "item_id": item_id,
                    "decision": "approved" if eligible else "rejected",
                    "semantic_review_ok": semantic_ok,
                    "semantic_reasons": reasons,
                    "deterministic_checks": deterministic_checks,
                    "failed_deterministic_checks": failed_checks,
                    "primary_review": first,
                    "independent_review": secondary.get(item_id),
                    "builder_version": "persona_v2_candidate_builder_v1",
                    "decided_at": now,
                }
            )

        active = conn.execute(
            "SELECT generation,revision FROM active_index_generations WHERE collection=?",
            (STYLE_COLLECTION,),
        ).fetchone()
        if active is None:
            raise ValueError("source candidate has no active Style vector generation after migration")
        source_generation = str(active["generation"])
        source_meta = conn.execute(
            "SELECT model,dimensions FROM index_generations WHERE collection=? AND generation=?",
            (STYLE_COLLECTION, source_generation),
        ).fetchone()
        if source_meta is None:
            raise ValueError("active Style generation metadata is missing")
        conn.execute("DROP TABLE IF EXISTS temp.persona_v2_selected_ids")
        conn.execute("CREATE TEMP TABLE persona_v2_selected_ids(id TEXT PRIMARY KEY)")
        conn.executemany(
            "INSERT INTO persona_v2_selected_ids(id) VALUES (?)",
            [(str(item_id),) for item_id in eligible_ids],
        )
        conn.execute(
            """
            INSERT INTO index_generations(
                collection,generation,status,model,dimensions,source_revision,
                config_hash,item_count,created_at,published_at
            ) VALUES (?,?,'building',?,?,?,?,0,datetime('now'),NULL)
            """,
            (
                STYLE_COLLECTION,
                generation,
                str(source_meta["model"]),
                int(source_meta["dimensions"]),
                f"semantic_reviews:{primary_hash}",
                manifest_hash,
            ),
        )
        conn.execute(
            """
            INSERT INTO vector_embeddings_v2(
                collection,generation,item_id,model,dimensions,vector
            )
            SELECT source.collection,?,source.item_id,source.model,source.dimensions,source.vector
            FROM vector_embeddings_v2 AS source
            JOIN persona_v2_selected_ids AS selected ON selected.id=source.item_id
            WHERE source.collection=? AND source.generation=?
            """,
            (generation, STYLE_COLLECTION, source_generation),
        )
        actual_vectors = int(
            conn.execute(
                "SELECT COUNT(*) FROM vector_embeddings_v2 WHERE collection=? AND generation=?",
                (STYLE_COLLECTION, generation),
            ).fetchone()[0]
        )
        if actual_vectors != len(eligible_ids):
            raise ValueError(
                f"eligible/vector count mismatch: eligible={len(eligible_ids)} vectors={actual_vectors}"
            )
        conn.execute(
            "UPDATE index_generations SET item_count=?,status='active',published_at=datetime('now') "
            "WHERE collection=? AND generation=?",
            (actual_vectors, STYLE_COLLECTION, generation),
        )
        conn.execute(
            "UPDATE index_generations SET status='retired' "
            "WHERE collection=? AND generation<>? AND status='active'",
            (STYLE_COLLECTION, generation),
        )
        conn.execute(
            "UPDATE active_index_generations SET generation=?,revision=revision+1,updated_at=datetime('now') "
            "WHERE collection=?",
            (generation, STYLE_COLLECTION),
        )
        conn.commit()

    with (output / "review_decisions.jsonl").open("x", encoding="utf-8") as handle:
        for decision in decisions:
            handle.write(json.dumps(decision, ensure_ascii=False) + "\n")
    with db.connect(candidate_db) as conn:
        export_rows = conn.execute(
            "SELECT * FROM style_examples WHERE review_status='approved' ORDER BY id"
        ).fetchall()
        with (output / "style_examples.reviewed.jsonl").open("x", encoding="utf-8") as handle:
            for row in export_rows:
                handle.write(style_row_to_model(row).model_dump_json() + "\n")
        active_generations = {
            str(row["collection"]): str(row["generation"])
            for row in conn.execute(
                "SELECT collection,generation FROM active_index_generations ORDER BY collection"
            ).fetchall()
        }

    summary = {
        "built_at": now,
        "source_database": str(source_db),
        "source_database_sha256": source_hash,
        "candidate_database": str(candidate_db),
        "candidate_database_sha256": _sha256(candidate_db),
        "package_manifest_sha256": manifest_hash,
        "primary_reviews_sha256": primary_hash,
        "independent_reviews_sha256": secondary_hash,
        "calibration": calibration,
        "old_approved_rows": len(decisions),
        "v2_runtime_eligible": len(eligible_ids),
        "v2_rejected_or_pending": len(decisions) - len(eligible_ids),
        "decision_counts": dict(Counter(str(row["decision"]) for row in decisions)),
        "style_generation": generation,
        "source_style_generation": source_generation,
        "active_generations": active_generations,
        "vector_strategy": "reused exact embeddings because prompt/response text is unchanged",
        "production_database_modified": False,
    }
    (output / "build_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--calibration-reviews", type=Path)
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--min-decision-agreement", type=float, default=0.75)
    run(parser.parse_args())
