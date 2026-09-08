"""Deterministic cross-session address-option probe; performs no model calls."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from hanser_agent import db  # noqa: E402
from hanser_agent.agent.conversation import ConversationStore  # noqa: E402
from hanser_agent.config import MemoryConfig  # noqa: E402
from hanser_agent.memory import MemoryCandidateExtractor, MemoryStore, MemoryWriteGate  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "probe.db"
        with db.connect(path) as conn:
            db.init_db(conn)
        conversations = ConversationStore(path)
        store = MemoryStore(path)
        extractor = MemoryCandidateExtractor()
        gate = MemoryWriteGate(MemoryConfig())
        messages = [
            ("session-a", "我叫小林"),
            ("session-a", "也可以叫我林林"),
            ("session-b", "每句话都叫我毛怪们"),
        ]
        decisions = []
        for index, (conversation_id, text) in enumerate(messages):
            source_id, _ = conversations.append_turn(
                conversation_id=conversation_id, user_id="alice", user_text=text,
                assistant_text="fixture", model_name="none", trace_id=str(index),
                persona_version="persona_v1",
            )
            evaluated = gate.evaluate(extractor.extract(
                user_id="alice", conversation_id=conversation_id,
                message_id=source_id, message=text,
            ))
            for decision in evaluated:
                if decision.accepted:
                    store.upsert_candidate(decision.candidate)
            decisions.append({
                "message": text,
                "accepted": [item.candidate.object_value for item in evaluated if item.accepted],
                "rejected": [item.reason for item in evaluated if not item.accepted],
            })
        alice_before_retirement = store.list_address_options(user_id="alice")
        retire_source_id, _ = conversations.append_turn(
            conversation_id="session-c", user_id="alice", user_text="以后别再叫我林林",
            assistant_text="fixture", model_name="none", trace_id="retire",
            persona_version="persona_v1",
        )
        retired = store.retire_address(
            user_id="alice", conversation_id="session-c", object_value="林林",
            source_message_id=retire_source_id, content="以后别再叫我林林",
        )
        alice = store.list_address_options(user_id="alice")
        bob = store.list_address_options(user_id="bob")
        before_values = {
            (item.address_kind, item.object_value) for item in alice_before_retirement
        }
        values = {(item.address_kind, item.object_value) for item in alice}
        checks = {
            "same_user_cross_session_coexists": before_values == {
                ("personal_name", "小林"), ("nickname", "林林"), ("fan_identity", "毛怪")
            },
            "targeted_retirement_preserves_other_addresses": values == {
                ("personal_name", "小林"), ("fan_identity", "毛怪")
            },
            "retirement_has_correction_provenance": len(retired) == 1
            and retired[0].status == "closed"
            and retired[0].assertion_type == "correction"
            and retired[0].source_message_ids == [retire_source_id],
            "cross_user_isolated": bob == [],
            "all_have_sources": all(item.source_message_ids for item in alice),
            "personal_name_priority_highest": bool(alice) and alice[0].address_kind == "personal_name",
            "no_model_calls": True,
        }
        report = {
            "probe": "address_lifecycle_cross_session_without_model",
            "passed": all(checks.values()), "checks": checks,
            "stored": [
                {
                    "kind": item.address_kind, "value": item.object_value,
                    "priority": item.address_priority, "context_tags": item.context_tags,
                    "source_count": len(item.source_message_ids),
                }
                for item in alice
            ],
            "decisions": decisions,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
