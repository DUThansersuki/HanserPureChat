import unittest

from scripts.local_style_label_pools import build_pools, select_candidates


class LabelPoolRoutingTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {'unit_id':'plain', 'expression':{'label':'none'}, 'scene_candidate':'greeting',
             'expression_tags_candidate':[], 'prior_runtime_eligible':True},
            {'unit_id':'swear', 'expression':{'label':'profanity'}, 'scene_candidate':'casual_chat',
             'expression_tags_candidate':['profanity','strong_marker'], 'prior_runtime_eligible':True},
            {'unit_id':'unreviewed', 'expression':{'label':'profanity'}, 'scene_candidate':'greeting',
             'expression_tags_candidate':['profanity','strong_marker'], 'prior_runtime_eligible':False},
        ]
        self.pools = build_pools(self.rows)
        self.by_id = {r['unit_id']:r for r in self.rows}

    def test_special_request_only_uses_matching_eligible_pool(self):
        result = select_candidates(self.by_id,self.pools,requested_expression='profanity')
        self.assertEqual(result['unit_ids'],['swear'])
        self.assertFalse(result['production_injection'])
        self.assertTrue(result['needs_current_runtime_gates_and_approved_projection'])

    def test_scene_intersection_and_block_do_not_fall_back_to_plain(self):
        result = select_candidates(self.by_id,self.pools,requested_expression='profanity',requested_scene='greeting')
        self.assertEqual(result['unit_ids'],[])
        result = select_candidates(self.by_id,self.pools,requested_expression='profanity',blocked_expressions={'profanity'})
        self.assertEqual(result['empty_reason'],'expression_blocked')
        self.assertEqual(result['unit_ids'],[])

    def test_main_keeps_plain_text_and_pools_store_no_copies(self):
        self.assertIn('plain', select_candidates(self.by_id,self.pools)['unit_ids'])
        self.assertEqual(self.pools['expression']['profanity'],['swear','unreviewed'])
        self.assertTrue(all(isinstance(uid,str) for uid in self.pools['main']))
        self.assertEqual(select_candidates(self.by_id,self.pools,requested_expression='none')['unit_ids'],['plain'])


if __name__=='__main__':
    unittest.main()
