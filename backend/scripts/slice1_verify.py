"""Verify the deterministic Slice 1 acceptance gates from persisted artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TARGET_PROBES = {
    "false_shared_event",
    "preference_conflict",
    "null_patch",
    "memory_prefilter",
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def check(name: str, passed: bool, evidence) -> dict:
    return {"id": name, "pass": bool(passed), "evidence": evidence}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
    )
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    output = args.output or run_dir / "slice1_acceptance_report.json"

    traces = [
        json.loads(line)
        for line in (run_dir / "runtime_traces.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    scenarios = read_json(run_dir / "multi_turn_cases.json")
    observations = read_json(run_dir / "slice1_acceptance_observations.json")
    cross_session = read_json(run_dir / "cross_session.json")
    cross_user = read_json(run_dir / "cross_user.json")
    probes = read_json(run_dir / "fault_probes.json")
    snapshot = read_json(run_dir / "snapshot.json")
    migration_preview = read_json(ROOT / "audit_artifacts/slice1_migration_preview.json")
    migration_apply = read_json(ROOT / "audit_artifacts/slice1_migration_apply.json")
    migration_postcheck = read_json(ROOT / "audit_artifacts/slice1_migration_postcheck.json")

    pytest = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=ROOT / "backend",
        capture_output=True,
        text=True,
        check=False,
    )

    trace_inputs = {
        scenario: [row["user"] for row in traces if row["scenario"] == scenario]
        for scenario in scenarios
    }
    scenario_counts = Counter(row["scenario"] for row in traces)
    active = observations["active"]
    active_by_key = {row["memory_key"]: row for row in active}
    session_memories = {row["memory_key"]: row for row in cross_session["memories"]}
    probe_by_id = {row["id"]: row for row in probes}

    persona_hashes = {}
    for row in snapshot["files"]:
        path = ROOT / row["path"]
        if "prompts/persona/" in row["path"].replace("\\", "/"):
            persona_hashes[row["path"]] = (
                path.exists()
                and hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]
            )

    gates = [
        check(
            "unit_tests",
            pytest.returncode == 0,
            {"command": "python -m pytest tests -q", "tail": pytest.stdout.strip().splitlines()[-2:]},
        ),
        check(
            "migration_numbered_and_backed_up",
            migration_apply.get("migration", {}).get("version") == 1
            and Path(migration_apply["backup"]).exists()
            and migration_preview["planned_actions"]["physical_memory_deletes"] == 0
            and migration_postcheck["migration_applied"],
            {
                "version": migration_apply.get("migration"),
                "backup": migration_apply.get("backup"),
                "pre_migration_memory_count": migration_apply["before"]["memory_count"],
                "postcheck": migration_postcheck["migration_applied"],
            },
        ),
        check(
            "two_ordered_20_turn_scenarios",
            len(traces) == 40
            and scenario_counts == {"continuity": 20, "mixed": 20}
            and trace_inputs == scenarios
            and all(row["http_status"] == 200 for row in traces),
            {"counts": dict(scenario_counts), "http_200": sum(row["http_status"] == 200 for row in traces)},
        ),
        check(
            "false_shared_reality_not_persisted",
            observations["shared_rows"] == 0
            and any(
                not decision["accepted"]
                and decision["reason"] == "question_not_assertion"
                for decision in observations["turn12_decisions"]
            ),
            {"shared_rows": observations["shared_rows"], "turn12": observations["turn12_decisions"]},
        ),
        check(
            "correction_supersedes_old_preference",
            active_by_key.get("preference:like:咖啡", {}).get("polarity") == "negative"
            and active_by_key.get("preference:like:茶", {}).get("polarity") == "positive"
            and "用户喜欢咖啡" not in {row["content"] for row in active},
            {"active_preferences": [row for row in active if row["type"] == "user_preference"]},
        ),
        check(
            "event_lifecycle_closed",
            bool(observations["closed"])
            and not any(row["type"] == "unresolved_thread" for row in active),
            {"closed": observations["closed"]},
        ),
        check(
            "source_coverage",
            observations["missing_sources"] == 0
            and observations["nonuser_sources"] == 0
            and all(json.loads(row["source_message_ids_json"]) for row in active),
            {
                "active_writes": len(active),
                "missing_sources": observations["missing_sources"],
                "nonuser_sources": observations["nonuser_sources"],
            },
        ),
        check(
            "same_user_cross_session_recall",
            cross_session["status"] == 200
            and session_memories.get("user:name", {}).get("object_value") == "毛怪们"
            and session_memories.get("preference:like:咖啡", {}).get("polarity") == "negative"
            and session_memories.get("preference:like:茶", {}).get("polarity") == "positive",
            {"response": cross_session["response"]["text"], "memory_keys": sorted(session_memories)},
        ),
        check(
            "cross_user_isolation",
            cross_user["status"] == 200
            and not cross_user["retrieved_memories"]
            and not cross_user["other_user_memories"],
            cross_user,
        ),
        check(
            "slice1_fault_probes",
            all(probe_by_id.get(name, {}).get("pass") for name in TARGET_PROBES),
            {name: probe_by_id.get(name) for name in sorted(TARGET_PROBES)},
        ),
        check(
            "persona_prompt_unchanged",
            bool(persona_hashes) and all(persona_hashes.values()),
            persona_hashes,
        ),
    ]
    non_target_probes = [
        row for row in probes if row["id"] not in TARGET_PROBES
    ]
    report = {
        "contract": "PERSONA_REGRESSION_SUITE.md + NEXT_STAGE_ENGINEERING_GUIDE.md Slice 1",
        "run_dir": str(run_dir),
        "verdict": "PASS" if all(item["pass"] for item in gates) else "FAIL",
        "gates": gates,
        "non_target_probe_status": non_target_probes,
        "limitations": [
            "Human blind persona scoring was not rerun because Persona Prompt/model/style were not changed.",
            "The 30-50 turn stress set remains NOT_EXECUTED as explicitly deferred by the contract.",
            "A generated response can still choose wording inconsistent with a valid memory; deterministic memory state and cross-session recall are the Slice 1 gate.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
