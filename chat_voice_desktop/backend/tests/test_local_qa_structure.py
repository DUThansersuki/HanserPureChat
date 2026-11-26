import unittest
from scripts.local_qa_structure import extract


class LocalQuizStructureTests(unittest.TestCase):
    def test_chinese_qa_and_multiline_keys_preserve_source_not_speaker(self):
        text='题1：考试等级？\n答：N1（排名第14名）\n题目二：说出两首歌\n答案：\n①歌甲\n②歌乙\n0.35；继续直播'
        rows=extract({'text':text,'source_id':'DOC-TEST','filename':'test'})
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[1]['answer_text'],'①歌甲\n②歌乙')
        for row in rows:
            self.assertFalse(row['answer_is_hanser_speech_verified'])
            for key in ['question','answer']:
                self.assertEqual(''.join(text[s['start']:s['end']] for s in row[key+'_source_spans']),row[key+'_text'])

    def test_inline_editor_answer_only_in_quiz_context(self):
        doc={'source_id':'DOC-TEST','filename':'test'}
        self.assertEqual(extract({**doc,'text':'你吃饭了吗？//编辑吐槽'}),[])
        row=extract({**doc,'text':'今晚问答赛道\n给哪个角色配音？//角色甲<!--注-->'})[0]
        self.assertEqual(row['answer_text'],'角色甲')
        self.assertEqual(row['record_kind'],'quiz_answer_key')
