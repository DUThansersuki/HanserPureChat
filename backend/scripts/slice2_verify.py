"""Report Slice 2 readiness without treating missing human review as success."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    audit = json.loads((run_dir / "candidate_audit.json").read_text(encoding="utf-8"))
    migration = json.loads(
        (ROOT / "audit_artifacts/slice2_migration_apply.json").read_text(encoding="utf-8")
    )
    candidate = run_dir / "candidate.db"
    with sqlite3.connect(candidate) as conn:
        counts = dict(conn.execute(
            "SELECT review_status,count(*) FROM style_examples GROUP BY review_status"
        ).fetchall())
        migrated = conn.execute("SELECT 1 FROM schema_migrations WHERE version=4").fetchone()
        vectors = conn.execute(
            "SELECT count(*) FROM vector_embeddings_v2 v JOIN active_index_generations p "
            "ON p.collection=v.collection AND p.generation=v.generation "
            "WHERE v.collection='style_examples'"
            if migrated else
            "SELECT count(*) FROM vector_embeddings WHERE collection='style_examples'"
        ).fetchone()[0]
    approved = counts.get("approved", 0)
    deterministic_ready = bool(
        audit["deterministic_pass"]
        and migration.get("migration", {}).get("version") == 2
        and (run_dir / "human_source_review_sample.csv").exists()
    )
    verdict = "READY_FOR_HUMAN_REVIEW"
    if not deterministic_ready:
        verdict = "FAIL"
    elif approved:
        verdict = "READY_FOR_CANDIDATE_INDEX"
    report = {
        "contract": "NEXT_STAGE_ENGINEERING_GUIDE.md Slice 2 + PERSONA_REGRESSION_SUITE.md",
        "verdict": verdict,
        "deterministic_implementation_pass": deterministic_ready,
        "migration_version": migration.get("migration"),
        "candidate_review_counts": counts,
        "candidate_vector_count": vectors,
        "production_switched": False,
        "required_before_pass": [
            "Named human source review decisions for the blinded sample and priority scenes",
            "Approved-only candidate index completeness check",
            "Fixed 37-case B/C and top-k rerun",
            "Independent stratified human blind pairwise with grounding scored separately",
        ],
        "not_executed": [
            "candidate index publication",
            "37-case candidate B/C generation",
            "candidate top-k evaluation",
            "independent human blind pairwise",
        ] if not approved else [],
    }
    path = run_dir / "slice2_status.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(1 if verdict == "FAIL" else 0)


if __name__ == "__main__":
    main()
