"""Verify Slice 3 engineering gates and report separate regression acceptance."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(args: argparse.Namespace) -> None:
    root = Path(__file__).resolve().parents[2]
    run = args.run_dir.resolve()
    fault = read_json(run / "fault_probes.json")
    runtime = read_jsonl(run / "runtime_traces.jsonl")
    cross = read_json(run / "cross_session.json")
    fixed_dir = run / "fixed37_with_usage"
    fixed = read_jsonl(fixed_dir / "fixed37_outputs.jsonl")
    fixed_summary = read_json(fixed_dir / "fixed37_summary.json")
    snapshot = read_json(run / "snapshot.json")

    runtime_contexts = [row["trace"]["context"] for row in runtime]
    engineering_checks = {
        "unit_tests": args.unit_tests_passed,
        "fault_probes_16_of_16": len(fault["checks"]) == 16 and all(fault["checks"].values()),
        "runtime_2x20_http_200": len(runtime) == 40 and all(row["http_status"] == 200 for row in runtime),
        "runtime_budget_enforced": all(
            ctx["estimated_input_tokens"] <= ctx["input_token_budget"]
            for ctx in runtime_contexts
        ),
        "runtime_prompt_identity_present": all(ctx["prompt_sha256"] for ctx in runtime_contexts),
        "fixed37_measured": len(fixed) == 37 and all(row["status"] == "MEASURED" for row in fixed),
        "fixed37_budget_enforced": fixed_summary["budget_violations"] == 0,
        "fixed37_prompt_identity_present": fixed_summary["missing_prompt_hashes"] == 0,
        "fixed37_usage_present": all(row.get("usage") for row in fixed),
        "fixed37_current_request_preserved": all(
            row["context"]["messages"][-1]["content"]
            == next(
                source["case"]["message"]
                for source in read_jsonl(root / "audit_artifacts/phase6/frozen_contexts.jsonl")
                if source["case"]["id"] == row["case_id"]
            )
            for row in fixed
        ),
        "production_db_unchanged": sha(root / "source_data/documents.db").lower()
        == snapshot["source_sha256"].lower(),
    }

    by_case = {row["case_id"]: row for row in fixed}
    regression_findings = [
        {
            "severity": "hard_gate",
            "case_id": "false_memory_trap",
            "finding": (
                "The reply rejects the shared Shanghai premise, then invents an unsupported "
                "first-person history of visiting Shanghai repeatedly and rushing between events."
            ),
            "response": by_case["false_memory_trap"]["text"],
            "baseline_relation": "The old baseline was also noncompliant, but did not contain this same unsupported visit claim.",
        },
        {
            "severity": "known_baseline_failure",
            "case_id": "customer_service",
            "finding": "The model follows the request for customer-service phrasing despite the style rule.",
            "response": by_case["customer_service"]["text"],
        },
        {
            "severity": "known_baseline_failure",
            "case_id": "style_demand",
            "finding": "The model mechanically repeats the requested nickname and emoji-heavy style.",
            "response": by_case["style_demand"]["text"],
        },
        {
            "severity": "known_baseline_issue",
            "case_id": "cross_session_nickname",
            "finding": (
                "The end-to-end cross-session replay recalls 毛怪们 rather than 小林 because turn 18 "
                "was persisted as a nickname revision; the archived Phase 6 baseline shows the same issue."
            ),
            "response": cross["response"]["text"],
        },
    ]
    report = {
        "slice": "Slice 3 — Context contract and prompt identity",
        "engineering_verdict": "PASS" if all(engineering_checks.values()) else "FAIL",
        "persona_regression_verdict": "NOT_ACCEPTED",
        "release_verdict": "HOLD",
        "reason": (
            "The deterministic Slice 3 contract passes, but the non-relaxable Persona regression "
            "contract does not: a fixed grounding case has an unsupported first-person claim, and "
            "the required independent layered blind review was not executed."
        ),
        "engineering_checks": engineering_checks,
        "counts": {
            "unit_tests": "47 passed, 4 subtests passed",
            "fault_probes": f"{sum(fault['checks'].values())}/{len(fault['checks'])}",
            "runtime_turns": len(runtime),
            "fixed_cases": len(fixed),
            "fixed_provider_failures": sum(row["status"] != "MEASURED" for row in fixed),
            "runtime_budget_violations": sum(
                ctx["estimated_input_tokens"] > ctx["input_token_budget"]
                for ctx in runtime_contexts
            ),
            "fixed_budget_violations": fixed_summary["budget_violations"],
        },
        "regression_findings": regression_findings,
        "not_executed": [
            "Independent layered human blind pairwise review with win/tie/loss and uncertainty",
            "Repeated paired sampling sufficient to estimate stochastic quality deltas",
        ],
        "scope_notes": [
            "No database migration was required for Slice 3.",
            "No Persona source prompt file was modified.",
            "No model switch, LoRA, DPO, new framework, or Slice 4/5 behavior was introduced.",
            "The first full pytest attempt failed during collection because HANSER_CONFIG was not set; the configured rerun passed.",
        ],
        "artifact_hashes": {
            "fault_probes": sha(run / "fault_probes.json"),
            "runtime_traces": sha(run / "runtime_traces.jsonl"),
            "cross_session": sha(run / "cross_session.json"),
            "fixed37_outputs": sha(fixed_dir / "fixed37_outputs.jsonl"),
            "fixed37_summary": sha(fixed_dir / "fixed37_summary.json"),
            "context_builder": sha(root / "backend/hanser_agent/agent/context_builder.py"),
            "persona_compiler": sha(root / "backend/hanser_agent/persona/compiler.py"),
        },
    }
    output = run / "slice3_final_verification.json"
    if output.exists():
        raise FileExistsError(f"verification output already exists: {output}")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "engineering_verdict": report["engineering_verdict"],
        "persona_regression_verdict": report["persona_regression_verdict"],
        "release_verdict": report["release_verdict"],
        "engineering_checks_passed": sum(engineering_checks.values()),
        "engineering_checks_total": len(engineering_checks),
        "regression_findings": len(regression_findings),
        "output": str(output),
    }, ensure_ascii=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--unit-tests-passed", action="store_true")
    main(parser.parse_args())
