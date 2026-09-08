from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hanser_agent.agent.planner import DialoguePlanner
from hanser_agent.config import ModelProfileConfig, Settings, load_settings
from hanser_agent.model_gateway import ModelGateway
from hanser_agent.models import ChatMessage, DialoguePlan


@dataclass(slots=True)
class RouterCase:
    message: str
    history: list[ChatMessage]
    expected_intents: set[str]
    need_wiki: bool
    response_mode: str | None
    rewrite_contains: list[str]


def load_cases(path: Path) -> list[RouterCase]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [
        RouterCase(
            message=str(row["message"]),
            history=[ChatMessage.model_validate(item) for item in row["history"]],
            expected_intents={str(value) for value in row["expected_intents"]},
            need_wiki=bool(row["need_wiki"]),
            response_mode=(
                str(row["response_mode"])
                if row.get("response_mode")
                else None
            ),
            rewrite_contains=[
                str(value)
                for value in row.get("rewrite_contains", [])
            ],
        )
        for row in rows
    ]


def profile_map(
    names: list[str],
    settings: Settings,
    local_keep_alive: str,
) -> dict[str, ModelProfileConfig]:
    profiles: dict[str, ModelProfileConfig] = {}
    if "local" in names:
        profiles["local"] = replace(
            settings.planner,
            fallback_profile=None,
            keep_alive=local_keep_alive,
        )
    if "external" in names:
        if settings.planner_external is None:
            raise ValueError("external planner profile is disabled")
        profiles["external"] = replace(
            settings.planner_external,
            fallback_profile=None,
        )
    return profiles


async def evaluate(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    cases = load_cases(args.dataset)
    names = [value.strip() for value in args.profiles.split(",") if value.strip()]
    gateway = ModelGateway(
        profile_map(names, settings, args.local_keep_alive)
    )
    exit_code = 0
    try:
        for profile_name in names:
            planner = DialoguePlanner(gateway, profile_name=profile_name)
            plans: list[DialoguePlan | None] = []
            latencies: list[float] = []
            errors: list[str] = []
            for case in cases:
                started = time.perf_counter()
                try:
                    plan = await gateway.generate_json(
                        profile_name,
                        planner.build_messages(case.message, case.history),
                        DialoguePlan,
                    )
                except Exception as exc:
                    plan = None
                    errors.append(f"{case.message}: {type(exc).__name__}: {exc}")
                latencies.append(time.perf_counter() - started)
                plans.append(plan)

            schema_success = sum(plan is not None for plan in plans) / len(cases)
            intent_accuracy = sum(
                plan is not None and plan.intent in case.expected_intents
                for case, plan in zip(cases, plans, strict=True)
            ) / len(cases)
            true_positive = sum(
                plan is not None and plan.need_wiki and case.need_wiki
                for case, plan in zip(cases, plans, strict=True)
            )
            false_positive = sum(
                plan is not None and plan.need_wiki and not case.need_wiki
                for case, plan in zip(cases, plans, strict=True)
            )
            false_negative = sum(
                (plan is None or not plan.need_wiki) and case.need_wiki
                for case, plan in zip(cases, plans, strict=True)
            )
            precision = true_positive / max(1, true_positive + false_positive)
            recall = true_positive / max(1, true_positive + false_negative)
            f1 = 2 * precision * recall / max(0.0001, precision + recall)
            rewrite_cases = [
                (case, plan)
                for case, plan in zip(cases, plans, strict=True)
                if case.rewrite_contains
            ]
            rewrite_accuracy = sum(
                plan is not None
                and all(
                    value.lower() in plan.standalone_query.lower()
                    for value in case.rewrite_contains
                )
                for case, plan in rewrite_cases
            ) / max(1, len(rewrite_cases))
            mode_cases = [
                (case, plan)
                for case, plan in zip(cases, plans, strict=True)
                if case.response_mode is not None
            ]
            mode_accuracy = sum(
                plan is not None and plan.response_mode == case.response_mode
                for case, plan in mode_cases
            ) / max(1, len(mode_cases))

            print(
                f"{profile_name}: schema={schema_success:.3f} "
                f"intent={intent_accuracy:.3f} wiki_f1={f1:.3f} "
                f"wiki_precision={precision:.3f} wiki_recall={recall:.3f} "
                f"rewrite={rewrite_accuracy:.3f} mode={mode_accuracy:.3f} "
                f"p50={statistics.median(latencies):.2f}s "
                f"total={sum(latencies):.2f}s"
            )
            for error in errors:
                print(f"  error: {error}")
            for case, plan in zip(cases, plans, strict=True):
                if plan is None:
                    continue
                mismatches: list[str] = []
                if plan.intent not in case.expected_intents:
                    mismatches.append(
                        f"intent={plan.intent} expected={sorted(case.expected_intents)}"
                    )
                if plan.need_wiki != case.need_wiki:
                    mismatches.append(
                        "need_wiki="
                        f"{plan.need_wiki} expected={case.need_wiki}"
                    )
                missing_rewrite = [
                    value
                    for value in case.rewrite_contains
                    if value.lower() not in plan.standalone_query.lower()
                ]
                if missing_rewrite:
                    mismatches.append(
                        f"rewrite_missing={missing_rewrite}"
                    )
                if (
                    case.response_mode is not None
                    and plan.response_mode != case.response_mode
                ):
                    mismatches.append(
                        f"mode={plan.response_mode} expected={case.response_mode}"
                    )
                if mismatches:
                    print(
                        f"  mismatch: {case.message}: "
                        + "; ".join(mismatches)
                    )

            if profile_name == "local" and (
                schema_success < args.min_schema
                or intent_accuracy < args.min_intent
                or f1 < args.min_wiki_f1
                or rewrite_accuracy < args.min_rewrite
            ):
                exit_code = 1
    finally:
        await gateway.close()
    return exit_code


def parse_args() -> argparse.Namespace:
    backend_dir = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=backend_dir / "config.yml",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=backend_dir / "data" / "eval" / "router_phase3.json",
    )
    parser.add_argument("--profiles", default="local,external")
    parser.add_argument("--local-keep-alive", default="5m")
    parser.add_argument("--min-schema", type=float, default=0.95)
    parser.add_argument("--min-intent", type=float, default=0.80)
    parser.add_argument("--min-wiki-f1", type=float, default=0.90)
    parser.add_argument("--min-rewrite", type=float, default=0.80)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(evaluate(parse_args())))
