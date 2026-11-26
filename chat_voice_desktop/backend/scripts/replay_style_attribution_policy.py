from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.run_style_reaction_pipeline_v2 import (
    _program_review,
    project,
    read_jsonl,
    unique_units,
    write_jsonl,
)
from scripts.style_reaction_pipeline import (
    ATTRIBUTION_POLICY_VERSION,
    PIPELINE_VERSION,
    apply_source_prior_attribution_policy,
    route_semantic_review,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source = args.source.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    original = unique_units(source)
    replayed: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    for unit in original:
        updated = apply_source_prior_attribution_policy(unit)
        updated.update(route_semantic_review(updated))
        replayed.append(updated)
        transitions.append({
            "unit_id": unit["unit_id"],
            "old_attribution_status": unit.get("attribution_status"),
            "new_attribution_status": updated.get("attribution_status"),
            "attribution_basis": updated.get("attribution_basis"),
            "old_review_route": unit.get("review_route"),
            "new_review_route": updated.get("review_route"),
            "review_reasons": updated.get("review_reasons", []),
            "risk_flags": updated.get("risk_flags", []),
        })

    for name in ("windows.replay.jsonl", "windows.full.jsonl", "windows.derived.jsonl"):
        source_path = source / name
        if source_path.exists():
            shutil.copy2(source_path, output / name)
    write_jsonl(output / "source_unit_candidates.jsonl", replayed)
    write_jsonl(output / "attribution_transitions.jsonl", transitions)

    program_reviews = [
        _program_review(unit) for unit in replayed if not unit["review_required"]
    ]
    write_jsonl(output / "semantic_reviews.jsonl", program_reviews)
    project(SimpleNamespace(output=output))

    projected = read_jsonl(output / "source_reaction_units.reviewed.jsonl")
    source_prior = [
        unit for unit in replayed if unit.get("attribution_basis") == "source_prior"
    ]
    manifest = {
        "artifact_kind": "offline_attribution_policy_replay",
        "source_run": str(source),
        "pipeline_version": PIPELINE_VERSION,
        "attribution_policy_version": ATTRIBUTION_POLICY_VERSION,
        "provider_calls": 0,
        "source_units": len(replayed),
        "old_attribution_status": dict(Counter(
            str(unit.get("attribution_status")) for unit in original
        )),
        "new_attribution_status": dict(Counter(
            str(unit.get("attribution_status")) for unit in replayed
        )),
        "attribution_basis": dict(Counter(
            str(unit.get("attribution_basis")) for unit in replayed
        )),
        "review_routes": dict(Counter(
            str(unit.get("review_route")) for unit in replayed
        )),
        "source_prior_with_risk": sum(bool(unit.get("risk_flags")) for unit in source_prior),
        "source_prior_without_risk": sum(not unit.get("risk_flags") for unit in source_prior),
        "q_projection": sum(unit.get("q_projection") is not None for unit in projected),
        "a_projection": sum(unit.get("a_projection") is not None for unit in projected),
        "qa": sum(
            unit.get("q_projection") is not None and unit.get("a_projection") is not None
            for unit in projected
        ),
        "a_only": sum(
            unit.get("q_projection") is None and unit.get("a_projection") is not None
            for unit in projected
        ),
        "q_only": sum(
            unit.get("q_projection") is not None and unit.get("a_projection") is None
            for unit in projected
        ),
        "runtime_eligible": sum(bool(unit.get("runtime_eligible")) for unit in projected),
        "runtime_attribution_basis": dict(Counter(
            str(unit.get("effective_attribution_basis"))
            for unit in projected if unit.get("runtime_eligible")
        )),
        "note": "Targeted-review units remain pending in this zero-provider replay.",
    }
    (output / "attribution_policy_replay_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
