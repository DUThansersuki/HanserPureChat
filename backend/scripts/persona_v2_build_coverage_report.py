"""Build the final candidate coverage report from reviewed assets and frozen inputs."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "audit_artifacts" / "persona_v2_candidate_2026-09-08_011"
DATA = ROOT / "backend" / "data"
SAFE_PAYLOADS = {"reaction_only", "turn_local_stance"}
SAFE_PROVENANCE = {"verbatim", "adapted"}


def _rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _count(rows: list[dict[str, object]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(str(row.get(field) or "<missing>") for row in rows).items()))


def _exclusion(row: dict[str, object], active: str) -> str | None:
    if row.get("review_status") == "quarantined":
        return "quarantined_fact_payload"
    if row.get("review_status") != "approved":
        return "review_not_approved"
    if row.get("schema_review_status") != "approved":
        return "schema_not_approved"
    if row.get("provenance_kind") not in SAFE_PROVENANCE:
        return "provenance_not_runtime_eligible"
    if row.get("payload_class") not in SAFE_PAYLOADS:
        return "payload_not_runtime_safe"
    if "style_runtime" not in (row.get("runtime_scope") or []):
        return "runtime_scope_excluded"
    if row.get("hidden_eval"):
        return "hidden_eval_group"
    if row.get("index_generation") != active:
        return "generation_mismatch"
    if row.get("provenance_kind") == "verbatim" and row.get("speaker_status") not in {"audio_verified", "transcript_verified"}:
        return "speaker_unverified"
    if row.get("provenance_kind") == "adapted" and row.get("speaker_status") != "not_applicable":
        return "adapted_speaker_status_invalid"
    return None


def main() -> None:
    style = _rows(CANDIDATE / "style_examples.reviewed.jsonl")
    with sqlite3.connect(CANDIDATE / "candidate.db") as conn:
        active = str(conn.execute(
            "SELECT generation FROM active_index_generations WHERE collection='style_examples'"
        ).fetchone()[0])
    reasons = Counter(_exclusion(row, active) or "eligible" for row in style)
    eligible = [row for row in style if _exclusion(row, active) is None]
    source_episodes = _rows(DATA / "persona" / "episodes.jsonl")
    designed_episodes = _rows(DATA / "persona" / "designed_daily_episodes.jsonl")
    daily = _rows(DATA / "eval" / "persona_v2" / "daily_calibration_24.jsonl")
    hidden = _rows(DATA / "eval" / "persona_v2" / "hidden_generalization_8.jsonl")
    sequences = _rows(DATA / "eval" / "persona_v2" / "daily_sequences_4.jsonl")

    expression_counts = Counter(
        str(tag) for row in eligible for tag in (row.get("expression_tags") or [])
    )
    opportunity_denominators = {
        "humor_or_meme": sum(row["category"] == "C4" and "stop" not in str(row["case_id"]) for row in daily),
        "profanity": sum("profanity" in str(row["case_id"]) or "game.fail" in str(row["case_id"]) for row in daily),
        "innuendo": sum("adult.pun" in str(row["case_id"]) for row in daily),
        "cutesy": sum(row["category"] in {"C1", "C2", "C3"} for row in daily),
        "note": "denominators are frozen evaluation opportunities, not source-frequency estimates",
    }
    report = {
        "schema_version": 3,
        "mode": "CANDIDATE_FREEZE_AGGREGATION",
        "candidate_id": "persona_v2_candidate_2026-09-08_011",
        "style_generation": active,
        "style": {
            "total_rows": len(style),
            "eligible_rows": len(eligible),
            "pending_rows": sum(row.get("review_status") == "pending" or row.get("schema_review_status") == "pending" for row in style),
            "quarantined_rows": sum(row.get("review_status") == "quarantined" for row in style),
            "provenance": _count(style, "provenance_kind"),
            "eligible_provenance": _count(eligible, "provenance_kind"),
            "payload_class": _count(style, "payload_class"),
            "eligible_payload_class": _count(eligible, "payload_class"),
            "speaker_status": _count(style, "speaker_status"),
            "runtime_scope": dict(sorted(Counter(scope for row in style for scope in (row.get("runtime_scope") or ["<none>"])).items())),
            "unique_groups_total": len({str(row.get("group_id")) for row in style if row.get("group_id")}),
            "unique_groups_eligible": len({str(row.get("group_id")) for row in eligible if row.get("group_id")}),
            "exclusion_reasons": dict(sorted(reasons.items())),
            "eligible_behavior_tags": dict(sorted(Counter(str(tag) for row in eligible for tag in (row.get("behavior_tags") or [])).items())),
            "eligible_expression_tags": dict(sorted(expression_counts.items())),
            "expression_opportunity_denominators": opportunity_denominators,
            "coverage_caveat": "legacy scene and generic multi-label counts do not prove daily-chat coverage or real-person frequency",
        },
        "episodes": {
            "source_total": len(source_episodes),
            "source_independent_groups": len({row["group_id"] for row in source_episodes}),
            "source_review_status": dict(Counter(str(row["review"]["status"]) for row in source_episodes)),
            "designed_total": len(designed_episodes),
            "designed_independent_groups": len({row["group_id"] for row in designed_episodes}),
            "designed_runtime_eligible": 0,
            "designed_frequency_evidence": 0,
        },
        "evaluation": {
            "legacy_exposed_cases": 72,
            "legacy_exposed_sequences": 6,
            "daily_calibration_cases": len(daily),
            "daily_category_counts": dict(sorted(Counter(str(row["category"]) for row in daily).items())),
            "hidden_generalization_cases": len(hidden),
            "daily_sequences": len(sequences),
            "hidden_sequences": sum(row["exposure"] == "hidden_frozen" for row in sequences),
        },
        "trait_evidence": {
            "source_supported": 6,
            "owner_preference": 4,
            "hypothesis": 1,
            "plain_warmth_without_infantilizing": "research_candidate; not promoted by designed examples",
        },
        "known_gaps": [
            "source episodes remain transcript-only/pending and do not establish cross-context frequency",
            "designed daily episodes validate product behavior only",
            "model acceptance and real lifecycle verdict are reported separately after execution",
        ],
    }
    (DATA / "persona" / "coverage_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
