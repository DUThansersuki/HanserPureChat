from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from hanser_agent.agent.planner import DialoguePlanner, PlannerDecision
from hanser_agent.config import load_settings
from hanser_agent.model_gateway import ModelGateway
from hanser_agent.models import ChatMessage


class FixedGateway:
    def __init__(self, payload: object):
        self.payload = payload

    async def generate_json_with_status(self, profile_name, messages, schema):
        del profile_name, messages
        if self.payload == "__failure__":
            raise ValueError("recorded planner failure")
        return schema.model_validate(self.payload), []


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate the actual desktop DialoguePlanner entrypoint")
    parser.add_argument(
        "--cases",
        type=Path,
        default=BACKEND_ROOT / "data" / "eval" / "style_planner_v1" / "cases.dev.jsonl",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, help="Use the real configured provider; omitted means fixture-only/no network")
    parser.add_argument("--case-id", action="append", default=[], help="Evaluate only the named development case; repeatable")
    return parser.parse_args()


async def evaluate(args: argparse.Namespace) -> None:
    cases = [
        json.loads(line) for line in args.cases.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.case_id:
        requested = set(args.case_id)
        cases = [case for case in cases if case["case_id"] in requested]
        missing = requested.difference(str(case["case_id"]) for case in cases)
        if missing:
            raise ValueError(f"unknown case ids: {sorted(missing)}")
    real_gateway = None
    if args.config:
        settings = load_settings(args.config)
        real_gateway = ModelGateway({"planner": settings.planner})
    results: list[dict[str, object]] = []
    try:
        for case in cases:
            gateway = real_gateway or FixedGateway(case["planner_fixture"])
            planner = DialoguePlanner(gateway)
            history = [ChatMessage.model_validate(item) for item in case.get("history", [])]
            try:
                plan = await planner.plan(str(case["message"]), history, case.get("summary"))
                actual = {
                    "intent": plan.intent,
                    "need_wiki": plan.need_wiki,
                    "query": plan.standalone_query,
                    "continuation_policy": plan.continuation_policy,
                }
                expected = case["expected"]
                passed = all(actual.get(key) == value for key, value in expected.items())
                results.append({
                    "case_id": case["case_id"], "contract_success": plan.planner_route_basis != "fallback",
                    "route_basis": plan.planner_route_basis, "failure_kind": plan.planner_failure_kind,
                    "degraded_reasons": plan.degraded_reasons, "actual": actual,
                    "expected": expected, "passed": passed,
                    "input_estimated_tokens": plan.planner_input_estimated_tokens,
                    "history_messages": plan.planner_history_messages,
                })
            except Exception as exc:
                results.append({
                    "case_id": case["case_id"], "contract_success": False,
                    "uncaught_error": type(exc).__name__, "passed": False,
                })
    finally:
        if real_gateway is not None:
            await real_gateway.close()

    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "real_provider" if args.config else "offline_fixtures",
        "network_requested": bool(args.config),
        "planner_module": str(Path(sys.modules[DialoguePlanner.__module__].__file__).resolve()),
        "style_search_module": str((BACKEND_ROOT / "hanser_agent" / "agent" / "tools" / "style_search.py").resolve()),
        "case_count": len(results),
        "passed": sum(bool(item.get("passed")) for item in results),
        "contract_successes": sum(bool(item.get("contract_success")) for item in results),
        "fallbacks": sum(item.get("route_basis") == "fallback" for item in results),
        "results": results,
    }
    routing_rows = [
        item for item in results
        if isinstance(item.get("expected"), dict)
        and "need_wiki" in item["expected"]
        and isinstance(item.get("actual"), dict)
    ]
    true_positive = sum(
        item["expected"]["need_wiki"] is True and item["actual"]["need_wiki"] is True
        for item in routing_rows
    )
    false_positive = sum(
        item["expected"]["need_wiki"] is False and item["actual"]["need_wiki"] is True
        for item in routing_rows
    )
    false_negative = sum(
        item["expected"]["need_wiki"] is True and item["actual"]["need_wiki"] is False
        for item in routing_rows
    )
    true_negative = len(routing_rows) - true_positive - false_positive - false_negative
    payload["routing_metrics"] = {
        "true_positive": true_positive, "false_positive": false_positive,
        "false_negative": false_negative, "true_negative": true_negative,
        "wiki_precision": true_positive / max(1, true_positive + false_positive),
        "wiki_recall": true_positive / max(1, true_positive + false_negative),
        "non_fact_false_positive_rate": false_positive / max(1, false_positive + true_negative),
    }
    args.output.parent.mkdir(parents=True, exist_ok=False)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        **{key: payload[key] for key in ("mode", "case_count", "passed", "fallbacks")},
        "routing_metrics": payload["routing_metrics"],
    }, ensure_ascii=False))
    if payload["passed"] != payload["case_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(evaluate(parse_args()))
