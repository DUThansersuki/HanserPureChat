from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.run_style_reaction_pipeline_v2 import unique_units, write_jsonl


QUOTAS = {
    "external": 8,
    "conflicted_or_unresolved": 8,
    "explicit_hanser": 10,
    "source_prior_risky": 17,
    "source_prior_clean": 17,
}


def bucket(unit: dict[str, Any]) -> str | None:
    status = str(unit.get("attribution_status"))
    basis = str(unit.get("attribution_basis"))
    if status == "external":
        return "external"
    if status in {"conflicted", "unresolved"}:
        return "conflicted_or_unresolved"
    if basis == "explicit_hanser_label":
        return "explicit_hanser"
    if basis == "source_prior" and unit.get("risk_flags"):
        return "source_prior_risky"
    if basis == "source_prior":
        return "source_prior_clean"
    return None


def stable_key(unit: dict[str, Any]) -> str:
    return hashlib.sha256(str(unit["unit_id"]).encode("utf-8")).hexdigest()


def diverse_sample(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sorted(rows, key=stable_key):
        by_source[str(row["source_id"])].append(row)
    selected: list[dict[str, Any]] = []
    sources = sorted(by_source)
    while len(selected) < limit and sources:
        remaining: list[str] = []
        for source_id in sources:
            if by_source[source_id] and len(selected) < limit:
                selected.append(by_source[source_id].pop(0))
            if by_source[source_id]:
                remaining.append(source_id)
        sources = remaining
    return selected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)

    units = unique_units(source)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for unit in units:
        name = bucket(unit)
        if name is not None:
            grouped[name].append(unit)
    selected: list[dict[str, Any]] = []
    selection_rows: list[dict[str, Any]] = []
    for name, quota in QUOTAS.items():
        picked = diverse_sample(grouped.get(name, []), quota)
        selected.extend(picked)
        selection_rows.extend({
            "unit_id": row["unit_id"], "source_id": row["source_id"],
            "bucket": name, "review_route": row.get("review_route"),
            "attribution_status": row.get("attribution_status"),
            "attribution_basis": row.get("attribution_basis"),
            "risk_flags": row.get("risk_flags", []),
        } for row in picked)

    for name in ("windows.replay.jsonl", "windows.full.jsonl", "windows.derived.jsonl"):
        path = source / name
        if path.exists():
            shutil.copy2(path, output / name)
    write_jsonl(output / "source_unit_candidates.jsonl", selected)
    write_jsonl(output / "review_sample_selection.jsonl", selection_rows)
    manifest = {
        "artifact_kind": "stratified_attribution_review_sample",
        "source_run": str(source),
        "source_units_available": len(units),
        "selected_units": len(selected),
        "selected_source_documents": len({str(row["source_id"]) for row in selected}),
        "requested_quotas": QUOTAS,
        "actual_buckets": dict(Counter(row["bucket"] for row in selection_rows)),
        "production_pointer_modified": False,
    }
    (output / "review_sample_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
