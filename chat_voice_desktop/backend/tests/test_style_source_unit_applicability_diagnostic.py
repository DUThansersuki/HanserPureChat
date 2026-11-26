import unittest

from scripts.diagnose_style_qa_retrieval import applicability_record, signals
from hanser_agent.persona.style_applicability import has_specific_behavior_applicability, style_applicability_exclusion_reason


class SourceUnitDiagnosticTests(unittest.TestCase):
    def test_unqualified_source_unit_cannot_take_legacy_a_bypass(self):
        unit = applicability_record({"semantic_review": {}})
        self.assertFalse(has_specific_behavior_applicability(unit))

    def test_reviewed_closing_requirement_is_shared_with_runtime_gate(self):
        unit = applicability_record({"semantic_review": {"applicability_requires_all": [
            {"field": "dialogue_function", "one_of": ["end"]}]}})
        self.assertTrue(has_specific_behavior_applicability(unit))
        self.assertIsNone(style_applicability_exclusion_reason(unit, signals({"dialogue_function": "end"}))[0])
        self.assertIsNotNone(style_applicability_exclusion_reason(unit, signals({"dialogue_function": "share"}))[0])


if __name__ == "__main__":
    unittest.main()
