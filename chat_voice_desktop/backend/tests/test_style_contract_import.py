import json
from pathlib import Path
import tempfile
import unittest

from scripts.import_legacy_style_contract import recovery_children, stage_import, verify_review_identity
from scripts.run_style_reaction_pipeline_v2 import read_jsonl, write_jsonl


class ContractImportTests(unittest.TestCase):
    def test_inflight_recovery_uses_exact_frozen_source_children(self):
        source = "Hanser：这是原话。\n" * 180
        window = {"window_id": "root", "source_id": "doc", "document_id": 1,
                  "filename": "source.docx", "source_start_char": 100,
                  "source_end_char": 100 + len(source), "source_span": source,
                  "parent_window_id": None}
        children = recovery_children(window)
        self.assertGreater(len(children), 1)
        covered = set()
        for child in children:
            start, end = child["source_start_char"] - 100, child["source_end_char"] - 100
            self.assertEqual(child["source_span"], source[start:end])
            self.assertEqual(child["parent_window_id"], "root")
            self.assertEqual(child["derivation_reason"], "legacy_inflight_exact_source_recovery")
            covered.update(range(start, end))
        self.assertEqual(covered, set(range(len(source))))

    def test_current_review_may_not_move_to_an_overlapping_representative(self):
        original = {"unit_id": "reviewed", "source_id": "doc", "source_response_turn": "原话",
                    "reaction_source_start_char": 1, "reaction_source_end_char": 3}
        with self.assertRaisesRegex(ValueError, "orphans_current_review"):
            verify_review_identity([original], [{**original, "unit_id": "shorter"}],
                                   [{"unit_id": "reviewed", "decision": "approved"}])

    def test_same_id_requires_unchanged_reviewed_text_and_span(self):
        original = {"unit_id": "u", "source_id": "doc", "source_response_turn": "原话",
                    "reaction_source_start_char": 1, "reaction_source_end_char": 3}
        for field, value in [("source_response_turn", "改写"), ("reaction_source_start_char", 0)]:
            with self.assertRaisesRegex(ValueError, "changes_reviewed_evidence"):
                verify_review_identity([original], [{**original, field: value}], [{"unit_id": "u"}])
        verify_review_identity([original], [original], [{"unit_id": "u"}])

    def test_terminal_empty_family_imports_without_claiming_incomplete_family(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            resume, legacy, audit, stage = [root / name for name in ("resume", "legacy", "audit", "stage")]
            for path in (resume, legacy, audit):
                path.mkdir()
            source = "原文\n" * 600
            windows = [{"window_id": identity, "source_id": identity, "source_start_char": 0,
                        "source_end_char": len(source), "source_span": source, "parent_window_id": None}
                       for identity in ("done", "pending")]
            for path in (resume, legacy):
                write_jsonl(path / "windows.full.jsonl", windows)
            write_jsonl(resume / "window_status.jsonl", [
                {"window_id": identity, "status": "awaiting_contract_migration"}
                for identity in ("done", "pending")])
            write_jsonl(legacy / "window_status.jsonl", [
                {"window_id": "done", "status": "no_unit"},
                {"window_id": "pending", "status": "inflight"}])
            write_jsonl(audit / "window_compatibility.jsonl", [
                {"window_id": "done", "coverage_complete_in_legacy": True},
                {"window_id": "pending", "coverage_complete_in_legacy": False}])
            (resume / "resume_manifest.json").write_text(json.dumps({"legacy_run": str(legacy)}))
            report = stage_import(resume, audit, stage)
            self.assertEqual(report["imported_base_windows"], ["done"])
            self.assertFalse(report["applied"])
            staged = {row["window_id"]: row["status"] for row in read_jsonl(stage / "window_status.jsonl")}
            self.assertEqual(staged, {"done": "no_unit", "pending": "awaiting_contract_migration"})
            self.assertEqual(read_jsonl(resume / "window_status.jsonl")[0]["status"],
                             "awaiting_contract_migration")
            recovered_report = stage_import(resume, audit, root / "recovery", include_incomplete=True)
            self.assertEqual(recovered_report["inflight_recovered_from_frozen_source"], ["pending"])
            recovered_status = {row["window_id"]: row for row in read_jsonl(root / "recovery/window_status.jsonl")}
            self.assertEqual(recovered_status["pending"]["legacy_status"], "inflight")
            self.assertEqual(recovered_status["pending"]["status"], "awaiting_children")
