import json
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from scripts.local_jev_expression_scene import expression_candidate, model_state, tasks, prepare_local


class ExpressionSceneBoundaryTests(unittest.TestCase):
    def test_previous_verdict_and_labels_do_not_enter_model_input(self):
        row = {"text": "好的。", "prior_review_decision": "approved", "prior_runtime_eligible": True,
               "prior_extraction_behavior_ids": ["light_contextual_tease"],
               "source_context_before": "之前的原文", "source_context_after": "之后的原文",
               "explicit_source_trigger": "原文提问"}
        expression = json.loads(model_state(row, "expression"))
        self.assertEqual(expression, {"target_reply": "好的。"})
        scene = json.loads(model_state(row, "scene"))
        self.assertNotIn("prior_review_decision", scene)
        self.assertNotIn("prior_extraction_behavior_ids", scene)

    def test_none_is_a_real_option_and_confidence_does_not_control_filter(self):
        self.assertIn("none", tasks()["expression"]["criteria"])
        self.assertNotIn("none", tasks()["expression_without_none"]["criteria"])
        row = {"text": "你好", "prior_review_decision": "rejected", "prior_runtime_eligible": False}
        plain = expression_candidate(row, {"expression": {"label": "none", "scores": {"none": .99}},
                                            "scene": {"label": "greeting"}})
        strong = expression_candidate(row, {"expression": {"label": "profanity", "scores": {"profanity": .21}},
                                             "scene": {"label": "casual_chat"}})
        self.assertFalse(plain["strong_feature_candidate"])
        self.assertTrue(strong["strong_feature_candidate"])
        self.assertEqual(strong["prior_review_decision"], "rejected")
        self.assertFalse(strong["semantic_approval_transferred"])
        self.assertFalse(strong["production_eligible_from_classifier"])

    def test_fresh_slices_prepare_without_legacy_extraction_and_without_approval(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);slices=root/'slices';slices.mkdir();output=root/'run'
            row={'unit_id':'LCS-new','source_id':'DOC-new','text':'好的。',
                 'explicit_source_trigger':'','reaction_source_start_char':0,'risk_flags':[],
                 'source_context_before':'','source_context_after':''}
            (slices/'candidates.jsonl').write_text(json.dumps(row,ensure_ascii=False)+'\n',encoding='utf-8')
            with patch('scripts.local_jev_expression_scene.all_windows',side_effect=AssertionError('Legacy input used')):
                prepare_local(slices,output,1)
            prepared=json.loads((output/'inputs.jsonl').read_text(encoding='utf-8'))
            self.assertEqual(prepared['unit_id'],'LCS-new')
            self.assertEqual(prepared['prior_review_decision'],'unreviewed')
            self.assertFalse(prepared['prior_runtime_eligible'])
            manifest=json.loads((output/'input_manifest.json').read_text(encoding='utf-8'))
            self.assertFalse(manifest['legacy_extraction_used'])
            self.assertIn('behavior',json.loads((output/'tasks.frozen.json').read_text(encoding='utf-8')))


if __name__ == "__main__":
    unittest.main()
