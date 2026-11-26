"""The wording trial uses fixture models only, never live inference."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from hanser_qq.headless import create_app, install_backend
from test_headless import app_with_models


def trial_app(config, tmp_path, draft, polished):
    def reply(payload):
        return polished if payload['messages'][-1]['content'] == '只润色上面的候选回复，输出正文。' else draft
    original, calls = app_with_models(config, tmp_path, reply_text=reply)
    from hanser_qq.text_core import configure_text_core
    from hanser_agent.models import DialoguePlan
    settings = original.state.agent.style_tool.settings
    agent = configure_text_core(original.state.agent, settings, user_trial=True, expression_polish_enabled=True)
    async def plan(*args, **kwargs):
        return DialoguePlan(intent='chitchat', need_wiki=False, need_memory=False, need_style_examples=False,
                            standalone_query='等下班', keywords=[], response_mode='casual', fact_sensitivity='low',
                            dialogue_function='share', greeting_turn=False, target_length='short')
    agent.planner.plan = plan
    return create_app(config, agent=agent, settings=settings), calls


PAYLOAD = {'user_id': 'qq:100:200', 'conversation_id': 'qq:dm:100:200',
           'request_id': 'wording-trial', 'message': '还要等两个小时才下班，好难熬'}
DRAFT = '我靠，还要等两个小时，这也太难熬了。'
POLISHED = '我靠，这两个小时也太难熬了。'


def test_polished_semantic_is_committed_and_replayed_once_with_audit(config, tmp_path):
    app, calls = trial_app(config, tmp_path, DRAFT, POLISHED)
    with TestClient(app) as client:
        headers = {'X-Hanser-QQ-Token': config.backend.token}
        response = client.post('/v1/chat', headers=headers, json=PAYLOAD)
        assert response.status_code == 200, response.text
        data = response.json()
        assert len(calls) == 2
        assert client.post('/v1/chat', headers=headers, json=PAYLOAD).json() == data
        assert len(calls) == 2
        assert client.get('/health', headers=headers).json()['expression_polish']['enabled']
    snapshot = app.state.agent.conversations.get_reply_snapshot(data['reply_id'], user_id=PAYLOAD['user_id'])
    assert snapshot.semantic_text == POLISHED
    assert snapshot.persona_trace['generation']['raw_text'] == DRAFT
    assert snapshot.persona_trace['generation']['responder_calls'] == 2
    audit = json.loads((tmp_path / 'retrieval_logs' / (data['reply_id'] + '.json')).read_text(encoding='utf-8'))
    assert audit['expression_trial']['polish']['status'] == 'accepted'
    assert audit['expression_trial']['polish']['draft'] == DRAFT
    assert audit['expression_trial']['polish']['final'] == POLISHED
    assert audit['expression_trial']['profanity']['status'] == 'optional_release'


@pytest.mark.parametrize('candidate,violation', [
    ('这两个小时也太难熬了。', 'polish_removed_profanity'),
    ('我靠，这三个小时也太难熬了。', 'polish_changed_quantity'),
    ('我靠，这两个小时也太难熬了～', 'polish_added_format_or_tilde'),
    ('', 'polish_empty_or_expanded'),
])
def test_editor_cannot_erase_roughness_change_quantity_or_add_tilde(config, tmp_path, candidate, violation):
    app, calls = trial_app(config, tmp_path, DRAFT, candidate)
    with TestClient(app) as client:
        response = client.post('/v1/chat', headers={'X-Hanser-QQ-Token': config.backend.token}, json=PAYLOAD)
        assert response.status_code == 200, response.text
    snapshot = app.state.agent.conversations.get_reply_snapshot(response.json()['reply_id'], user_id=PAYLOAD['user_id'])
    assert snapshot.semantic_text == DRAFT and len(calls) == 2
    audit = json.loads((tmp_path / 'retrieval_logs' / (snapshot.reply_id + '.json')).read_text(encoding='utf-8'))
    assert violation in audit['expression_trial']['polish']['violations']


def test_optional_editor_failure_keeps_first_reply_without_regenerating(config, tmp_path):
    app, calls = trial_app(config, tmp_path, DRAFT, POLISHED)
    from hanser_agent.failures import ServiceFailure
    original = app.state.agent.responder.model_gateway.generate
    async def generate(profile, messages, **kwargs):
        if messages[-1].content == '只润色上面的候选回复，输出正文。':
            raise ServiceFailure('provider_timeout', 'fixture timeout', retryable=True)
        return await original(profile, messages, **kwargs)
    app.state.agent.responder.model_gateway.generate = generate
    with TestClient(app) as client:
        response = client.post('/v1/chat', headers={'X-Hanser-QQ-Token': config.backend.token}, json=PAYLOAD)
        assert response.status_code == 200, response.text
    snapshot = app.state.agent.conversations.get_reply_snapshot(response.json()['reply_id'], user_id=PAYLOAD['user_id'])
    assert snapshot.semantic_text == DRAFT and len(calls) == 1
    assert 'qq_expression_polish_unavailable' in snapshot.persona_trace['generation']['validator_actions']


def test_protected_and_factual_outputs_skip_the_editor(config):
    install_backend(config.backend.package_path)
    from hanser_qq.expression_trial import polish_skip_reason
    result = SimpleNamespace(generation_status='model_success', semantic_text=DRAFT)
    context = SimpleNamespace(exact_output='照抄', required_verbatim_spans=[], has_wiki_evidence=False,
                              persona=SimpleNamespace(response_mode='casual'), behavior_decision=None)
    assert polish_skip_reason(context, result) == 'protected_output'
    context.exact_output = None
    context.required_verbatim_spans = ['照抄']
    assert polish_skip_reason(context, result) == 'protected_output'
    context.required_verbatim_spans = []
    context.has_wiki_evidence = True
    assert polish_skip_reason(context, result) == 'factual_output'


def test_daily_complaint_opportunity_respects_disable_distress_and_cooldown(config):
    install_backend(config.backend.package_path)
    from hanser_qq.expression_trial import profanity_guidance
    from hanser_agent.models import ChatMessage, DialoguePlan
    from hanser_agent.persona.schemas import BehaviorDecision, EffectivePersonaSettings, TurnSignals, SignalObservation
    plan = DialoguePlan(intent='chitchat', need_wiki=False, need_memory=False, need_style_examples=False,
                        standalone_query='下班', keywords=[], response_mode='casual', target_length='short', fact_sensitivity='low')
    effective = EffectivePersonaSettings(profanity_level=1, profanity_target_rate=.15)
    decision = BehaviorDecision()
    history = [ChatMessage(role='assistant', content='还得再等一会儿。')] * 8
    text, trace = profanity_guidance('还要两个小时才下班', plan, history, None, effective, decision)
    assert trace['status'] == 'optional_release' and '我靠' in text
    history[-1] = ChatMessage(role='assistant', content='我靠，等好久了。')
    assert profanity_guidance('还要两个小时才下班', plan, history, None, effective, decision)[1]['status'] == 'cooldown'
    decision.expression_caps.hard_disallowed = ['profanity']
    assert profanity_guidance('还要两个小时才下班', plan, [], None, effective, decision)[1]['status'] == 'blocked'
    decision.expression_caps.hard_disallowed = []
    signals = TurnSignals(values={'distress': SignalObservation(name='distress', value=True, source='planner',
                         confidence='high', status='observed', evidence_refs=['fixture'])})
    assert profanity_guidance('好痛苦', plan, [], signals, effective, decision)[1]['status'] == 'blocked'
