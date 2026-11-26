"""No network/model inference: independent quotas and actual source pairs."""
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import json

import pytest

from hanser_qq.headless import install_backend
from hanser_qq.rag_retrieval import retrieval_plan, search_lane, select_candidates


@pytest.fixture(autouse=True)
def backend():
    install_backend(Path(__file__).resolve().parents[1] / '.runtime/backend')


def result(examples=()):
    from hanser_agent.agent.tools.style_search import StyleSearchResult
    return StyleSearchResult(examples=list(examples))


def query(text='星空摄影', history=()):
    return retrieval_plan(text, SimpleNamespace(), history, None, None)


def unit(uid, lane='a', text='星空摄影真有意思', trigger='星空摄影怎么拍', label=1, **extra):
    return {'unit_id': uid, 'qa_status': 'source_pair' if lane == 'qa' else 'a_only',
        'explicit_source_trigger': trigger if lane == 'qa' else '', 'text': text,
        'label_number': label, 'source_id': uid, 'attribution_basis': 'source_prior', **extra}


def hits(rows):
    return [{'candidate': u, 'rank': -10 + i * .1} for i, u in enumerate(rows)]


def test_separate_three_plus_three_and_qa_pairing():
    q = [unit('q'+str(i), 'qa', text='星空摄影回应'+str(i)) for i in range(5)]
    a = [unit('a'+str(i), text='星空摄影表达'+str(i)) for i in range(5)]
    selected, trace = select_candidates({'qa': hits(q), 'a': hits(a)}, result(), query())
    assert trace['selected_by_lane'] == {'qa': 3, 'a': 3}
    assert len(selected) == 6
    assert [x['lane'] for x in selected] == ['qa', 'a'] * 3
    assert all(x['unit']['explicit_source_trigger'] == '星空摄影怎么拍' for x in selected if x['lane'] == 'qa')


def test_no_cross_lane_backfill_and_empty_is_valid():
    selected, trace = select_candidates({'qa': [], 'a': hits([unit(str(i), text='星空摄影'+str(i)) for i in range(8)])}, result(), query())
    assert len(selected) == 3 and trace['selected_by_lane'] == {'a': 3}
    selected, _ = select_candidates({'qa': [], 'a': hits([unit('bad', text='那咋办 今晚播不了了')])}, result(), query('那咋办嘛'))
    assert not selected


def test_qa_matches_q_not_answer_and_a_pool_excludes_pairs(tmp_path):
    p = tmp_path / 'corpus.db'
    with sqlite3.connect(p) as c:
        c.executescript('CREATE TABLE units(unit_id,bucket,label_number,record_json); CREATE VIRTUAL TABLE candidate_fts USING fts5(unit_id UNINDEXED,answer,trigger_text);')
        rows = [unit('qa', 'qa', text='无关回答', trigger='星空摄影'),
                unit('answer_only', 'qa', text='星空摄影', trigger='午饭吃啥'),
                unit('a', text='星空摄影')]
        for u in rows:
            c.execute('INSERT INTO units VALUES(?,?,?,?)', (u['unit_id'], 'classified', 1, json.dumps(u)))
            c.execute('INSERT INTO candidate_fts VALUES(?,?,?)', (u['unit_id'], u['text'], u['explicit_source_trigger']))
    assert [h['candidate']['unit_id'] for h in search_lane(p, {'星空摄影'}, 'qa')] == ['qa']
    assert [h['candidate']['unit_id'] for h in search_lane(p, {'星空摄影'}, 'a')] == ['a']


def test_context_is_recorded_but_does_not_replace_current_content():
    q = query('星空摄影', [SimpleNamespace(content='昨天一直在练习吉他')])
    assert q['context_terms'] and 'history_-1' in q['evidence_refs']
    selected, trace = select_candidates({'qa': [], 'a': hits([unit('offtopic', text='吉他练习真有意思')])}, result(), q)
    assert not selected
    assert trace['excluded']['qq-trial:offtopic'] == 'insufficient_context_match'


def test_greeting_does_not_retrieve_birthday_or_traffic():
    rows = [unit('birthday', text='下午五点十五出生', label=2),
            unit('traffic', text='下午会堵车', label=2),
            unit('greet', text='下午好呀', label=9)]
    selected, _ = select_candidates({'qa': [], 'a': hits(rows)}, result(), query('下午好呀'))
    assert [x['id'] for x in selected] == ['qq-trial:greet']


def test_reviewed_behavior_hit_cannot_impersonate_matching_qa():
    from hanser_agent.models import StyleExample
    example = StyleExample(id='reviewed:unrelated', user_context='',
        character_response='但他是一种溺爱', source_user_turn='憨爸不也是爱憨嘛',
        scene='casual', speech_act='react', answer_length='short', response_mode='casual',
        source_type='real', authenticity_score=1, quality_score=1)
    selected, trace = select_candidates({'qa': [], 'a': []}, result([example]), query('下午好呀'))
    assert not selected
    assert trace['excluded'][example.id] == 'qa_requires_source_q_retrieval'


def test_mimic_speech_does_not_match_audio_check_by_speech_alone():
    lanes = {'qa': hits([unit('audio', 'qa', trigger='可以听到我说话吗', text='嗯可以'),
                         unit('mimic', 'qa', trigger='学别人说话', text='星空摄影回应')]), 'a': []}
    selected, trace = select_candidates(lanes, result(), query('干嘛学我说话'))
    assert [x['id'] for x in selected] == ['qq-trial:mimic']
    assert trace['excluded']['qq-trial:audio'] == 'insufficient_context_match'


def test_explicit_expression_block_and_recent_penalty():
    from hanser_agent.persona.schemas import BehaviorDecision
    d = BehaviorDecision(expression_caps={'hard_disallowed': ['profanity']})
    rows = [unit('swear', text='星空摄影 我靠！'), unit('recent'), unit('fresh', text='星空摄影很漂亮')]
    selected, trace = select_candidates({'qa': [], 'a': hits(rows)}, result(), query(), decision=d, recent_ids=['qq-trial:recent'])
    assert trace['excluded']['qq-trial:swear'] == 'expression_hard_blocked'
    assert selected[0]['id'] == 'qq-trial:fresh'


def test_relevant_qa_soft_labels_influence_a_order():
    q = [unit('qa', 'qa', label=6)]
    a = [unit('neutral', text='星空摄影表达', label=1), unit('playful', text='星空摄影好好笑', label=6)]
    plan = query(); plan['label_preferences'] = {}
    selected, trace = select_candidates({'qa': hits(q), 'a': hits(a)}, result(), plan)
    assert trace['plan']['final_label_preferences'][6] > 0
    assert next(x for x in selected if x['lane'] == 'a')['id'] == 'qq-trial:playful'


def test_six_selected_survive_runtime_models_and_qa_prompt_rendering():
    from hanser_qq.runtime_style import FullCorpusStyleTool
    from hanser_qq.text_core import QQContextBuilder
    rows = {}
    for lane in ('qa', 'a'):
        rows[lane] = hits([unit(lane+str(i), lane, text='星空摄影回应'+lane+str(i),
            reaction_source_start_char=0, reaction_source_end_char=20,
            source_revision='fixture-source', source_spans=[]) for i in range(3)])
    baseline = result()
    selected, trace = select_candidates(rows, baseline, query())
    tool = FullCorpusStyleTool.__new__(FullCorpusStyleTool)
    tool.generation, tool.pinned_generation = 'test-corpus', 'test-style'
    converted = tool._trial_selection(baseline, selected, trace)
    assert len(converted.examples) == 6
    assert converted.candidate_counts['qa_selected'] == converted.candidate_counts['a_selected'] == 3
    # The real builder's item renderer must keep Q paired to A and preserve the
    # trial boundary, without making a responder call.
    builder = QQContextBuilder.__new__(QQContextBuilder)
    prompt = '\n'.join(builder._style_item(e, i+1) for i, e in enumerate(converted.examples))
    assert prompt.count('<source_user>') == 3
    assert prompt.count('<source_response>') == 6
    assert prompt.count('review_status="pending"') == 6
    assert all(set(routes) <= {'Q', 'A'} for routes in converted.matched_by.values())


def test_real_context_budget_can_inject_all_six(config, tmp_path):
    from fastapi.testclient import TestClient
    from test_headless import app_with_models
    from hanser_qq.runtime_style import FullCorpusStyleTool
    from hanser_qq.text_core import configure_text_core
    from hanser_qq.headless import create_app
    original, calls = app_with_models(config, tmp_path)
    settings = original.state.agent.style_tool.settings
    agent = configure_text_core(original.state.agent, settings, user_trial=True)
    raw = {lane: hits([unit(lane+str(i), lane, text='星空摄影回应'+lane+str(i),
        reaction_source_start_char=0, reaction_source_end_char=20,
        source_revision='fixture-source', source_spans=[]) for i in range(3)]) for lane in ('qa', 'a')}
    selected, trace = select_candidates(raw, result(), query())
    tool = FullCorpusStyleTool.__new__(FullCorpusStyleTool)
    tool.generation, tool.pinned_generation = 'test-corpus', 'test-style'
    selected_result = tool._trial_selection(result(), selected, trace)
    async def search(*args, **kwargs):
        return selected_result
    agent.style_tool.search = search
    app = create_app(config, agent=agent, settings=settings)
    with TestClient(app) as client:
        response = client.post('/v1/chat', headers={'X-Hanser-QQ-Token': config.backend.token}, json={
            'user_id': 'qq:100:200', 'conversation_id': 'qq:dm:100:200',
            'request_id': 'six-rag-budget', 'message': '星空摄影'})
        assert response.status_code == 200, response.text
    audit = json.loads((tmp_path / 'retrieval_logs/latest.json').read_text(encoding='utf-8'))
    assert len(audit['injected']['style_ids']) == 6
    assert audit['injected']['prompt_blocks']['style_examples'].count('<source_user>') == 3
    assert calls  # httpx.MockTransport fixtures only; no real provider requests
