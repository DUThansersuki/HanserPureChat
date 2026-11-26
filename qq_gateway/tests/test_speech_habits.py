import json
import sqlite3

from fastapi.testclient import TestClient

from hanser_qq.headless import create_app
from hanser_qq.speech_habits import load_profile, mine, trial_guidance
from test_headless import app_with_models


def test_inventory_counts_dispositions_and_keeps_exact_original_coordinates(tmp_path):
    path = tmp_path / 'corpus.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.executescript('CREATE TABLE metadata(key,value); CREATE TABLE units(bucket,label_number,record_json,source_id,unit_id);')
        conn.execute('INSERT INTO metadata VALUES(?,?)', ('manifest', json.dumps({'generation': 'test-source'})))
        for i, (bucket, label, source) in enumerate([
            ('classified', 1, 'doc1'), ('classified', 2, 'doc2'), ('classified', 13, 'doc3'), ('excluded', None, 'doc4')]):
            row = {'unit_id': str(i), 'source_id': source, 'text': '哎呀 好拗口', 'filename': source,
                'source_revision': 'source-version', 'source_spans': [{'start': 50, 'end': 57, 'text': '哎呀 好拗口'}],
                'attribution_basis': 'source_prior', 'review_status': 'pending'}
            conn.execute('INSERT INTO units VALUES(?,?,?,?,?)', (bucket, label, json.dumps(row), source, str(i)))
    report = mine(path)
    phrase = next(row for row in report['phrases'] if row['phrase'] == '哎呀')
    assert report['scanned_units'] == 4 and report['counted_units'] == 2
    assert (phrase['occurrences'], phrase['documents']) == (2, 2)
    assert [(item['start'], item['end'], item['source_review_status']) for item in phrase['evidence']] == [(50, 52, 'pending')] * 2


def test_voice_guidance_respects_facts_emotion_format_and_recent_repetition(config, tmp_path):
    app, _ = app_with_models(config, tmp_path)
    from hanser_agent.models import ChatMessage, DialoguePlan
    from hanser_agent.persona.schemas import TurnSignals, SignalObservation
    plan = DialoguePlan(intent='chitchat', need_wiki=False, standalone_query='随便聊聊', keywords=[],
        response_mode='casual', fact_sensitivity='low', target_length='short')
    profile = load_profile()
    assert '[QQ SPEECH HABITS ' in trial_guidance(profile, '随便聊聊', [], plan, None)
    assert not trial_guidance(profile, '查询事实', [], plan.model_copy(update={'need_wiki': True}), None)
    assert not trial_guidance(profile, '认真谈谈', [], plan.model_copy(update={'response_mode': 'emotional'}), None)
    assert not trial_guidance(profile, '只输出 pip install -r requirements.txt --no-cache-dir', [], plan, None)
    assert not trial_guidance(profile, '请原样保留“Hello, World!”', [], plan, None)
    signals = TurnSignals(values={'distress': SignalObservation(name='distress', value=True, source='rule', status='observed')})
    assert not trial_guidance(profile, '我很难受', [], plan, signals)
    history = [ChatMessage(role='assistant', content='哎呀 我觉得可以')]
    guidance = trial_guidance(profile, '继续聊聊', history, plan, None)
    assert '哎呀（' not in guidance and '我觉得（' not in guidance and '诶（' in guidance
    history.append(ChatMessage(role='assistant', content=' '.join(item['phrase'] for item in profile['habits'])))
    assert not trial_guidance(profile, '继续聊聊', history, plan, None)


def test_trial_voice_is_in_budgeted_model_context_and_replay_keeps_one_turn(config, tmp_path):
    original, calls = app_with_models(config, tmp_path, reply_text='哎呀，终于跑起来了，这半天没白折腾啊。')
    from hanser_qq.text_core import configure_text_core
    settings = original.state.agent.style_tool.settings
    agent = configure_text_core(original.state.agent, settings, user_trial=True, speech_habits_enabled=True)
    app = create_app(config, agent=agent, settings=settings)
    body = {'user_id': 'qq:100:200', 'conversation_id': 'qq:dm:100:200',
            'request_id': 'voice-trial', 'message': '今天折腾了半天，终于把程序跑起来了'}
    with TestClient(app) as client:
        headers = {'X-Hanser-QQ-Token': config.backend.token}
        first = client.post('/v1/chat', headers=headers, json=body)
        assert first.status_code == 200, first.text
        assert first.json()['text'] == '哎呀 终于跑起来了 这半天没白折腾啊'
        prompts = [call['messages'][0]['content'] for call in calls if not call.get('response_format')]
        assert any('[QQ SPEECH HABITS qq-speech-habits-trial-v1;' in prompt for prompt in prompts)
        # Only the curated phrase palette enters the prompt, not the source's biography.
        assert all('草莓什么代好喝' not in prompt and '熊猫烧香时期' not in prompt for prompt in prompts)
        count = len(calls)
        assert client.post('/v1/chat', headers=headers, json=body).json() == first.json()
        assert len(calls) == count
        assert client.get('/health', headers=headers).json()['speech_habits']['enabled']
    formal = configure_text_core(original.state.agent, settings, user_trial=False, speech_habits_enabled=True)
    assert formal.context_builder.speech_habits_profile is None
    disabled = configure_text_core(original.state.agent, settings, user_trial=True, speech_habits_enabled=False)
    assert disabled.context_builder.speech_habits_profile is None


def test_trial_removes_only_arbitrary_twelve_character_reaction_limit(config, tmp_path):
    app, _ = app_with_models(config, tmp_path)
    from hanser_qq.text_core import QQValidator
    from hanser_agent.persona.schemas import TurnSignals, SignalObservation
    validator = QQValidator(app.state.agent.context_builder.persona_compiler.prompt_dir / 'style_constraints.yaml')
    signals = TurnSignals(values={'interaction_frame': SignalObservation(name='interaction_frame',
        value='achievement_share', source='rule', status='observed')})
    text = '哎呀，终于跑起来了，这半天没白折腾啊。'
    assert 'achievement_reaction_overelaborated' in validator.validate_semantic_output(text, turn_signals=signals).violations
    validator.trial_reaction_lengths = True
    result = validator.validate_semantic_output(text, turn_signals=signals)
    assert not result.violations and 'qq_trial_reaction_length_relaxed' in result.actions
    question = validator.validate_semantic_output('之前试了多少次才弄好的？', turn_signals=signals)
    assert 'achievement_followup_not_grounded' in question.violations
    exact = validator.validate_semantic_output(text, exact_output='hello', turn_signals=signals)
    assert 'exact_output_mismatch' in exact.violations
