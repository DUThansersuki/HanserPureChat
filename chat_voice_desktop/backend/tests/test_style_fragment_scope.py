from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from scripts.run_style_reaction_pipeline_v2 import (
    fragment_within_reaction, project, read_jsonl, review_gap_reasons,
    select_review_units, write_jsonl,
)


class FragmentScopeTests(unittest.TestCase):
    def setUp(self):
        self.unit = {"unit_id": "u", "source_id": "doc", "window_id": "w",
            "reaction_source_start_char": 0, "reaction_source_end_char": 3,
            "reaction_spans": [{"segment_id": "s0001", "start": 0, "end": 3}],
            "review_required": True, "source_response_turn": "提问？", "source_user_turn": None,
            "trigger_basis": "unknown", "attribution_status": "verified"}
        self.review = {"unit_id": "u", "decision": "approved", "attribution_status": "verified",
            "transfer_scope": "general", "payload_kind": "reviewed_fragment",
            "payload_class": "reaction_only",
            "behavior_ids": ["direct_natural_reply"], "source_specific_dependency": False,
            "reviewed_fragment_spans": [{"segment_id": "s0002", "start": 0, "end": 3}]}

    def test_adjacent_context_answer_is_not_the_units_reaction(self):
        self.assertFalse(fragment_within_reaction(self.unit, self.review))
        self.assertEqual(review_gap_reasons(self.review, self.unit), ["approved_fragment_outside_reaction"])
        self.assertEqual(select_review_units([self.unit], {"u": [self.review]}, {},
                                            revisit_gaps=True), [self.unit])

    def test_subspan_and_contiguous_union_are_allowed_but_gaps_are_not(self):
        unit = {"reaction_spans": [{"segment_id": "s", "start": 0, "end": 3},
                                    {"segment_id": "s", "start": 3, "end": 6}]}
        review = {"reviewed_fragment_spans": [{"segment_id": "s", "start": 1, "end": 5}]}
        self.assertTrue(fragment_within_reaction(unit, review))
        unit["reaction_spans"][1]["start"] = 4
        self.assertFalse(fragment_within_reaction(unit, review))

    def test_historical_approval_outside_reaction_cannot_project_a_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            write_jsonl(output / "source_unit_candidates.jsonl", [self.unit])
            write_jsonl(output / "semantic_reviews.jsonl", [self.review])
            write_jsonl(output / "windows.full.jsonl", [{"window_id": "w", "source_span": "提问？\n回答。"}])
            project(SimpleNamespace(output=output))
            row = read_jsonl(output / "source_reaction_units.reviewed.jsonl")[0]
            self.assertFalse(row["runtime_eligible"])
            self.assertIsNone(row["runtime_reaction_fragment"])
            self.assertEqual(row["projection_block_reason"], "fragment_outside_reaction")
