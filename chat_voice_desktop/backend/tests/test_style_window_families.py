from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest

from scripts.resume_style_window_families import resume_families
from scripts.run_style_reaction_pipeline_v2 import append_jsonl, write_jsonl


class WindowFamilyTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_children_are_processed_and_other_roots_are_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            roots = [{"window_id": identity, "parent_window_id": None} for identity in ("root", "other")]
            write_jsonl(output / "windows.full.jsonl", roots)
            write_jsonl(output / "window_status.jsonl", [
                {"window_id": identity, "status": "pending"} for identity in ("root", "other")])
            selected = []
            async def fake_extract(args):
                selected.append(args.window_ids)
                append_jsonl(output / "extract_call_records.jsonl", [{"total_tokens": 10}])
                if len(selected) == 1:
                    write_jsonl(output / "windows.derived.jsonl", [{"window_id": "child", "parent_window_id": "root"}])
                    states = [("root", "awaiting_children"), ("child", "pending"), ("other", "pending")]
                else:
                    states = [("root", "success"), ("child", "success"), ("other", "pending")]
                write_jsonl(output / "window_status.jsonl", [{"window_id": identity, "status": state} for identity, state in states])
            with patch("scripts.resume_style_window_families.extract", side_effect=fake_extract):
                report = await resume_families(output, ["root"], 2, 100)
            self.assertEqual(selected, [["root"], ["child"]])
            self.assertEqual(report["calls"], 2)
            self.assertEqual(report["tokens"], 20)
            self.assertEqual(report["unfinished"], {})

    async def test_failed_calls_stop_at_budget_and_remain_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            write_jsonl(output / "windows.full.jsonl", [{"window_id": "root", "parent_window_id": None}])
            write_jsonl(output / "window_status.jsonl", [{"window_id": "root", "status": "pending"}])
            async def fake_extract(args):
                append_jsonl(output / "extract_call_records.jsonl", [{"total_tokens": 10, "status": "failed"}])
                write_jsonl(output / "window_status.jsonl", [{"window_id": "root", "status": "failed"}])
            with patch("scripts.resume_style_window_families.extract", side_effect=fake_extract) as mocked:
                report = await resume_families(output, ["root"], 1, 5)
            self.assertEqual(mocked.call_count, 1)
            self.assertEqual(report["unfinished"], {"root": "failed"})
