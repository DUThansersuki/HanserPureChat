from __future__ import annotations

import unittest

from scripts.benchmark_responders import BenchmarkCase, deterministic_metrics


class ResponderBenchmarkTests(unittest.TestCase):
    def test_deterministic_metrics_flag_assistantese_and_punctuation(self) -> None:
        case = BenchmarkCase(
            id="test",
            message="晚上好",
            response_mode="casual",
            target_length="short",
        )
        metrics = deterministic_metrics(
            case,
            "当然可以，以下是回答。",
            "当然可以 以下是回答",
        )
        self.assertEqual(metrics["punctuation_compliance"], 0.0)
        self.assertEqual(metrics["assistantese"], 1.0)
        self.assertEqual(metrics["length_compliance"], 1.0)

    def test_unknown_fact_flags_invented_first_person_experience(self) -> None:
        case = BenchmarkCase(
            id="unknown",
            message="你小时候紧张吗",
            response_mode="factual",
            target_length="short",
        )
        metrics = deterministic_metrics(
            case,
            "我小时候第一次参加比赛时觉得很紧张",
            "我小时候第一次参加比赛时觉得很紧张",
        )
        self.assertEqual(metrics["unsupported_first_person"], 1.0)


if __name__ == "__main__":
    unittest.main()
