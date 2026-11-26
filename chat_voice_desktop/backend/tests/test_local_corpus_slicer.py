import unittest

from scripts.local_corpus_slicer import SliceConfig, atoms, slice_document


def document(text):
    return {'text':text,'source_id':'DOC-TEST','document_id':1,'filename':'test.docx'}


class LocalSlicerTests(unittest.TestCase):
    def test_circled_qa_without_colons(self):
        text='④Q怎么保持热情A我喜欢就会继续。'
        result=slice_document(document(text))
        row=next(r for r in result['candidates'] if '继续' in r['text'])
        self.assertEqual(row['text'],'我喜欢就会继续。')
        self.assertEqual(row['explicit_source_trigger'],'怎么保持热情')
        self.assertEqual(row['qa_pair_basis'],'explicit_qa_markers')
        self.check_mapping(text,result)

    def test_inline_actor_after_timestamp_and_numbered_audience_semicolon(self):
        text='Han：我回来了。2.38憨：填满。三：我不是Hanser。Han：收到。毛怪1；我有个问题。Han：你说。'
        result=slice_document(document(text))
        self.assertEqual([r['text'] for r in result['candidates']],['我回来了。','填满。','收到。','你说。'])
        self.assertEqual(result['candidates'][-1]['explicit_source_trigger'],'我有个问题。')
        self.check_mapping(text,result)

    def test_dense_recording_clocks_and_timestamped_submission_header(self):
        text='22:15你好22:16再见22:17结束\n2.06；L04：养蝉\n憨：我们那时候也是五毛。'
        result=slice_document(document(text))
        rendered=''.join(r['text'] for r in result['candidates'])
        self.assertNotIn('22:',rendered)
        self.assertNotIn('养蝉',rendered)
        self.check_mapping(text,result)

    def test_multiline_editor_note_and_inline_performance_title(self):
        text='憨：你好。（详情见录播，原话：第一行\n第二行（内注）结束）再见。01:39《凤仙花》01:44我喜欢《凤仙花》。'
        result=slice_document(document(text))
        rendered=''.join(r['text'] for r in result['candidates'])
        self.assertNotIn('第二行',rendered)
        self.assertNotIn('《凤仙花》我喜欢',rendered)
        self.assertIn('我喜欢《凤仙花》。',rendered)
        self.check_mapping(text,result)

    def test_inline_semicolon_timestamps_aliases_and_metadata(self):
        text='负责记录：某人\n负责校对：某人\nHan：你好。Y：别人的话。三无：客人的话。猫：另一位。聂总：主持的话。Han：回来啦。（憨笑）2.30；开始唱。2.56；结束唱。'
        result=slice_document(document(text))
        rendered=''.join(r['text'] for r in result['candidates'])
        for unwanted in ['某人','别人的话','客人的话','另一位','主持的话','憨笑','2.30','2.56']:
            self.assertNotIn(unwanted,rendered)
        self.assertIn('回来啦。',rendered)
        self.check_mapping(text,result)

    def check_mapping(self, text, result):
        self.assertEqual(''.join(p['text'] for p in result['atoms']),text)
        for row in result['candidates']:
            self.assertEqual(''.join(text[s['start']:s['end']] for s in row['source_spans']),row['text'])
            self.assertFalse(row['runtime_eligible'])

    def test_reply_merges_its_sentences_and_keeps_real_trigger(self):
        text='弹幕：今天累不累？\n憨：累死我了！先让我歇会。\n'
        result=slice_document(document(text))
        self.assertEqual([r['text'] for r in result['candidates']],['累死我了！先让我歇会。'])
        self.assertEqual(result['candidates'][0]['explicit_source_trigger'],'今天累不累？')
        self.check_mapping(text,result)

    def test_other_speaker_continuation_is_not_hanser(self):
        text='憨：我建议不看。\n大毛：我来试试。\n我还要说一句\n0.05\n没有标记的这段\n憨：我回来了。'
        result=slice_document(document(text))
        self.assertNotIn('我还要说一句',''.join(r['text'] for r in result['candidates']))
        unknown=next(r for r in result['candidates'] if '没有标记' in r['text'])
        self.assertEqual(unknown['speaker_candidate'],'unknown')
        self.check_mapping(text,result)

    def test_inline_comments_and_headers_preserve_ranges(self):
        text='2023年5月22日 星期一\nTitle：测试\n0.12 憨：你好<!--注释跨\n两行-->再见。\n校对：某人\n'
        result=slice_document(document(text))
        self.assertNotIn('注释',''.join(r['text'] for r in result['candidates']))
        self.assertNotIn('测试',''.join(r['text'] for r in result['candidates']))
        self.check_mapping(text,result)

    def test_inline_numeric_fact_and_ordinary_parenthesis_are_preserved(self):
        text='憨：圆周率是3.14，时间是12:30（只是举例）。'
        result=slice_document(document(text))
        self.assertEqual(result['candidates'][0]['text'],'圆周率是3.14，时间是12:30（只是举例）。')
        self.check_mapping(text,result)

    def test_inline_comment_stops_at_next_recording_marker(self):
        text='憨：像不像水滴声！//编辑吐槽00:40我教你们一个诀窍。00:41再来一次。'
        result=slice_document(document(text))
        rendered=''.join(r['text'] for r in result['candidates'])
        self.assertIn('我教你们一个诀窍。',rendered)
        self.assertIn('再来一次。',rendered)
        self.assertNotIn('编辑吐槽',rendered)
        self.assertNotIn('00:40',rendered)
        self.check_mapping(text,result)

    def test_audience_identity_does_not_make_whole_document_multispeaker(self):
        result=slice_document(document('毛怪：你好！\n0.02晚上好。'))
        self.assertEqual(result['candidates'][0]['attribution_basis'],'source_prior')
        self.assertEqual(result['candidates'][0]['speaker_candidate'],'hanser')

    def test_source_prior_is_accepted_without_second_speaker_confirmation(self):
        row=slice_document(document('晚上好。'))['candidates'][0]
        self.assertEqual(row['attribution_status'],'accepted_by_source_policy')
        self.assertFalse(row['speaker_confirmation_required'])
        self.assertNotIn('source_prior',row['risk_flags'])
        self.assertFalse(row['runtime_eligible'])
        unknown=slice_document(document('憨：你好。\n菠：我来了。\n0:23没有说话人标记的这段。'))['candidates'][-1]
        self.assertEqual(unknown['speaker_candidate'],'unknown')
        self.assertTrue(unknown['speaker_confirmation_required'])

    def test_additional_actor_aliases_and_cheer_prefix_do_not_leak(self):
        text='H：你好！\nM：我不是hanser。\n憨：我回来了。\n菠：我教你方法。\n应援弹幕：歌词正文\n0:23憨：再见。'
        result=slice_document(document(text))
        rendered=''.join(r['text'] for r in result['candidates'])
        self.assertNotIn('不是hanser',rendered)
        self.assertNotIn('教你方法',rendered)
        self.assertNotIn('应援',rendered)
        self.assertNotIn('歌词正文',rendered)
        self.assertNotIn('0:23',rendered)
        self.check_mapping(text,result)

    def test_unrecognized_actor_is_explicitly_unknown_and_negative_marker_is_removed(self):
        text='-0.06；\n陌生人：未知说话人的话。\n憨：你好。'
        result=slice_document(document(text))
        self.assertNotIn('-0.06',''.join(r['text'] for r in result['candidates']))
        self.assertEqual(result['candidates'][0]['attribution_basis'],'unrecognized_speaker_label')
        self.assertEqual(result['candidates'][0]['speaker_candidate'],'unknown')

    def test_natural_colon_and_inline_timestamp_speaker_are_not_unknown_actor(self):
        result=slice_document(document('然后对方回复说：“等一下。”\n还有就是：不要急。\n0:23憨：好的。\n0:24菠：你看这里。'))
        self.assertTrue(any('然后对方回复说' in r['text'] for r in result['candidates']))
        self.assertFalse(any(r['attribution_basis']=='unrecognized_speaker_label' for r in result['candidates']))
        self.assertNotIn('你看这里',''.join(r['text'] for r in result['candidates']))

    def test_quote_continuation_and_document_separator(self):
        text='憨：“哪个比较秃一点？\n她这个不能调身子啊”#@p@#\n茶：轮到我了。'
        result=slice_document(document(text))
        self.assertEqual(len(result['candidates']),1)
        self.assertIn('她这个不能调身子啊”',result['candidates'][0]['text'])
        self.assertNotIn('#@p@#',result['candidates'][0]['text'])
        self.check_mapping(text,result)

    def test_timestamped_action_note_and_goodnight_song_are_not_reply(self):
        text='2.37；（打算不玩的时候）\n2.49；小缘谢完，到hanser谢礼物~\n3.19；晚安歌《深海少女》//编辑评价\n憨：晚安。'
        result=slice_document(document(text))
        self.assertEqual([r['text'] for r in result['candidates']],['晚安。'])

    def test_submission_section_heading_is_not_joined_to_reply(self):
        text='憨：我不敢和猫玩得很欢\nL02: 养鱼（B站id：某人）\n憨：下一个投稿。'
        result=slice_document(document(text))
        self.assertNotIn('L02',''.join(r['text'] for r in result['candidates']))
        self.assertNotIn('B站id',''.join(r['text'] for r in result['candidates']))
        self.check_mapping(text,result)

    def test_sentence_before_newline_starts_separate_reply(self):
        result=slice_document(document('早上好。\n今天开始讲另一个话题。'))
        self.assertEqual([r['text'] for r in result['candidates']],['早上好。','今天开始讲另一个话题。'])

    def test_length_cap_and_fragment_filter(self):
        text='憨：'+('很长的话没有标点'*50)+'\nhan\n'
        result=slice_document(document(text),SliceConfig(120,240))
        self.assertTrue(any('hard_length_split' in r['risk_flags'] for r in result['candidates']))
        self.assertTrue(all(len(r['text'])<=240 for r in result['candidates']))
        self.check_mapping(text,result)
        result=slice_document(document('han\n'))
        self.assertEqual(result['candidates'],[])
        self.assertEqual(result['excluded'][0]['reason'],'latin_label_fragment')

    def test_inline_bare_and_numbered_qa_keep_exact_pairs(self):
        text='Q：专辑呢？A：我在努力了。Q2：想去哪？A2：青海。'
        rows=slice_document(document(text))['candidates']
        self.assertEqual([r['text'] for r in rows],['我在努力了。','青海。'])
        self.assertEqual([r['explicit_source_trigger'] for r in rows],['专辑呢？','想去哪？'])
        self.assertTrue(all(r['qa_pair_basis']=='explicit_qa_markers' for r in rows))
        for row in rows:
            self.assertEqual(''.join(text[s['start']:s['end']] for s in row['trigger_source_spans']),row['explicit_source_trigger'])

    def test_question_line_then_source_prior_answer_and_missing_answer(self):
        rows=slice_document(document('问题18如果去不了怎么办？\n有事去不了就退票嘛。\n问题19什么时候开票？'))['candidates']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['text'],'有事去不了就退票嘛。')
        self.assertEqual(rows[0]['qa_pair_basis'],'source_prior_adjacency')
        self.assertEqual(rows[0]['attribution_status'],'accepted_by_source_policy')
        self.assertEqual(slice_document(document('Q：只有问题吗？'))['candidates'],[])
        a=slice_document(document('A：我在努力了。'))['candidates'][0]
        self.assertEqual(a['qa_status'],'a_only')
        self.assertEqual(a['explicit_source_trigger'],'')

    def test_other_actor_or_timestamp_interrupts_qa_pair(self):
        row=slice_document(document('弹幕：你累吗？\n菠：我累。\n憨：你好。'))['candidates'][0]
        self.assertEqual(row['qa_status'],'a_only')
        row=slice_document(document('Q1：你累吗？A2：你好。'))['candidates'][0]
        self.assertEqual(row['qa_status'],'a_only')
        self.assertIn('qa_number_mismatch',row['risk_flags'])
        row=slice_document(document('Q：你累吗？\n0:30\nA：你好。'))['candidates'][0]
        self.assertEqual(row['qa_status'],'a_only')


if __name__=='__main__':unittest.main()
