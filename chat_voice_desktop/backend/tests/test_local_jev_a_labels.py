import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.local_jev_a_labels import LABELS, classify, parse_label, request_json, summarize


def response():
    scores = {key: 0.0 for key in LABELS}
    scores['self_deprecation'] = .7
    scores['direct_natural_reply'] = .3
    return {'model': 'jevos-v4', 'answers': {'behavior': {
        'type': 'choice', 'choice': 'self_deprecation', 'probabilities': scores,
        'confidence': .6}}, 'usage': {'input_tokens': 10}}


class LocalJevLabelsTests(unittest.TestCase):
    def test_external_endpoint_is_refused_before_connection(self):
        with patch('http.client.HTTPConnection') as connection:
            for endpoint in ['https://api.deepseek.com', 'http://example.com',
                             'http://127.0.0.1@external.example', 'http://localhost']:
                with self.assertRaises(ValueError):
                    request_json(endpoint, '/health')
            connection.assert_not_called()

    def test_invalid_distributions_cannot_produce_a_label(self):
        for corruption in ['nonfinite', 'missing', 'wrong_choice']:
            data = copy.deepcopy(response())
            answer = data['answers']['behavior']
            if corruption == 'nonfinite':
                answer['probabilities']['self_deprecation'] = float('nan')
            elif corruption == 'missing':
                del answer['probabilities']['self_deprecation']
            else:
                answer['choice'] = 'direct_natural_reply'
            with self.assertRaises(ValueError):
                parse_label(data, LABELS)

    def test_resume_skips_completed_and_keeps_approval_separate(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            inputs = [{'unit_id': key, 'text': '我可真是个笨蛋', 'source_id': 'DOC-0001', 'filename': 'source.docx',
                       'cohort_200': True, 'prior_review_decision': 'unreviewed',
                       'prior_runtime_eligible': False,
                       'prior_extraction_behavior_ids': ['self_deprecation'],
                       'prior_risk_flags': []} for key in ['one', 'two']]
            (output/'inputs.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in inputs), encoding='utf-8')
            (output/'labels.frozen.json').write_text(json.dumps(LABELS), encoding='utf-8')
            (output/'instructions.frozen.txt').write_text('Classify reply', encoding='utf-8')
            prior = {'unit_id': 'one', **parse_label(response(), LABELS)}
            (output/'labels.jsonl').write_text(json.dumps(prior)+'\n', encoding='utf-8')
            with patch('scripts.local_jev_a_labels.request_json', side_effect=[
                    {'fingerprint': 'same'}, response()]) as request:
                classify(output, 'http://127.0.0.1:8017', None, 1)
                self.assertEqual(request.call_count, 2)
            summary = summarize(output)
            self.assertEqual(summary['labeled'], 2)
            views = [json.loads(line) for line in (output/'a_candidates.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertTrue(all(row['a_behavior_ids'] == ['self_deprecation'] for row in views))
            self.assertTrue(all(row['prior_review_decision'] == 'unreviewed' and
                                row['semantic_approval_transferred'] is False and
                                row['prior_runtime_eligible'] is False for row in views))


if __name__ == '__main__':
    unittest.main()
