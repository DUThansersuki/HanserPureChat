import unittest
from scripts.deepseek_a13_labels import LABELS,fixed_rule,messages_for,parse_number

class A13Tests(unittest.TestCase):
    def test_numeric_contract_rejects_wrappers_and_out_of_range(self):
        for n in range(1,14):self.assertEqual(parse_number(str(n)),n)
        for text in ['0','14','类别6','6。','{"label":6}','6\n解释']:
            with self.assertRaises(ValueError):parse_number(text)

    def test_fixed_rule_first_and_only_target_a_last(self):
        rule=fixed_rule();messages=messages_for(rule,'别吓我了。')
        self.assertEqual(messages[0].role,'system')
        self.assertEqual(messages[0].content,rule)
        self.assertEqual(messages[-1].role,'user')
        self.assertEqual(messages[-1].content,'别吓我了。')
        self.assertEqual(LABELS[13][0],'不建议使用')
        self.assertIn('完全无意义',LABELS[13][1])
        self.assertIn('第三方',LABELS[13][1])

if __name__=='__main__':unittest.main()
