from __future__ import annotations

import unittest

from scripts.audit_legacy_style_contract import migrate_unit, remap_slices
from scripts.style_reaction_pipeline import (
    ReactionExtractionResult, materialize_reaction_events, segment_transport_batch,
)


class LegacyContractTests(unittest.TestCase):
    def setUp(self):
        self.window = {"window_id": "w", "source_id": "DOC-1", "document_id": 1,
            "filename": "source.docx", "source_span": "弹幕：真的吗？\nHanser：没有啦。\n",
            "source_start_char": 100, "source_end_char": 122, "parent_window_id": None}

    def test_old_slice_can_cross_new_segment_boundaries_without_changing_text(self):
        segments = segment_transport_batch(self.window["source_span"])
        text = self.window["source_span"]
        refs = remap_slices([{"window_start": 0, "window_end": len(text), "text": text}], self.window, segments)
        self.assertGreater(len(refs), 1)
        values = {row["segment_id"]: row for row in segments}
        self.assertEqual("".join(values[row["segment_id"]]["text"][row["start"]:row["end"]]
                                  for row in refs), text)

    def test_reconstructed_contract_preserves_provenance_and_remains_ineligible(self):
        segments = segment_transport_batch(self.window["source_span"])
        reaction = next(row for row in segments if row["marker"] == "hanser_label")
        result = ReactionExtractionResult.model_validate({"events": [{
            "reaction_spans": [{"segment_id": reaction["segment_id"], "start": 0, "end": reaction["length"]}],
            "trigger_spans": [], "trigger_basis": "unknown", "speaker_candidate": "hanser",
            "attribution_status_initial": "assumed", "interaction_frame": "other", "interaction_target": "unknown",
            "behavior_ids": ["direct_natural_reply"], "reaction_mechanism": "直接回答", "event_relation": "回应",
            "context_needed": "none", "risk_flags": [], "confidence": "high", "uncertainty": "",
        }], "coverage_status": "complete"})
        rows, failures = materialize_reaction_events(self.window, segments, result)
        self.assertEqual(failures, [])
        original = {**rows[0], "extraction_schema_version": "legacy-v4", "pipeline_version": "legacy"}
        migrated = migrate_unit(original, self.window)
        self.assertEqual(migrated["source_response_turn"], original["source_response_turn"])
        self.assertEqual(migrated["unit_id"], original["unit_id"])
        self.assertEqual(migrated["contract_migration"]["original_extraction_schema_version"], "legacy-v4")
        self.assertFalse(migrated["runtime_eligible"])
        self.assertFalse(migrated["contract_migration"]["semantic_review_reused"])

    def test_source_drift_is_not_normalized_into_compatibility(self):
        segments = segment_transport_batch(self.window["source_span"])
        with self.assertRaisesRegex(ValueError, "source_text_mismatch"):
            remap_slices([{"window_start": 0, "window_end": 2, "text": "伪造"}], self.window, segments)


if __name__ == "__main__":
    unittest.main()
