import hashlib
from pathlib import Path

import pytest

from hanser_qq.releases import publish_release, read_json, write_json
from hanser_qq.runtime_update import apply_staged_runtime_update, stage_runtime_update


def test_staged_update_preserves_trial_and_consumes_exact_prepared_record(tmp_path, monkeypatch):
    runtime = tmp_path / '.runtime'
    runtime.mkdir()
    previous = {'release_id': 'existing', 'mode': 'user_trial', 'formal_approval': False,
                'evaluation_decision': 'HOLD', 'evaluation': str(tmp_path / 'evaluation.json')}
    write_json(runtime / 'active_release.json', previous)
    import hanser_qq.runtime_update as update
    monkeypatch.setattr(update, 'runtime_engine_hashes', lambda: {'engine': 'checked-version'})
    assert stage_runtime_update(tmp_path, authority='user-request-current-audio-update',
                                validation='offline audio assembly checks')['staged']
    pending = read_json(runtime / 'pending_reply_controls.json')
    assert pending['authority'] == 'user-request-current-audio-update'
    assert pending['validation'] == 'offline audio assembly checks'
    assert read_json(runtime / 'active_release.json') == previous
    calls = []
    def publish(root, release_id, evaluation, **kwargs):
        calls.append((root, release_id, evaluation, kwargs))
        return {'runtime_refreshed': True}
    monkeypatch.setattr(update, 'publish_release', publish)
    assert apply_staged_runtime_update(tmp_path)['runtime_refreshed']
    assert calls == [(tmp_path, 'existing', Path(previous['evaluation']), {
        'user_trial': True, 'expected_engine': {'engine': 'checked-version'}, 'expected_active': previous})]
    assert not (runtime / 'pending_reply_controls.json').exists()
    assert (runtime / 'applied_reply_controls.json').exists()
    assert apply_staged_runtime_update(tmp_path) is None


def test_publication_rejects_changed_code_or_changed_active_state(tmp_path):
    target = tmp_path / 'releases/test'
    target.mkdir(parents=True)
    payload = b'[]'
    (target / 'style_rows.json').write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    write_json(target / 'release.json', {'release_id': 'test', 'payload_sha256': digest})
    evaluation = tmp_path / 'evaluation.json'
    write_json(evaluation, {'release_id': 'test', 'decision': 'HOLD', 'payload_sha256': digest})
    runtime = tmp_path / '.runtime'
    runtime.mkdir()
    active = {'release_id': 'test', 'mode': 'user_trial', 'evaluation_decision': 'HOLD'}
    write_json(runtime / 'active_release.json', active)
    with pytest.raises(ValueError, match='code changed'):
        publish_release(tmp_path, 'test', evaluation, user_trial=True, expected_engine={'changed': 'version'})
    with pytest.raises(ValueError, match='release changed'):
        publish_release(tmp_path, 'test', evaluation, user_trial=True, expected_active={'different': 'selection'})
    assert read_json(runtime / 'active_release.json') == active
    assert not (runtime / 'hanser.db').exists()
