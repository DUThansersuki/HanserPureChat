from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from hanser_agent.persona.settings import load_effective_settings


CANDIDATE_DIR = (
    Path(__file__).resolve().parents[1]
    / "hanser_agent"
    / "prompts"
    / "persona"
    / "candidates"
    / "hanser-persona-v2-candidate"
)


class PersonaSettingsTests(unittest.TestCase):
    def test_candidate_defaults_and_override_trace_are_explicit(self) -> None:
        settings = load_effective_settings(
            CANDIDATE_DIR / "expression_policy.yaml",
            CANDIDATE_DIR / "product_overrides.yaml",
        )

        self.assertEqual(settings.profile_id, "balanced_candidate")
        self.assertEqual(settings.cutesy_bias, 0.10)
        self.assertEqual(settings.innuendo_level, 1)
        self.assertEqual(settings.trace["cutesy_bias"].source, "product.reduce_cutesy.v1")
        self.assertIn("candidate engineering default", settings.trace["warmth"].reason)

    def test_out_of_range_override_fails_closed(self) -> None:
        data = yaml.safe_load(
            (CANDIDATE_DIR / "product_overrides.yaml").read_text(encoding="utf-8")
        )
        data["overrides"][1]["parameter_effects"]["cutesy_bias"]["value"] = 0.9

        with tempfile.TemporaryDirectory() as tmp:
            override_path = Path(tmp) / "overrides.yaml"
            override_path.write_text(
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "above"):
                load_effective_settings(
                    CANDIDATE_DIR / "expression_policy.yaml",
                    override_path,
                )

    def test_two_active_overrides_cannot_silently_replace_each_other(self) -> None:
        data = yaml.safe_load(
            (CANDIDATE_DIR / "product_overrides.yaml").read_text(encoding="utf-8")
        )
        duplicate = dict(data["overrides"][1])
        duplicate["override_id"] = "product.conflicting_cutesy.v1"
        duplicate["parameter_effects"] = {
            "cutesy_bias": {"value": 0.2, "reason": "deliberate conflict fixture"}
        }
        data["overrides"].append(duplicate)

        with tempfile.TemporaryDirectory() as tmp:
            override_path = Path(tmp) / "overrides.yaml"
            override_path.write_text(
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "resolve the conflict explicitly"):
                load_effective_settings(
                    CANDIDATE_DIR / "expression_policy.yaml",
                    override_path,
                )


if __name__ == "__main__":
    unittest.main()
