from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from scripts.run_style_reaction_pipeline_v2 import project, read_jsonl, review_gap_reasons, write_jsonl


class PayloadFactsGateTests(unittest.TestCase):
    def project_one(self, folder, kind, classification):
        output = Path(folder)
        unit = {"unit_id": "u", "source_id": "doc", "window_id": "w",
                "reaction_source_start_char": 0, "reaction_source_end_char": 4,
                "reaction_spans": [{"segment_id": "s0001", "start": 0, "end": 4}],
                "source_response_turn": "我有房。", "source_user_turn": None,
                "trigger_basis": "self_initiated", "attribution_status": "verified",
                "reaction_mechanism": "描述房产", "review_required": True}
        review = {"unit_id": "u", "decision": "approved", "attribution_status": "verified",
                  "transfer_scope": "general", "source_specific_dependency": False,
                  "behavior_ids": ["direct_natural_reply"], "payload_kind": kind,
                  "reviewed_fragment_spans": unit["reaction_spans"]}
        if classification is not None:
            review["payload_class"] = classification
        write_jsonl(output / "source_unit_candidates.jsonl", [unit])
        write_jsonl(output / "semantic_reviews.jsonl", [review])
        write_jsonl(output / "windows.full.jsonl", [{"window_id": "w", "source_span": "我有房。"}])
        project(SimpleNamespace(output=output))
        return read_jsonl(output / "source_reaction_units.reviewed.jsonl")[0], review, unit

    def test_legacy_approval_and_factual_classes_do_not_project(self):
        for kind in ("reviewed_fragment", "reaction_pattern"):
            for classification in (None, "world_fact", "past_or_current_autobiography", "fiction_or_quote"):
                with self.subTest(kind=kind, classification=classification), tempfile.TemporaryDirectory() as folder:
                    row, review, unit = self.project_one(folder, kind, classification)
                    self.assertFalse(row["runtime_eligible"])
                    self.assertEqual(row["runtime_payload_kind"], "none")
                    self.assertEqual(row["projection_block_reason"], "payload_facts_not_cleared")
                    self.assertIn("approved_missing_safe_payload_class", review_gap_reasons(review, unit))

    def test_explicit_safe_class_is_required_for_both_payload_routes(self):
        # This validates the projection contract; semantic correctness is a separate source review.
        for kind in ("reviewed_fragment", "reaction_pattern"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                row, _, _ = self.project_one(folder, kind, "reaction_only")
                self.assertTrue(row["runtime_eligible"])
                self.assertEqual(row["runtime_payload_kind"], kind)


if __name__ == "__main__":
    unittest.main()
