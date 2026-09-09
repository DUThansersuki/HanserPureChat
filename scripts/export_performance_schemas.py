from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from hanser_agent.responder.performance import (  # noqa: E402
    AllowedPerformance as AgentAllowedPerformance,
    Delivery as AgentDelivery,
    OutputPreferences as AgentOutputPreferences,
    PerformanceIntent as AgentPerformanceIntent,
    ReplySnapshot as AgentReplySnapshot,
)
from voice_runtime.contracts import (  # noqa: E402
    AllowedPerformance as RuntimeAllowedPerformance,
    Delivery as RuntimeDelivery,
    JobEvent,
    JobSnapshot,
    SegmentPackage,
    VisualTurnPlan,
    VoiceJobRequest,
)


SCHEMAS = {
    "allowed-performance.schema.json": AgentAllowedPerformance,
    "job-event.schema.json": JobEvent,
    "job-snapshot.schema.json": JobSnapshot,
    "output-preferences.schema.json": AgentOutputPreferences,
    "performance-intent.schema.json": AgentPerformanceIntent,
    "reply-snapshot.schema.json": AgentReplySnapshot,
    "segment-package.schema.json": SegmentPackage,
    "visual-turn-plan.schema.json": VisualTurnPlan,
    "voice-job-request.schema.json": VoiceJobRequest,
}


def main() -> None:
    if {item.value for item in AgentDelivery} != {
        item.value for item in RuntimeDelivery
    }:
        raise RuntimeError("Agent and Runtime delivery enums diverged")
    agent_allowed = AgentAllowedPerformance.model_json_schema()
    runtime_allowed = RuntimeAllowedPerformance.model_json_schema()
    if set(agent_allowed["$defs"]["Delivery"]["enum"]) != set(
        runtime_allowed["$defs"]["Delivery"]["enum"]
    ):
        raise RuntimeError("Agent and Runtime AllowedPerformance diverged")

    destination = ROOT / "contracts" / "generated"
    destination.mkdir(parents=True, exist_ok=True)
    for filename, model in SCHEMAS.items():
        (destination / filename).write_text(
            json.dumps(
                model.model_json_schema(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
