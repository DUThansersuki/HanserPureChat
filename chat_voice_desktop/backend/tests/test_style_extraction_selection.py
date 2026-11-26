from types import SimpleNamespace
import unittest

from scripts.run_style_reaction_pipeline_v2 import select_extraction_windows


class ExtractionSelectionTests(unittest.TestCase):
    def setUp(self):
        self.windows = {"base": {}, "child": {"parent_window_id": "base"},
                        "done": {}, "failed": {}}
        self.statuses = [{"window_id": name, "status": state, "attempts": 1}
                         for name, state in [("base", "pending"), ("child", "pending"),
                                             ("done", "success"), ("failed", "failed")]]
        self.args = SimpleNamespace(window_ids=None, window_id=None, retry_failures=False,
                                    max_attempts=2, derived_first=True, max_calls=10)

    def test_explicit_child_does_not_start_unrelated_base(self):
        self.args.window_ids = ["child", "done"]
        self.assertEqual([row["window_id"] for row in select_extraction_windows(
            self.windows, self.statuses, self.args)], ["child"])

    def test_unknown_selection_fails_before_provider_or_ledger_mutation(self):
        self.args.window_id = "missing"
        with self.assertRaisesRegex(ValueError, "unknown_extraction_window_ids"):
            select_extraction_windows(self.windows, self.statuses, self.args)

    def test_retry_and_call_limit_still_apply_to_selection(self):
        self.args.window_ids = ["failed", "child"]
        self.args.retry_failures = True
        self.args.max_calls = 1
        self.assertEqual([row["window_id"] for row in select_extraction_windows(
            self.windows, self.statuses, self.args)], ["child"])
        self.args.window_ids = ["failed"]
        self.assertEqual(len(select_extraction_windows(self.windows, self.statuses, self.args)), 1)
        self.args.max_attempts = 1
        self.assertEqual(select_extraction_windows(self.windows, self.statuses, self.args), [])
