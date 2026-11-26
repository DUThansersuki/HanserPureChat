import unittest
from scripts.prepare_a13_bulk import exclusion_reason


def row(text,**extra):
    return {'text':text,'speaker_candidate':'hanser','risk_flags':[],**extra}


class BulkPreparationTests(unittest.TestCase):
    def test_real_third_party_content_remains_for_classification(self):
        self.assertIsNone(exclusion_reason(row('我妈妈昨天给我打电话。')))
        self.assertIsNone(exclusion_reason(row('我喜欢《凤仙花》。')))
        self.assertIsNone(exclusion_reason(row('嗯，好。')))
        self.assertIsNone(exclusion_reason(row('时间是12:30。')))

    def test_input_pollution_is_excluded_without_rewriting(self):
        for text in ['《凤仙花》','M12：我来回答。','L04：养蝉','1,02；','/谁是玩家/我不知道。','你好（编辑注）','22:15我们开播了。']:
            self.assertIsNotNone(exclusion_reason(row(text)),text)
        self.assertEqual(exclusion_reason(row('你好。',speaker_candidate='unknown')),'unknown_speaker')
        self.assertEqual(exclusion_reason(row('还没说完',risk_flags=['hard_length_split'])),'hard_length_split')
