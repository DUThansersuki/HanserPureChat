from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.prepare_style_reaction_resume import prepare
from scripts.run_style_reaction_pipeline_v2 import read_jsonl, write_jsonl
from scripts.style_reaction_pipeline import (
    ATTRIBUTION_POLICY_VERSION, EXTRACTION_SCHEMA_VERSION, PIPELINE_VERSION,
    REVIEW_SCHEMA_VERSION, segment_transport_batch,
)


class ResumeLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.paths = {name: root / name for name in ["frozen", "compatible", "reviewed", "legacy", "output"]}
        for name, path in self.paths.items():
            if name != "output":
                path.mkdir()
        base = {"window_id": "base", "source_id": "DOC-1", "source_span": "你好\n再见\n", "source_start_char": 0, "source_end_char": 6, "parent_window_id": None}
        second = {**base, "window_id": "second", "source_id": "DOC-2"}
        child = {**base, "window_id": "child", "source_span": "你好\n", "source_end_char": 3, "parent_window_id": "base"}
        write_jsonl(self.paths["frozen"] / "windows.frozen.jsonl", [base, second])
        for name in ["split_manifest.json", "window_manifest.json"]:
            (self.paths["frozen"] / name).write_text("{}", encoding="utf-8")
        write_jsonl(self.paths["compatible"] / "windows.replay.jsonl", [base])
        write_jsonl(self.paths["compatible"] / "windows.derived.jsonl", [child])
        write_jsonl(self.paths["compatible"] / "window_status.jsonl", [{"window_id": "base", "status": "success"}, {"window_id": "child", "status": "success"}])
        segment = segment_transport_batch(child["source_span"])[0]
        self.unit = {
            "unit_id": "unit", "window_id": "child", "source_id": "DOC-1",
            "reaction_source_start_char": 0, "reaction_source_end_char": 3,
            "source_response_turn": "你好\n", "reaction_spans": [{"segment_id": segment["segment_id"], "start": 0, "end": 3}],
            "pipeline_version": PIPELINE_VERSION, "extraction_schema_version": EXTRACTION_SCHEMA_VERSION,
            "attribution_policy_version": ATTRIBUTION_POLICY_VERSION,
        }
        self.save_unit()
        write_jsonl(self.paths["reviewed"] / "semantic_reviews.jsonl", [{"unit_id": "unit", "review_schema_version": REVIEW_SCHEMA_VERSION, "decision": "pending"}])
        for origin, names in [
            ("compatible", ["extract_raw.jsonl", "extract_call_records.jsonl"]),
            ("reviewed", ["review_call_records.jsonl", "review_evidence_packets.jsonl", "review_unit_status.jsonl"]),
        ]:
            for name in names:
                write_jsonl(self.paths[origin] / name, [])
        write_jsonl(self.paths["legacy"] / "windows.full.jsonl", [base, second])
        write_jsonl(self.paths["legacy"] / "window_status.jsonl", [{"window_id": "base", "status": "pending"}, {"window_id": "second", "status": "inflight"}])
        write_jsonl(self.paths["legacy"] / "source_unit_candidates.jsonl", [])

    def save_unit(self):
        write_jsonl(self.paths["compatible"] / "source_unit_candidates.jsonl", [self.unit])

    def test_reuses_compatible_work_and_blocks_legacy_duplicate_calls(self):
        with patch("scripts.run_style_reaction_pipeline_v2.gateway_for", side_effect=AssertionError("provider must not run")):
            result = prepare(**self.paths, run_id="test")
        self.assertEqual(result["reused_basic_windows"], 1)
        self.assertEqual(result["reused_derived_windows"], 1)
        self.assertEqual(result["legacy_basic_windows_awaiting_migration"], 1)
        self.assertEqual(result["provider_calls_this_preparation"], 0)
        states = {row["window_id"]: row["status"] for row in read_jsonl(self.paths["output"] / "window_status.jsonl")}
        self.assertEqual(states, {"base": "success", "child": "success", "second": "awaiting_contract_migration"})
        progress = json.loads((self.paths["output"] / "extract_progress.json").read_text(encoding="utf-8"))
        self.assertFalse(progress["full_scan_complete"])

    def test_mismatched_frozen_span_and_contract_are_rejected_before_output_creation(self):
        self.unit["extraction_schema_version"] = "legacy"
        self.save_unit()
        with self.assertRaisesRegex(ValueError, "incompatible unit"):
            prepare(**self.paths, run_id="test")
        self.assertFalse(self.paths["output"].exists())
        self.unit["extraction_schema_version"] = EXTRACTION_SCHEMA_VERSION
        self.unit["source_response_turn"] = "编造的反应"
        self.save_unit()
        with self.assertRaisesRegex(ValueError, "does not match"):
            prepare(**self.paths, run_id="test")
        self.assertFalse(self.paths["output"].exists())


if __name__ == "__main__":
    unittest.main()
