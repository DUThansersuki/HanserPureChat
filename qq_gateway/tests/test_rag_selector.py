import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from hanser_qq.config import RagSelectorSettings
from hanser_qq.rag_selector import ModelRagSelector, validate_numbers
from hanser_qq.rag_retrieval import retrieval_plan, select_candidates
from hanser_qq.headless import install_backend
from test_rag_retrieval import unit, hits, result
from test_corpus import corpus_inputs


@pytest.fixture(autouse=True)
def backend():
    install_backend(Path(__file__).resolve().parents[1] / '.runtime/backend')


def candidates(count=10):
    return [{'id': lane + str(i), 'lane': lane, 'text': '素材' + lane + str(i),
             'unit': {'explicit_source_trigger': '来源问题' if lane == 'qa' else None}}
            for i in range(count) for lane in ('qa', 'a')]


def trace():
    return {'selected_ids': [c['id'] for c in candidates()], 'selected_by_lane': {'qa': 10, 'a': 10}}


def test_one_call_only_numbers_and_twenty_full_history_messages(tmp_path):
    key = tmp_path / 'key'; key.write_text('test-key', encoding='utf-8')
    settings = RagSelectorSettings(enabled=True, api_key_file=key)
    calls = []
    def respond(request):
        body = json.loads(request.content); calls.append(body)
        return httpx.Response(200, json={'id': 'selector-response', 'model': 'gpt-6.1-terra',
            'choices': [{'message': {'content': '{"qa":[1,5,3],"a":[2,6,4]}'}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20}},
            headers={'x-oneapi-request-id': 'selector-request'})
    selector = ModelRagSelector(settings, transport=httpx.MockTransport(respond))
    history = [SimpleNamespace(role='user' if i % 2 == 0 else 'assistant', content=str(i) + '长' * 300) for i in range(24)]
    audit = trace()
    selected = asyncio.run(selector.select(candidates(), '当前消息', history, audit))
    assert [c['id'] for c in selected] == ['qa0', 'a0', 'qa2', 'a2', 'qa1', 'a1']
    assert len(calls) == 1 and calls[0]['model'] == 'rag-selector'
    payload = json.loads(calls[0]['messages'][1]['content'])
    assert len(payload['history']) == 20 and payload['history'][0]['content'].startswith('4长')
    assert len(payload['history'][0]['content']) > 160
    assert len(payload['candidates']) == 20 and payload['current_message'] == '当前消息'
    assert audit['selector']['returned_model'] == 'gpt-6.1-terra'
    assert audit['selected_by_lane'] == {'qa': 3, 'a': 3}


@pytest.mark.parametrize('content', [
    '{"qa":[1,1],"a":[]}', '{"qa":[2],"a":[]}', '{"qa":[99],"a":[]}',
    '{"qa":[true],"a":[]}', '{"qa":[1,3,5,7],"a":[]}',
    '{"qa":[],"a":[],"reason":"解释"}', '```json\n{"qa":[],"a":[]}\n```',
])
def test_reject_unknown_duplicate_wrong_lane_or_non_number_outputs(content):
    with pytest.raises(ValueError):
        validate_numbers(content, candidates())


def test_local_top10_keeps_weak_matches_for_model_instead_of_hard_rejection():
    query = retrieval_plan('星空摄影', SimpleNamespace(), [], None, None)
    raw = {lane: hits([unit(lane + str(i), lane, text='不同表达' + lane + str(i),
        trigger='星空问题' + str(i)) for i in range(14)]) for lane in ('qa', 'a')}
    selected, audit = select_candidates(raw, result(), query, local_top_k=10, semantic=True)
    assert len(selected) == 20 and audit['selected_by_lane'] == {'qa': 10, 'a': 10}
    assert 'insufficient_context_match' not in audit['excluded'].values()


def test_failure_does_not_silently_use_local_ten_as_final(tmp_path):
    key = tmp_path / 'key'; key.write_text('test-key', encoding='utf-8')
    def respond(request):
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"qa":[999],"a":[]}'}}]})
    selector = ModelRagSelector(RagSelectorSettings(api_key_file=key), transport=httpx.MockTransport(respond))
    audit = trace()
    with pytest.raises(ValueError):
        asyncio.run(selector.select(candidates(), '问题', [], audit))
    assert audit['selector']['status'] == 'failed'
    assert audit['selected_ids'] == [] and audit['selected_by_lane'] == {}


def test_empty_candidates_never_call_model():
    selector = ModelRagSelector(RagSelectorSettings())
    audit = {'selected_ids': [], 'selected_by_lane': {}}
    assert asyncio.run(selector.select([], '问题', [], audit)) == []
    assert audit['selector']['status'] == 'skipped_empty_candidates'


def test_history_is_not_dropped_from_selector_plan():
    history = [SimpleNamespace(role='user' if i % 2 == 0 else 'assistant', content='消息' + str(i)) for i in range(20)]
    q = retrieval_plan('接着聊', SimpleNamespace(), history, None, None)
    assert len(q['context_messages']) == 20


def test_planner_reads_ten_persisted_rounds_even_when_prompt_history_is_short(config, tmp_path):
    from test_headless import app_with_models
    from hanser_qq.text_core import QQPlanner, CURRENT_REQUEST
    from hanser_qq.rag_retrieval import RETRIEVAL_HISTORY
    from hanser_agent.models import DialoguePlan
    app, calls = app_with_models(config, tmp_path)
    agent = app.state.agent
    for i in range(13):
        agent.conversations.append_turn(conversation_id='qq:dm:100:200', user_id='qq:100:200',
            user_text=f'用户{i}', assistant_text=f'回复{i}', model_name='fixture', trace_id=str(i), persona_version='fixture')
    class Planner:
        model_gateway = None
        async def plan(self, *args):
            return DialoguePlan(intent='chitchat', need_wiki=False, standalone_query='继续聊',
                keywords=[], response_mode='casual', fact_sensitivity='low', target_length='short')
    async def run():
        request_token = CURRENT_REQUEST.set(SimpleNamespace(conversation_id='qq:dm:100:200', user_id='qq:100:200'))
        history_token = RETRIEVAL_HISTORY.set(())
        try:
            planner = QQPlanner(Planner(), agent.memory_store, agent.post_turn.retriever)
            await planner.plan('继续聊', [], '已被摘要覆盖的历史')
            history = RETRIEVAL_HISTORY.get()
            assert len(history) == 20
            assert history[0].content == '用户3' and history[-1].content == '回复12'
        finally:
            RETRIEVAL_HISTORY.reset(history_token)
            CURRENT_REQUEST.reset(request_token)
    asyncio.run(run())
    assert not calls


def test_selector_attached_to_real_context_and_audit(config, tmp_path, corpus_inputs, monkeypatch):
    from fastapi.testclient import TestClient
    from test_headless import app_with_models
    from hanser_qq.corpus import build_corpus
    from hanser_qq.headless import create_app
    from hanser_qq.runtime_style import attach_release_engine
    from hanser_qq.releases import read_json, migrate_style
    from hanser_agent.desktop_api import build_desktop_chat_agent
    from hanser_agent.memory.store import MemoryStore
    import hanser_qq.rag_selector as module
    build_corpus(*corpus_inputs)
    original, _ = app_with_models(config, tmp_path)
    settings = original.state.agent.style_tool.settings
    release = Path(__file__).resolve().parents[1] / 'releases/qq-full-corpus-20261018-v8'
    if not (release / "release.json").exists():
        pytest.skip("requires the separately supplied full-corpus release")
    manifest = read_json(release / 'release.json')
    migrate_style(settings.db_path, read_json(release / 'style_rows.json'), manifest)
    key = tmp_path / 'selector-key'; key.write_text('fixture', encoding='utf-8')
    config.rag_selector = RagSelectorSettings(enabled=True, api_key_file=key)
    calls = []
    def respond(request):
        body = json.loads(request.content)
        payload = json.loads(body['messages'][1]['content']); calls.append(payload)
        selected = {lane: [c['number'] for c in payload['candidates'] if c['lane'] == lane][:3] for lane in ('qa', 'a')}
        return httpx.Response(200, json={'model': 'gpt-6.1-terra',
            'choices': [{'message': {'content': json.dumps(selected)}}]})
    real_class = module.ModelRagSelector
    monkeypatch.setattr(module, 'ModelRagSelector', lambda s: real_class(s, transport=httpx.MockTransport(respond)))
    agent = build_desktop_chat_agent(settings, original.state.agent.responder.model_gateway,
        MemoryStore(settings.db_path), persona_dir_override=release / 'persona')
    agent = attach_release_engine(agent, settings, manifest, corpus_inputs[2], user_trial=True, rag_selector=config.rag_selector)
    app = create_app(config, agent=agent, settings=settings)
    with TestClient(app) as client:
        response = client.post('/v1/chat', headers={'X-Hanser-QQ-Token': config.backend.token}, json={
            'user_id': 'qq:100:200', 'conversation_id': 'qq:dm:100:200',
            'request_id': 'selector-context-integration', 'message': '晚上好'})
        assert response.status_code == 200, response.text
    audit = json.loads((tmp_path / 'retrieval_logs/latest.json').read_text(encoding='utf-8'))
    assert len(calls) == 1
    assert audit['retrieval_selection']['selector']['status'] == 'completed'
    assert audit['retrieval_selection']['selector']['returned_model'] == 'gpt-6.1-terra'
    assert audit['injected']['style_ids'] == audit['retrieval_selection']['selected_ids']
