import json
import unittest
from scripts.local_jev_coarse_labels import task_specs,model_state,route_user_label

class CoarseLabelTests(unittest.TestCase):
    def test_exactly_two_axes_with_profanity_binary_and_thirteen_interactions(self):
        specs=task_specs()
        self.assertEqual(set(specs),{'profanity','interaction'})
        self.assertEqual(set(specs['profanity']['criteria']),{'no','yes'})
        self.assertEqual(len(specs['interaction']['criteria']),13)
        self.assertIn('unclear',specs['interaction']['criteria'])

    def test_neighbors_and_old_labels_cannot_become_target_or_q(self):
        row={'text':'你好。','source_context_before':'邻居卧槽','source_context_after':'邻居安慰',
             'prior_review_decision':'approved','scene_candidate':'greeting'}
        self.assertEqual(json.loads(model_state(row,'profanity'))['target_reply'],'你好。')
        state=json.loads(model_state(row,'interaction'))
        self.assertEqual(state['source_q'],'')
        self.assertNotIn('scene_candidate',state)
        self.assertNotIn('source_context_before',state)

    def test_user_swore_does_not_grant_profanity_permission(self):
        route=route_user_label('practical_help')
        self.assertIn('practical_help',route['response_modes'])
        self.assertFalse(route['profanity_allowed'])
        self.assertFalse(route['production_injection'])

if __name__=='__main__':unittest.main()
