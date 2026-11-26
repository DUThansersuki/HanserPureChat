"""Conversation contracts and local fixture transport; no model inference."""
import json

from fastapi.testclient import TestClient

from hanser_qq.headless import create_app, install_backend
from test_headless import app_with_models


def dialogue_plan(config):
    install_backend(config.backend.package_path)
    from hanser_agent.models import DialoguePlan
    return DialoguePlan(intent='chitchat', need_wiki=False, need_memory=False,
                        need_style_examples=False, standalone_query='下午好', keywords=[],
                        response_mode='casual', fact_sensitivity='low', target_length='short',
                        dialogue_function='other', greeting_turn=True)


def test_short_greeting_no_longer_requires_an_extra_sentence(config):
    plan = dialogue_plan(config)
    from hanser_qq.text_core import QQContextBuilder, QQValidator
    from hanser_agent.agent.context_builder import ContextBuilder
    from hanser_agent.persona.schemas import LanguageControl
    from hanser_agent.responder.validator import StyleValidator
    control = LanguageControl(greeting_extension_required=True)
    assert '再补一句' in ContextBuilder._response_contract(plan)
    contract = QQContextBuilder._response_contract(plan)
    assert '再补一句' not in contract and '一小句也可以' in contract
    assert '邀请聊天' in contract and '直接给具体内容' in contract
    assert '问候后自然多接一句' not in QQContextBuilder._language_control_block(control)
    assert control.greeting_extension_required is True and plan.greeting_turn is True
    # Use the selected frozen package, as the root compatibility file can differ.
    app, _ = app_with_models(config, config.storage_path.parent)
    path = app.state.agent.context_builder.persona_compiler.prompt_dir / 'style_constraints.yaml'
    assert 'bare_greeting' in StyleValidator(path).validate_semantic_output(
        '下午好。', language_control=control).violations
    result = QQValidator(path).validate_semantic_output('下午好。', language_control=control)
    assert 'bare_greeting' not in result.violations
    assert 'qq_short_greeting_allowed' in result.actions
    assert result.text == '下午好。'


def test_exact_output_keeps_requested_words_including_reported_phrase(config, tmp_path):
    plan = dialogue_plan(config)
    app, _ = app_with_models(config, tmp_path)
    from hanser_qq.text_core import QQContextBuilder, QQValidator
    literal = '那就随便聊几句，不用特意找什么正经话题。'
    contract = QQContextBuilder._response_contract(plan, exact_output=literal)
    assert 'QQ CONVERSATION' not in contract
    assert literal in contract
    validator = QQValidator(app.state.agent.context_builder.persona_compiler.prompt_dir / 'style_constraints.yaml')
    result = validator.validate_semantic_output(literal, exact_output=literal)
    assert result.text == literal and not result.violations


def test_hello_one_sentence_is_committed_once_and_contract_is_auditable(config, tmp_path):
    original, calls = app_with_models(config, tmp_path, reply_text='下午好。')
    from hanser_qq.text_core import configure_text_core
    settings = original.state.agent.style_tool.settings
    agent = configure_text_core(original.state.agent, settings, user_trial=True)
    async def plan(*args, **kwargs):
        return dialogue_plan(config)
    agent.planner.plan = plan
    app = create_app(config, agent=agent, settings=settings)
    payload = {'user_id': 'qq:100:200', 'conversation_id': 'qq:dm:100:200',
               'request_id': 'natural-short-greeting', 'message': '下午好呀'}
    with TestClient(app) as client:
        headers = {'X-Hanser-QQ-Token': config.backend.token}
        response = client.post('/v1/chat', headers=headers, json=payload)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['text'] == '下午好' and len(calls) == 1
        assert client.post('/v1/chat', headers=headers, json=payload).json() == data
        assert len(calls) == 1  # no repair solely to turn a greeting into filler
    snapshot = agent.conversations.get_reply_snapshot(data['reply_id'], user_id=payload['user_id'])
    assert snapshot.semantic_text == '下午好。'
    audit = json.loads((tmp_path / 'retrieval_logs' / (data['reply_id'] + '.json')).read_text(encoding='utf-8'))
    contract = audit['conversation_contract']
    assert 'qq-conversation-direct-turn-v1' in contract['sources']
    assert '再补一句' not in contract['response_contract']
    assert '问候后自然多接一句' not in contract['language_control']
