import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.inspect_style_payloads import Findings, run
from scripts.run_style_reaction_pipeline_v2 import read_jsonl, write_jsonl


class Gateway:
    def __init__(self, duplicate=False):
        self.duplicate = duplicate
        self.calls = 0
        self.closed = False

    def start_call_capture(self):
        return None

    def finish_call_capture(self, capture):
        return [{"total_tokens": 123}]

    async def generate_json(self, role, messages, schema):
        self.calls += 1
        units = json.loads(messages[-1].content)["units"]
        assert all("stratum" not in unit for unit in units)
        rows = [{"unit_id": unit["unit_id"], "reaction_ok": "yes",
                 "trigger_relation_ok": "unknown_not_required", "transfer_ok": "yes",
                 "speaker_conflict": False, "issues": [], "basis": "原话与来源一致",
                 "evidence_quotes": ["原话", "不存在的证据"]} for unit in units]
        if self.duplicate:
            rows.append(rows[0])
        return Findings.model_validate({"findings": rows})

    async def close(self):
        self.closed = True


class PayloadInspectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_freeze_is_read_only_and_resume_does_not_repeat_completed_calls(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            (output / "prompt.frozen.txt").write_text("frozen inspection protocol", encoding="utf-8")
            inputs = [{"unit_id": "one", "stratum": "new_eligible", "source_text": "这里是原话。"}]
            write_jsonl(output / "inputs.frozen.jsonl", inputs)
            original = (output / "inputs.frozen.jsonl").read_bytes()
            gateway = Gateway()
            with patch("scripts.inspect_style_payloads.gateway_for", return_value=gateway):
                report = await run(output)
                await run(output)
            self.assertEqual(gateway.calls, 1)
            self.assertTrue(gateway.closed)
            self.assertEqual(report["tokens"], 123)
            self.assertEqual(report["invalid_quote_units"], ["one"])
            self.assertEqual(read_jsonl(output / "findings.jsonl")[0]["invalid_evidence_quotes"], ["不存在的证据"])
            self.assertEqual((output / "inputs.frozen.jsonl").read_bytes(), original)

    async def test_duplicate_provider_findings_fail_with_cost_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            (output / "prompt.frozen.txt").write_text("frozen inspection protocol", encoding="utf-8")
            write_jsonl(output / "inputs.frozen.jsonl", [{"unit_id": "one", "stratum": "new_eligible", "source_text": "原话"}])
            with patch("scripts.inspect_style_payloads.gateway_for", return_value=Gateway(duplicate=True)):
                report = await run(output)
            self.assertEqual(report["units"], 0)
            self.assertEqual(report["tokens"], 123)
            self.assertEqual(read_jsonl(output / "failures.jsonl")[0]["message"], "inspection_returned_unit_ids_mismatch")


if __name__ == "__main__":
    unittest.main()
