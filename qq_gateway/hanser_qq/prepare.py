from __future__ import annotations

import json
import secrets
import shutil
import sqlite3
from pathlib import Path

import yaml

from .locking import service_lock


def enable_plain_speech(backend: Path):
    """QQ's neutral TTS consumes validated semantic text without JSON performance generation."""
    path = backend / 'hanser_agent/responder/performance.py'
    text = path.read_text(encoding='utf-8')
    old = '    ) if structured_enabled else OutputPreferences()'
    if old in text:
        path.write_text(text.replace(old, '    )  # QQ: speech is independent of structured performance output'), encoding='utf-8')
    baseline = backend / 'baseline.json'
    if baseline.exists():
        value = json.loads(baseline.read_text(encoding='utf-8'))
        value['qq_plain_speech_boundary'] = 'neutral-tts-independent-of-structured-v1'
        baseline.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


RUNTIME_TABLES = (
    "conversation_summaries", "continuity_tokens", "continuity_turns", "continuity_checkpoints",
    "conversations", "evaluation_outputs", "evaluation_runs", "memories", "messages",
    "persona_permission_events", "post_turn_commits", "post_turn_failures", "relationship_states",
    "reply_snapshots", "request_executions", "scene_states", "voice_job_links",
)


def export_clean_seed(source: Path, destination: Path, generation: str):
    # Only the supplied clean resource seed is read, never a desktop user's live database.
    source = source.resolve()
    if source == destination.resolve():
        raise ValueError("seed destination must differ from source")
    src = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
        tables = {row[0] for row in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        with dst:
            for table in RUNTIME_TABLES:
                if table in tables:
                    dst.execute(f'DELETE FROM "{table}"')
        active = dst.execute("SELECT generation FROM active_index_generations WHERE collection='style_examples'").fetchone()
        if active is None or active[0] != generation:
            raise ValueError("resource seed and frozen Persona Style generation disagree")
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("QQ seed integrity check failed")
    finally:
        src.close()
        dst.close()


def prepare(root: Path, *, source_root: Path | None = None, refresh_backend: bool = False) -> dict:
    root = root.resolve()
    source_root = (source_root or root.parent / "chat_voice_desktop").resolve()
    runtime = root / ".runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    config = root / "config.yml"
    if not config.exists():
        raw = yaml.safe_load((root / "config.example.yml").read_text(encoding="utf-8"))
        raw["gateway"]["onebot_access_token"] = secrets.token_urlsafe(32)
        raw["gateway"]["admin_token"] = secrets.token_urlsafe(32)
        raw["backend"]["token"] = secrets.token_urlsafe(32)
        config.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    if not (root / "hanser.yml").exists():
        shutil.copy2(root / "hanser.example.yml", root / "hanser.yml")
    backend = runtime / "backend"
    with service_lock(runtime / "backend.lock"):
        if not backend.exists() or refresh_backend:
            staging = runtime / "backend.staging"
            # These are fixed generated paths within this product, never a computed user path.
            if staging.resolve().parent != runtime.resolve():
                raise ValueError("staging directory is outside the QQ runtime")
            if staging.exists():
                shutil.rmtree(staging)
            shutil.copytree(source_root / "backend" / "hanser_agent", staging / "hanser_agent",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            enable_plain_speech(staging)
            pointer_path = staging / "hanser_agent/prompts/persona/releases/production.json"
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            desktop_pointer = dict(pointer)
            export_clean_seed(source_root / "resources/database/documents.seed.db",
                              staging / "documents.seed.db", pointer["style_generation"])
            from .releases import active_release, read_json, release_path, migrate_style, install_pointer
            selected = active_release(root)
            if selected:
                release = release_path(root, selected["release_id"])
                manifest = read_json(release / "release.json")
                migrate_style(staging / "documents.seed.db", read_json(release / "style_rows.json"), manifest)
                install_pointer(staging, root, manifest)
                pointer = read_json(pointer_path)
            shutil.copy2(source_root / "resources/userdict.txt", staging / "userdict.txt")
            (staging / "baseline.json").write_text(json.dumps({
                "source_root": str(source_root), "persona": pointer,
                "desktop_persona": desktop_pointer, "qq_release": selected,
                "copy_policy": "explicit source snapshot; existing QQ user DB is preserved",
                "qq_plain_speech_boundary": "neutral-tts-independent-of-structured-v1",
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            previous = runtime / "backend.previous"
            if previous.resolve().parent != runtime.resolve():
                raise ValueError("previous backend is outside the QQ runtime")
            if (runtime / "hanser.db").exists():
                with sqlite3.connect((runtime / "hanser.db").as_uri() + "?mode=ro", uri=True) as live:
                    active = live.execute("SELECT generation FROM active_index_generations WHERE collection='style_examples'").fetchone()
                if not active or active[0] != pointer["style_generation"]:
                    raise ValueError("existing QQ database needs an explicit compatible Style migration before backend refresh")
            if previous.exists():
                shutil.rmtree(previous)
            if backend.exists():
                backend.rename(previous)
            staging.rename(backend)
        if not (runtime / "hanser.db").exists():
            # Initialization only; upgrades never overwrite conversations or preferences.
            shutil.copy2(backend / "documents.seed.db", runtime / "hanser.db")
        if not (runtime / "userdict.txt").exists():
            shutil.copy2(backend / "userdict.txt", runtime / "userdict.txt")
    return {"config": str(config), "backend_path": str(backend), "runtime_database": str(runtime / "hanser.db")}
