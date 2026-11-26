"""Explicit QQ Voice snapshot and owned sidecar, without starting the desktop UI."""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path

import yaml

from .config import load_settings
from .locking import service_lock


def prepare_voice(root: Path) -> dict:
    settings = load_settings(root / "config.yml")
    target = settings.voice_runtime.package_path
    source = root.parent / "chat_voice_desktop" / "voice_runtime"
    if target != root / ".runtime/voice":
        raise ValueError("voice-prepare writes only the QQ-owned .runtime/voice directory")
    if not settings.voice_runtime.python_path.is_file():
        raise ValueError("configure an existing VoxCPM2 Python environment first")
    with service_lock(settings.storage_path.parent / "run.lock"):
        if target.exists():
            raise ValueError("Voice snapshot already exists; retain it or explicitly move it aside before preparing again")
        from .prepare import enable_plain_speech
        with service_lock(settings.storage_path.parent / 'backend.lock'):
            enable_plain_speech(settings.backend.package_path)
        override = os.getenv("HANSER_VOICE_PROFILE")
        profile_path = Path(override).resolve() if override else source / "config/voice_profile.local.yml"
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        profile["model"]["local_path"] = str((profile_path.parents[1] / profile["model"]["local_path"]).resolve())
        wanted = {profile["clone"].get(key) for key in ("identity_reference_id", "default_style_prompt_id")} - {None}
        if wanted:
            manifest = (profile_path.parent / profile["assets_manifest"]).resolve()
            asset_root = manifest.parent
            assets = []
            for line in manifest.read_text(encoding="utf-8").splitlines():
                item = json.loads(line)
                if item["asset_id"] not in wanted:
                    continue
                if item["review_status"] != "approved":
                    raise ValueError("QQ requires approved voice references")
                destination = (asset_root / item["path"]).resolve()
                if not destination.is_relative_to(asset_root) or not destination.is_file():
                    raise ValueError("approved voice reference is missing from its project assets")
                assets.append({**item, "path": str(destination)})
            if {item["asset_id"] for item in assets} != wanted:
                raise ValueError("approved identity/style references missing")
        target.mkdir(parents=True)
        shutil.copytree(source, target / "voice_runtime", ignore=shutil.ignore_patterns(
            ".venv", "models", "data", "tests", "scripts", "__pycache__", "*.pyc"))
        if wanted:
            copied_manifest = target / "approved-assets.jsonl"
            copied_manifest.write_text(
                "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in assets), encoding="utf-8"
            )
            profile["assets_manifest"] = str(copied_manifest)
        (target / "voice_runtime/config/voice_profile.qq.yml").write_text(
            yaml.safe_dump(profile, allow_unicode=True, sort_keys=False), encoding='utf-8')
        (target / 'baseline.json').write_text(json.dumps({
            'source': str(source), 'profile_revision': profile['profile_revision'],
            'model_path': profile['model']['local_path'], 'references': sorted(wanted),
            'copy_policy': 'explicit QQ snapshot; shared read-only model and approved project assets; independent jobs/cache',
        }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        raw = yaml.safe_load((root / 'config.yml').read_text(encoding='utf-8'))
        raw.setdefault('voice_runtime', {})['token'] = settings.voice_runtime.token or secrets.token_urlsafe(32)
        (root / 'config.yml').write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding='utf-8')
    return {'prepared': True, 'package_path': str(target), 'profile_revision': profile['profile_revision']}


def start_voice(settings, log):
    runtime = settings.voice_runtime
    profile = runtime.package_path / "voice_runtime/config/voice_profile.qq.yml"
    if not profile.is_file() or not runtime.python_path.is_file() or not runtime.token:
        raise ValueError("Voice is not prepared; run python -m hanser_qq voice-prepare")
    env = dict(os.environ, PYTHONUTF8='1', PYTHONUNBUFFERED='1',
        # This Windows torch_cpu.dll build crashed during parallel weight
        # loading. A single CPU thread loaded the same checkpoint successfully;
        # synthesis still uses the configured CUDA device.
        OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', PYTHONFAULTHANDLER='1',
        PYTHONPATH=os.pathsep.join([str(Path(__file__).resolve().parents[1]), str(runtime.package_path)]),
        HANSER_VOICE_HOST='127.0.0.1', HANSER_VOICE_PORT=str(runtime.port),
        HANSER_VOICE_INTERNAL_TOKEN=runtime.token, HANSER_VOICE_LOAD_MODEL='true',
        HANSER_VOICE_PROFILE=str(profile),
        HANSER_QQ_COMMA_PAUSE_MIN_MS=str(settings.gateway.voice_reply.comma_pause_min_ms),
        HANSER_QQ_COMMA_PAUSE_MAX_MS=str(settings.gateway.voice_reply.comma_pause_max_ms),
        HANSER_RIG_PROFILE=str(runtime.package_path / 'voice_runtime/config/rig_profile.disabled.yml'),
        HANSER_VOICE_DATA_ROOT=str(settings.storage_path.parent / 'voice_data'),
        HANSER_VOICE_OFFLINE_DEADLINE=str(settings.gateway.voice_reply.timeout_seconds))
    return subprocess.Popen([str(runtime.python_path), '-m', 'hanser_qq.voice_entry'],
        cwd=runtime.package_path, env=env, stdout=log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
