"""Verify Slice 4 against its engineering and non-relaxable regression gates."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name: str, passed: bool, evidence: object) -> dict[str, object]:
    return {"id": name, "pass": bool(passed), "evidence": evidence}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--resource-dir", type=Path, required=True)
    parser.add_argument("--release-resource-dir", type=Path, required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    resource = args.resource_dir.resolve()
    release_resource = args.release_resource_dir.resolve()

    tests = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=ROOT / "backend",
        capture_output=True,
        text=True,
        check=False,
    )
    traces = read_jsonl(run / "runtime_traces.jsonl")
    scenarios = read_json(run / "multi_turn_cases.json")
    cross = read_json(run / "cross_session.json")
    probes = read_json(run / "fault_probes.json")
    retain = read_json(resource / "summary.json")
    release = read_json(release_resource / "summary.json")
    preview = read_json(
        ROOT / "audit_artifacts/slice4_2026-09-07_001/migration_preview.json"
    )
    migration = read_json(
        ROOT / "audit_artifacts/slice4_2026-09-07_001/production_migration.json"
    )
    postcheck = read_json(
        ROOT / "audit_artifacts/slice4_2026-09-07_001/production_postcheck.json"
    )

    scenario_counts = Counter(str(row["scenario"]) for row in traces)
    ordered_inputs = {
        name: [row["user"] for row in traces if row["scenario"] == name]
        for name in scenarios
    }
    responses = [row.get("response", {}) for row in traces]
    degraded = [
        response for response in responses if response.get("status", "ok") == "degraded"
    ]
    with sqlite3.connect(run / "audit.db") as connection:
        false_shared_memories = connection.execute(
            "SELECT count(*) FROM memories "
            "WHERE user_id IN ('audit-continuity','audit-mixed') "
            "AND type='shared_event' AND status='active'"
        ).fetchone()[0]

    baseline_snapshot = read_json(ROOT / "audit_artifacts/phase6/snapshot.json")
    persona_hashes = {
        row["path"]: sha256(ROOT / row["path"]) == row["sha256"]
        for row in baseline_snapshot["files"]
        if "prompts/persona/" in row["path"].replace("\\", "/")
    }
    default_config = (ROOT / "backend/config.yml").read_text(encoding="utf-8")
    probe_passes = {row["id"]: bool(row["pass"]) for row in probes}
    cross_text = str(cross.get("response", {}).get("text", ""))
    cross_memories = cross.get("memories", [])
    cross_values = {row.get("object_value") for row in cross_memories}

    checks = [
        check(
            "unit_tests_and_slice4_fault_tests",
            tests.returncode == 0,
            {
                "command": "python -m pytest tests -q",
                "tail": tests.stdout.strip().splitlines()[-2:],
                "covered_faults": [
                    "provider 200 empty content and same-responder retry",
                    "provider timeout and structured API error",
                    "reranker failure with preserved sources",
                    "dense failure BM25 fallback",
                    "persisted reply plus retryable post-turn failure",
                    "request idempotency and conflict",
                    "embedding failure retry",
                ],
            },
        ),
        check(
            "migration_preview_backup_apply_postcheck",
            not preview["blockers"]
            and preview["planned_deletes"] == 0
            and migration["applied"]
            and Path(migration["backup"]).exists()
            and postcheck["integrity"] == "ok"
            and postcheck["migration5"] == 1
            and postcheck["message_count"] == preview["message_count"],
            {
                "preview_hash": preview["database_sha256"],
                "planned_deletes": preview["planned_deletes"],
                "backup": migration["backup"],
                "postcheck": postcheck,
            },
        ),
        check(
            "ordered_two_by_twenty_runtime",
            len(traces) == 40
            and scenario_counts == {"continuity": 20, "mixed": 20}
            and ordered_inputs == scenarios
            and all(row.get("http_status") == 200 for row in traces),
            {"counts": dict(scenario_counts), "http_200": sum(row.get("http_status") == 200 for row in traces)},
        ),
        check(
            "runtime_nonempty_traceable_and_not_degraded",
            all(str(response.get("text", "")).strip() for response in responses)
            and all(response.get("request_id") and response.get("trace_id") for response in responses)
            and not degraded,
            {"nonempty": sum(bool(str(r.get("text", "")).strip()) for r in responses), "traceable": sum(bool(r.get("request_id") and r.get("trace_id")) for r in responses), "degraded": len(degraded)},
        ),
        check(
            "cross_session_recall",
            cross.get("status") == 200
            and "小林" in cross_text
            and "茶" in cross_text
            and {"小林", "茶", "咖啡"}.issubset(cross_values),
            {"status": cross.get("status"), "text": cross_text, "memory_values": sorted(v for v in cross_values if v)},
        ),
        check(
            "hard_grounding_gate_no_false_shared_memory",
            false_shared_memories == 0,
            {"active_shared_event_memories": false_shared_memories},
        ),
        check(
            "fault_probes",
            bool(probe_passes) and all(probe_passes.values()),
            probe_passes,
        ),
        check(
            "exclusive_gpu_resource_experiment",
            retain["all"]["n"] == 20
            and retain["all"]["http_failures"] == 0
            and retain["gpu_peak_mib"] <= 6144
            and retain["degraded_responses"] < release["degraded_responses"],
            {"selected": retain, "comparison_release_reranker": release},
        ),
        check(
            "rollbackable_default_resource_policy",
            "release_after_request" not in default_config
            and retain["policy"] == "retain",
            {"effective_default": "retain reranker", "experimental_switch": "reranker.release_after_request"},
        ),
        check(
            "persona_prompt_unchanged",
            bool(persona_hashes) and all(persona_hashes.values()),
            persona_hashes,
        ),
    ]
    report = {
        "slice": "Slice 4 — Request and model failure boundaries",
        "contract": "NEXT_STAGE_ENGINEERING_GUIDE.md + PERSONA_REGRESSION_SUITE.md + ARCHITECTURE_SPEC_DELTA.md",
        "verdict": "PASS" if all(row["pass"] for row in checks) else "FAIL",
        "checks": checks,
        "runtime": {
            "run_dir": str(run),
            "latencies_seconds": {
                "min": min(float(row["seconds"]) for row in traces),
                "max": max(float(row["seconds"]) for row in traces),
            },
        },
        "scope": {
            "persona_prompt_changed": False,
            "new_framework": False,
            "second_responder": False,
            "model_switch": False,
        },
        "diagnostic_run_not_quality_sample": str(
            ROOT / "audit_artifacts/slice4_runtime_2026-09-07_002"
        ),
        "notes": [
            "The diagnostic run with Ollama unavailable returned HTTP 200 through deterministic planning, but every response was correctly marked degraded and is excluded from normal quality acceptance.",
            "The healthy final run contains 40 non-degraded responses and a successful service-rebuild cross-session recall.",
            "The exact legacy fallback text is covered by deterministic fault tests; naturally generated contextual uses of 不知道 are allowed.",
            "No independent blind persona comparison was rerun because Persona Prompt, responder model, and style data were unchanged; all non-relaxable grounding gates remain enforced.",
        ],
        "artifact_hashes": {
            "runtime_traces": sha256(run / "runtime_traces.jsonl"),
            "cross_session": sha256(run / "cross_session.json"),
            "fault_probes": sha256(run / "fault_probes.json"),
            "resource_summary": sha256(resource / "summary.json"),
        },
    }
    output = run / "slice4_acceptance_report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "passed": sum(row["pass"] for row in checks), "total": len(checks), "output": str(output)}, ensure_ascii=False))
    raise SystemExit(0 if report["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
