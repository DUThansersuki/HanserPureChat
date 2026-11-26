from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    parent = args.parent.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)

    sources = read_jsonl(parent / "sources.frozen.jsonl")
    statuses = read_jsonl(parent / "window_status.jsonl")
    extractions = read_jsonl(parent / "extractions.jsonl")
    split_counts = Counter(str(row.get("split")) for row in sources)
    status_counts = Counter(str(row.get("status")) for row in statuses)

    database = PROJECT_ROOT / "pure_chat_desktop" / "resources" / "database" / "documents.dev.db"
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as conn:
        row = conn.execute(
            "SELECT generation,revision FROM active_index_generations WHERE collection='style_examples'"
        ).fetchone()
    active_generation = {"generation": row[0], "revision": row[1]} if row else None
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True
    ).strip()
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=PROJECT_ROOT, text=True
    ).strip()
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=PROJECT_ROOT, text=True
    ).splitlines()

    manifest = {
        "run_id": output.name,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "parent_run": parent.name,
        "execution_plan": "STYLE_RAG_CODEX_EXECUTION_PLAN_2026-09-16.md",
        "git": {
            "branch": branch,
            "head": head,
            "initial_worktree_dirty": False,
            "dirty_paths_at_manifest_write": dirty,
        },
        "frozen_inputs": {
            "sources": len(sources), "split_counts": dict(split_counts),
            "basic_windows": sum(1 for _ in (parent / "windows.frozen.jsonl").open(encoding="utf-8")),
            "derived_windows": sum(1 for _ in (parent / "windows.derived.jsonl").open(encoding="utf-8")),
            "status_rows": len(statuses), "window_status": dict(status_counts),
            "valid_units": sum(row.get("unit_id") is not None for row in extractions),
            "remaining_windows": sum(row.get("status") in {"pending", "failed"} for row in statuses),
        },
        "input_versions": {
            "sources_sha256": sha256(parent / "sources.frozen.jsonl"),
            "windows_sha256": sha256(parent / "windows.frozen.jsonl"),
            "window_status_sha256": sha256(parent / "window_status.jsonl"),
            "extractions_sha256": sha256(parent / "extractions.jsonl"),
            "reprojection_report": json.loads((parent / "reprojection_report.json").read_text(encoding="utf-8")),
        },
        "contracts": {
            "style_record_version": 3,
            "projection_schema_version": "source-reaction-unit-projection-v1",
            "runtime_payloads": ["pattern_only", "reviewed_fragment"],
            "generation_policies": {
                "M0": "current product policy; has_style template differences retained",
                "M1": "attribution policy; bounded template wording independent of has_style",
            },
        },
        "production": {
            "active_persona_package": "persona-v2-production",
            "active_style_index": active_generation,
            "pointer_change_authorized": False,
            "pointer_modified": False,
        },
        "holdout_policy": {
            "independent_evaluation_holdout_sources": split_counts.get("independent_evaluation_holdout", 0),
            "included_in_build": False,
        },
    }
    (output / "execution_baseline_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "run_parent.json").write_text(
        json.dumps({
            "parent_run": str(parent),
            "reuse_policy": "reference frozen sources/windows/raw; do not overwrite parent",
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
