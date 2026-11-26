import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

from scripts.run_style_reaction_pipeline_v2 import extract_one
from scripts.style_reaction_pipeline import ReactionExtractionWireResult


class FailedWireAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_cursor_keeps_provider_wire_and_actual_cost(self):
        wire = ReactionExtractionWireResult.model_validate({
            "c": "partial", "k": 99, "e": [{"r": [[1, 0, 3]], "t": [],
                "b": "self_initiated", "s": "hanser", "a": "assumed", "q": "high"}]})
        gateway = Mock()
        gateway.start_call_capture.return_value = None
        gateway.generate_json = AsyncMock(return_value=wire)
        gateway.finish_call_capture.return_value = [{"total_tokens": 123}]
        result = await extract_one(gateway, {"window_id": "w", "source_id": "doc",
            "source_span": "好的。", "source_start_char": 0, "source_end_char": 3}, asyncio.Semaphore(1))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["message"], "unknown_wire_segment_id")
        self.assertEqual(result["provider_raw_wire"]["k"], 99)
        self.assertEqual(result["call_records"], [{"total_tokens": 123}])
        self.assertIsNone(result["result"])


if __name__ == "__main__":
    unittest.main()
