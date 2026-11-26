from pathlib import Path
import tempfile
import unittest

from scripts.restore_style_extraction_ledger import restore
from scripts.run_style_reaction_pipeline_v2 import read_jsonl, write_jsonl
from scripts.style_reaction_pipeline import EXTRACTION_SCHEMA_VERSION, segment_transport_batch


class LedgerRestoreTests(unittest.TestCase):
    def test_saved_no_unit_commits_without_new_provider_call_or_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            windows = [{"window_id": name, "source_id": "doc", "source_span": "原文",
                        "parent_window_id": None} for name in ("saved", "unknown")]
            write_jsonl(output / "windows.full.jsonl", windows)
            write_jsonl(output / "window_status.jsonl", [
                {"window_id": name, "status": "inflight", "attempts": 1}
                for name in ("saved", "unknown")])
            write_jsonl(output / "extract_raw.jsonl", [{"window_id": "saved",
                "extraction_schema_version": EXTRACTION_SCHEMA_VERSION,
                "result": {"events": [], "coverage_status": "complete", "no_unit_reason": "没有反应"}}])
            report = restore(output)
            self.assertEqual(report["provider_calls"], 0)
            self.assertEqual(report["restored_window_ids"], ["saved"])
            rows = {row["window_id"]: row for row in read_jsonl(output / "window_status.jsonl")}
            self.assertEqual(rows["saved"]["status"], "no_unit")
            self.assertEqual(rows["saved"]["attempts"], 1)
            self.assertEqual(rows["unknown"]["status"], "inflight")

    def test_saved_partial_recreates_missing_children_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            source = "Hanser：这是原文。\n" * 200
            window = {"window_id": "root", "source_id": "doc", "source_span": source,
                "source_start_char": 100, "source_end_char": 100 + len(source), "parent_window_id": None}
            write_jsonl(output / "windows.full.jsonl", [window])
            write_jsonl(output / "window_status.jsonl", [{"window_id": "root", "status": "inflight", "attempts": 1}])
            write_jsonl(output / "extract_raw.jsonl", [{"window_id": "root",
                "extraction_schema_version": EXTRACTION_SCHEMA_VERSION,
                "result": {"events": [], "coverage_status": "partial",
                    "continuation_from_segment_id": segment_transport_batch(source)[0]["segment_id"]}}])
            report = restore(output)
            self.assertGreater(report["new_children"], 1)
            rows = read_jsonl(output / "window_status.jsonl")
            self.assertEqual(rows[0]["status"], "awaiting_children")
            self.assertTrue(all(row["status"] == "pending" for row in rows[1:]))
            self.assertEqual(restore(output)["restored_window_ids"], [])
            self.assertEqual(len(read_jsonl(output / "window_status.jsonl")), len(rows))
