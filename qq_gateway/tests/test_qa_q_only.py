from pathlib import Path
from types import SimpleNamespace

import pytest

from hanser_qq.headless import install_backend
from hanser_qq.rag_retrieval import QuestionIndex, retrieval_plan, select_candidates
from test_rag_retrieval import unit, result


@pytest.fixture(autouse=True)
def backend():
    install_backend(Path(__file__).resolve().parents[1] / '.runtime/backend')


def query(text='无聊的时候聊什么', history=()):
    return retrieval_plan(text, SimpleNamespace(), history, None, None)


def test_qa_answer_keywords_cannot_nominate_a_pair():
    rows = [unit('wrong', 'qa', trigger='跟小学生打架算不算', text='无聊的时候找我聊天'),
            unit('right', 'qa', trigger='无聊的时候聊什么', text='随便聊聊呗')]
    hits = QuestionIndex(rows, []).search(query())
    assert [h['candidate']['unit_id'] for h in hits] == ['right']
    assert hits[0]['q_match']['current_terms']


def test_answer_text_length_and_a_labels_do_not_change_question_ranking():
    rows = [unit('one', 'qa', trigger='无聊的时候聊什么'),
            unit('two', 'qa', trigger='无聊了怎么办')]
    before = QuestionIndex(rows, []).search(query())
    changed = [{**u, 'text': '无聊聊天' * 300, 'label_number': 6,
                'behavior_tags': ['reasoned_independent_response']} for u in rows]
    after = QuestionIndex(changed, []).search(query())
    assert [(h['id'], h['q_score']) for h in before] == [(h['id'], h['q_score']) for h in after]


def test_behavior_only_qa_is_excluded_even_with_terra_enabled():
    from hanser_agent.models import StyleExample
    e = StyleExample(id='style:wrong', user_context='', source_user_turn='跟小学生打架算不算',
        character_response='无聊的时候聊聊天', scene='casual', speech_act='react', answer_length='short',
        response_mode='casual', source_type='real', authenticity_score=1, quality_score=1,
        behavior_tags=['reasoned_independent_response'])
    baseline = result([e]); baseline.matched_by[e.id] = ['A']
    selected, audit = select_candidates({'qa': [], 'a': []}, baseline, query(), semantic=True, local_top_k=10)
    assert not selected
    assert audit['excluded'][e.id] == 'qa_requires_source_q_retrieval'


def test_context_must_also_match_source_question_not_answer():
    history = [SimpleNamespace(content='之前在说上班太累')]
    rows = [unit('context_q', 'qa', trigger='上班累了怎么办', text='歇会儿'),
            unit('context_a', 'qa', trigger='红包怎么发', text='上班太累了')]
    hits = QuestionIndex(rows, []).search(query('接着聊', history))
    assert [h['candidate']['unit_id'] for h in hits] == ['context_q']
    assert hits[0]['q_match']['context_terms']


def test_a_preferences_do_not_reorder_q_candidates():
    rows = [unit('one', 'qa', trigger='无聊的时候聊什么', label=1),
            unit('two', 'qa', trigger='无聊了怎么办', label=6)]
    q = query(); lanes = {'qa': QuestionIndex(rows, []).search(q), 'a': []}
    first, _ = select_candidates(lanes, result(), q, semantic=True, local_top_k=10)
    q['label_preferences'] = {6: 1, 1: 0}
    second, _ = select_candidates(lanes, result(), q, semantic=True, local_top_k=10)
    assert [x['id'] for x in first] == [x['id'] for x in second]
    assert all(x['lane'] == 'qa' for x in first)
