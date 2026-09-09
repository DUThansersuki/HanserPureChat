from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from hanser_agent.persona import PersonaCompiler, build_guidance, build_turn_signals


CANDIDATE_DIR = (
    Path(__file__).resolve().parents[1]
    / "hanser_agent"
    / "prompts"
    / "persona"
    / "candidates"
    / "hanser-persona-v2-candidate"
)


class PersonaV2CompilerTests(unittest.TestCase):
    def test_candidate_compile_is_deterministic_and_constraint_oriented(self) -> None:
        compiler = PersonaCompiler(CANDIDATE_DIR)
        signals = build_turn_signals(
            "我真的很难受 先别开玩笑 也别给建议",
            current_message_ref="message:current",
        )
        decision = build_guidance(
            signals,
            {},
            None,
            compiler.effective_settings,
            response_mode="emotional",
        )
        first = compiler.compile(
            "emotional",
            turn_signals=signals,
            behavior_decision=decision,
        )
        second = compiler.compile(
            "emotional",
            turn_signals=signals,
            behavior_decision=decision,
        )

        self.assertEqual(first.render_sha256, second.render_sha256)
        self.assertEqual(first.source_sha256, second.source_sha256)
        self.assertEqual(
            first.package_id,
            "hanser-persona-v2-production-20260910",
        )
        self.assertIn("硬要求必须遵守", first.behavior)
        self.assertIn("软倾向可自然融合", first.behavior)
        self.assertIn("user.no_unsolicited_advice", first.behavior)
        self.assertNotIn("每十轮", first.render())

    def test_manifest_hash_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "candidate"
            shutil.copytree(CANDIDATE_DIR, copied)
            (copied / "core.md").write_text("tampered", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "manifest hash mismatch"):
                PersonaCompiler(copied)


if __name__ == "__main__":
    unittest.main()
