from __future__ import annotations

import unittest

from scripts.machine_review_style_candidate import build_messages, parse_decision


class MachineReviewStyleCandidateTests(unittest.TestCase):
    def test_variable_pair_is_last_and_shared_prefix_is_stable(self) -> None:
        first = build_messages("问题一", "回答一")
        second = build_messages("问题二", "回答二")

        self.assertEqual(first[:-1], second[:-1])
        self.assertEqual(first[-1]["role"], "user")
        self.assertIn("prompt:\n问题一", first[-1]["content"])
        self.assertTrue(first[-1]["content"].endswith("response:\n回答一"))
        self.assertNotIn("问题一", "".join(item["content"] for item in first[:-1]))
        self.assertTrue(
            all(
                item["content"] in {"A", "R"}
                for item in first
                if item["role"] == "assistant"
            )
        )

    def test_decision_parser_never_normalizes_or_repairs_output(self) -> None:
        self.assertEqual(parse_decision("A"), "A")
        self.assertEqual(parse_decision("R"), "R")
        for invalid in ("", " A", "A ", "A\n", ": A", '"A"', '{"decision":"A"}'):
            self.assertIsNone(parse_decision(invalid))


if __name__ == "__main__":
    unittest.main()
