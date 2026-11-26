import unittest

from scripts.local_gpu_a_labels import canonical_result
from scripts.local_jev_a_labels import LABELS


class LocalGpuLabelsTests(unittest.TestCase):
    def test_letter_keys_and_rounded_probabilities_map_to_existing_vocabulary(self):
        mapping = {chr(65+i): key for i, key in enumerate(LABELS)}
        scores = {key: 0.0 for key in mapping}
        scores.update({'A': .3333, 'B': .3333, 'E': .3333})
        result = canonical_result({'model': 'laya-rl-agent', 'answers': {'behavior': {
            'type': 'choice', 'choice': 'E', 'probabilities': scores,
            'confidence': .2857}}}, mapping, LABELS)
        self.assertEqual(result['top1_label'], 'direct_natural_reply')
        self.assertEqual(set(result['label_scores']), set(LABELS))
        self.assertEqual(result['model'], 'convaiinnovations/laya-multilingual')

    def test_missing_candidate_is_not_published_as_a_label(self):
        mapping = {chr(65+i): key for i, key in enumerate(LABELS)}
        with self.assertRaises(ValueError):
            canonical_result({'answers': {'behavior': {
                'type': 'choice', 'choice': 'A', 'probabilities': {'A': 1.0}}}}, mapping, LABELS)


if __name__ == '__main__':
    unittest.main()
