from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.run_style_reaction_pipeline_v2 import (
    read_jsonl, review, review_gap_reasons, select_review_units, write_jsonl,
)


def unit(identity):
    return {"unit_id": identity, "source_id": "DOC-" + identity, "reaction_source_start_char": 0,
            "reaction_source_end_char": 3, "review_required": True, "window_id": "w",
            "reaction_spans": [{"segment_id": "s0001", "start": 0, "end": 3}],
            "reaction_source_slices": [{"segment_id": "s0001", "segment_marker": "unmarked"}],
            "trigger_spans": []}


class ReviewSelectionTests(unittest.TestCase):
    def test_gap_revisit_preserves_compatible_approvals_and_rejections(self):
        rows = [unit(name) for name in ["good", "rejected", "pending", "missing-a", "new", "active"]]
        prior = {"good": [{"decision": "approved", "behavior_ids": ["direct_natural_reply"]}],
                 "rejected": [{"decision": "rejected"}],
                 "pending": [{"decision": "pending", "insufficient_context": True, "needed_context": "both"}] * 2,
                 "missing-a": [{"decision": "approved", "behavior_ids": []}]}
        normal = select_review_units(rows, prior, {"active": {"status": "inflight"}})
        self.assertEqual([row["unit_id"] for row in normal], ["new"])
        revised = select_review_units(rows, prior, {"active": {"status": "inflight"}}, revisit_gaps=True)
        self.assertEqual({row["unit_id"] for row in revised}, {"pending", "missing-a", "new"})
        self.assertEqual(revised[-1]["unit_id"], "new")
        self.assertEqual(review_gap_reasons(prior["missing-a"][-1]), ["approved_missing_a_projection"])

    def test_explicit_ids_limit_revisit(self):
        rows = [unit("new"), unit("missing-a")]
        prior = {"missing-a": [{"decision": "approved", "behavior_ids": []}]}
        selected = select_review_units(rows, prior, {}, revisit_gaps=True, unit_ids={"missing-a"})
        self.assertEqual([row["unit_id"] for row in selected], ["missing-a"])


class ReviewBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_calls_and_failed_tokens_are_bounded_and_preserved(self):
        class Gateway:
            calls = 0
            def start_call_capture(self):
                return None
            def finish_call_capture(self, token):
                return [{"status": "error", "total_tokens": 12}]
            async def generate_json(self, *args):
                self.calls += 1
                raise ValueError("provider output failed")
            async def close(self):
                pass
        for max_calls, max_tokens in [(1, 0), (5, 10)]:
            with tempfile.TemporaryDirectory() as folder:
                output = Path(folder)
                write_jsonl(output / "source_unit_candidates.jsonl", [unit("a"), unit("b"), unit("c")])
                write_jsonl(output / "windows.full.jsonl", [{"window_id": "w", "source_span": "你好\n"}])
                args = SimpleNamespace(output=output, unit_ids=None, revisit_review_gaps=False,
                    review_model_max_tokens=100, max_review_units=3, review_batch_size=2,
                    max_calls=max_calls, max_total_tokens=max_tokens)
                gateway = Gateway()
                with patch("scripts.run_style_reaction_pipeline_v2.gateway_for", return_value=gateway):
                    await review(args)
                self.assertEqual(gateway.calls, 1)
                self.assertEqual(len(read_jsonl(output / "review_call_records.jsonl")), 1)
                ledger = read_jsonl(output / "review_batch_executions.jsonl")[-1]
                self.assertEqual(ledger["tokens"], 12)
                self.assertEqual(len(read_jsonl(output / "review_unit_status.jsonl")), 2)

    async def test_unit_limit_does_not_round_up_to_batch_size(self):
        class Gateway:
            def start_call_capture(self):
                return None
            def finish_call_capture(self, token):
                return [{"total_tokens": 1}]
            async def generate_json(self, *args):
                raise ValueError("failure")
            async def close(self):
                pass
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            write_jsonl(output / "source_unit_candidates.jsonl", [unit("a"), unit("b"), unit("c")])
            write_jsonl(output / "windows.full.jsonl", [{"window_id": "w", "source_span": "你好\n"}])
            args = SimpleNamespace(output=output, unit_ids=None, revisit_review_gaps=False,
                review_model_max_tokens=100, max_review_units=1, review_batch_size=3,
                max_calls=5, max_total_tokens=0)
            with patch("scripts.run_style_reaction_pipeline_v2.gateway_for", return_value=Gateway()):
                await review(args)
            self.assertEqual(len(read_jsonl(output / "review_unit_status.jsonl")), 1)


if __name__ == "__main__":
    unittest.main()
