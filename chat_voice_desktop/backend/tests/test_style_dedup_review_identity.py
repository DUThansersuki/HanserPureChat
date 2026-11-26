from pathlib import Path
import tempfile
import unittest

from scripts.run_style_reaction_pipeline_v2 import unique_units, write_jsonl


class DedupReviewIdentityTests(unittest.TestCase):
    def test_shorter_overlapping_extraction_does_not_replace_reviewed_evidence(self):
        for decision in ("approved", "pending", "rejected"):
            with self.subTest(decision=decision), tempfile.TemporaryDirectory() as folder:
                output = Path(folder)
                original = {"unit_id": "original", "source_id": "doc", "source_response_turn": "原审核全文",
                    "reaction_source_start_char": 10, "reaction_source_end_char": 30,
                    "source_lineage": [{"window_id": "old"}],
                    "reaction_spans": [{"segment_id": "s1", "start": 0, "end": 20}]}
                newer = {"unit_id": "new-short", "source_id": "doc", "source_response_turn": "子句",
                    "reaction_source_start_char": 12, "reaction_source_end_char": 20,
                    "source_lineage": [{"window_id": "new"}]}
                write_jsonl(output / "source_unit_candidates.jsonl", [original, newer])
                write_jsonl(output / "semantic_reviews.jsonl", [{"unit_id": "original", "decision": decision}])
                rows = unique_units(output)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["unit_id"], "original")
                self.assertEqual(rows[0]["source_response_turn"], original["source_response_turn"])
                self.assertEqual(rows[0]["reaction_spans"], original["reaction_spans"])
                self.assertIn("new-short", rows[0]["dedupe_merged_unit_ids"])

    def test_unreviewed_overlap_keeps_existing_shortest_span_policy(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            write_jsonl(output / "source_unit_candidates.jsonl", [
                {"unit_id": "long", "source_id": "doc", "reaction_source_start_char": 0,
                 "reaction_source_end_char": 20},
                {"unit_id": "short", "source_id": "doc", "reaction_source_start_char": 3,
                 "reaction_source_end_char": 10}])
            self.assertEqual(unique_units(output)[0]["unit_id"], "short")
