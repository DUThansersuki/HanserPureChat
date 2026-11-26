import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest

from scripts.run_style_reaction_pipeline_v2 import read_jsonl, write_jsonl


class LedgerWriteTests(unittest.TestCase):
    def test_failure_before_replace_keeps_committed_ledger(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "window_status.jsonl"
            write_jsonl(path, [{"window_id": "w", "status": "inflight"}])
            with patch.object(Path, "replace", side_effect=OSError("write interrupted")):
                with self.assertRaises(OSError):
                    write_jsonl(path, [{"window_id": "w", "status": "success"}])
            self.assertEqual(read_jsonl(path)[0]["status"], "inflight")
            write_jsonl(path, [{"window_id": "w", "status": "success"}])
            self.assertEqual(read_jsonl(path)[0]["status"], "success")

    def test_serialization_failure_does_not_truncate_prior_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.jsonl"
            write_jsonl(path, [{"status": "saved"}])
            with self.assertRaises(TypeError):
                write_jsonl(path, [{"not_json": object()}])
            self.assertEqual(read_jsonl(path), [{"status": "saved"}])
