"""One prepared owner-authorized code update, applied before services start."""
from pathlib import Path

from .releases import active_release, publish_release, read_json, runtime_engine_hashes, write_json


def stage_runtime_update(root: Path, *,
                         authority='user-request-2026-10-30-reply-controls-no-model-calls',
                         validation='focused deterministic and HTTP fixture tests; no model inference or acoustic evaluation'):
    previous = active_release(root)
    if not previous or previous.get('mode') != 'user_trial':
        raise ValueError('reply controls update requires the existing user trial; no new release is activated')
    record = {
        'authority': authority,
        'previous_active': previous,
        'expected_engine': runtime_engine_hashes(),
        'validation': validation,
    }
    write_json(root / '.runtime/pending_reply_controls.json', record)
    return {'staged': True, 'release_id': previous['release_id'], 'applies': 'next stopped-service startup'}


def apply_staged_runtime_update(root: Path):
    pending = root / '.runtime/pending_reply_controls.json'
    if not pending.exists():
        return None
    record = read_json(pending)
    previous = record['previous_active']
    if previous.get('mode') != 'user_trial':
        raise ValueError('staged QQ update cannot promote an evaluated release')
    result = publish_release(root, previous['release_id'], Path(previous['evaluation']), user_trial=True,
                             expected_engine=record['expected_engine'], expected_active=previous)
    write_json(root / '.runtime/applied_reply_controls.json', {**record, 'result': result})
    pending.unlink()
    return result
