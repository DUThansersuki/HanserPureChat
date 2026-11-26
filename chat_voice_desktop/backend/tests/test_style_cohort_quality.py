import json
from pathlib import Path
import tempfile
import unittest

from scripts.check_style_cohort_quality import audit
from scripts.run_style_reaction_pipeline_v2 import write_jsonl
from scripts.style_reaction_pipeline import ReactionExtractionResult, materialize_reaction_events, segment_transport_batch


class CohortQualityTests(unittest.TestCase):
    def fixture(self, folder):
        root = Path(folder)
        resume, frozen, cohort = [root / name for name in ("resume", "frozen", "cohort")]
        for path in (resume, frozen, cohort):
            path.mkdir()
        source = "Hanser：原话。\n其他句。"
        window = {"window_id": "w", "source_id": "doc", "document_id": 1,
            "filename": "source.docx", "source_span": source, "parent_window_id": None,
            "source_start_char": 0, "source_end_char": len(source)}
        segments = segment_transport_batch(source)
        result = ReactionExtractionResult.model_validate({"coverage_status": "complete", "events": [{
            "reaction_spans": [{"segment_id": segments[0]["segment_id"], "start": 0, "end": segments[0]["length"]}],
            "trigger_spans": [], "trigger_basis": "self_initiated", "speaker_candidate": "hanser",
            "attribution_status_initial": "assumed", "confidence": "high"}]})
        units, errors = materialize_reaction_events(window, segments, result)
        self.assertEqual(errors, [])
        write_jsonl(frozen / "windows.frozen.jsonl", [window])
        write_jsonl(frozen / "sources.frozen.jsonl", [{"source_id": "doc", "split": "corpus_build"}])
        write_jsonl(resume / "windows.full.jsonl", [window])
        write_jsonl(resume / "window_status.jsonl", [{"window_id": "w", "status": "success"}])
        write_jsonl(resume / "source_unit_candidates.jsonl", units)
        (resume / "resume_manifest.json").write_text(json.dumps({"frozen_run": str(frozen)}))
        (cohort / "selection.json").write_text(json.dumps({"window_ids": ["w"]}))
        return resume, frozen, cohort, units[0]

    def test_exact_build_source_and_complete_family_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            resume, frozen, cohort, unit = self.fixture(folder)
            report = audit(resume, cohort, Path(folder) / "result")
            self.assertEqual(report["hard_gate_errors"], [])
            self.assertEqual(report["cohort_complete_families"], 1)
            self.assertEqual(report["cohort_review_decisions"], {"unreviewed": 1})

    def test_holdout_and_adjacent_fragment_are_reported_as_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            resume, frozen, cohort, unit = self.fixture(folder)
            write_jsonl(frozen / "sources.frozen.jsonl", [{"source_id": "doc", "split": "independent_evaluation_holdout"}])
            review = {"decision": "approved", "attribution_status": "verified", "transfer_scope": "general",
                "payload_class": "reaction_only",
                "source_specific_dependency": False, "reviewed_fragment_spans": [{"segment_id": "s9999", "start": 0, "end": 3}]}
            write_jsonl(resume / "source_reaction_units.reviewed.jsonl", [{**unit,
                "runtime_eligible": True, "runtime_payload_kind": "reviewed_fragment",
                "a_projection": {"behavior_ids": ["direct_natural_reply"]}, "semantic_review": review}])
            report = audit(resume, cohort, Path(folder) / "result")
            self.assertEqual({row["kind"] for row in report["hard_gate_errors"]},
                             {"holdout_or_excluded_source", "fragment_boundary"})
