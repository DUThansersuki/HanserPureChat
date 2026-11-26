from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from uuid import uuid4
from pathlib import Path

import httpx

from .config import load_settings
from .locking import service_lock


ROOT = Path(__file__).resolve().parents[1]


def serve_gateway(path: Path):
    import nonebot
    from nonebot.adapters.onebot.v11 import Adapter
    settings = load_settings(path)
    with service_lock(settings.storage_path.parent / "gateway.lock"):
        nonebot.init(driver="~fastapi", host=settings.gateway.host, port=settings.gateway.port,
                     log_level="WARNING", onebot_access_token=settings.gateway.onebot_access_token,
                     hanser_qq_config=str(path), nickname=set(), api_timeout=30)
        nonebot.get_driver().register_adapter(Adapter)
        plugin = nonebot.load_plugin("hanser_qq.plugin")
        if plugin is None:
            raise RuntimeError("QQ plugin failed to load")
        nonebot.run()


def serve_backend(path: Path):
    import uvicorn
    from .headless import create_app
    settings = load_settings(path)
    with service_lock(settings.storage_path.parent / "backend.lock"):
        uvicorn.run(create_app(settings, prepare_models=True), host=settings.backend.host, port=settings.backend.port,
                    log_level="warning", access_log=False)


def status(path: Path):
    settings = load_settings(path)
    with httpx.Client(trust_env=False, timeout=5) as client:
        response = client.get(f"http://127.0.0.1:{settings.gateway.port}/health",
                              headers={"X-Hanser-QQ-Admin-Token": settings.gateway.admin_token})
        response.raise_for_status()
        return response.json()


def chat(path: Path, query: str | None = None):
    """Local effect check through the running backend; never sends to QQ."""
    settings = load_settings(path)
    with httpx.Client(trust_env=False, timeout=120,
                      headers={"X-Hanser-QQ-Token": settings.backend.token}) as client:
        def send(message):
            response = client.post(f"http://127.0.0.1:{settings.backend.port}/v1/chat", json={
                "user_id": "qq:0:0", "conversation_id": "qq:dm:0:0",
                "request_id": "local-check-" + uuid4().hex, "message": message})
            response.raise_for_status()
            print(response.json()["text"])
        if query is not None:
            send(query)
            return
        print("Local chat (test identity qq:0:0). /quit exits.")
        while True:
            try:
                message = input("You> ")
            except (EOFError, KeyboardInterrupt):
                return
            if message.strip() == "/quit":
                return
            if message.strip():
                send(message)


def run(path: Path):
    if os.name == "nt":
        def stop_on_break(signum, frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGBREAK, stop_on_break)
    settings = load_settings(path)
    runtime = settings.storage_path.parent
    from .runtime_update import apply_staged_runtime_update
    update = apply_staged_runtime_update(runtime.parent)
    if update:
        print('Applied the prepared QQ reply controls update; original user-trial evaluation is retained.')
    from .model_routes import start_responder_gateway
    start_responder_gateway(settings)
    processes, files = [], []
    voice_process = None
    with service_lock(runtime / "run.lock"):
        try:
            if settings.gateway.voice_reply.enabled:
                from .voice_runtime import start_voice
                log = (runtime / 'voice.log').open('ab')
                files.append(log)
                voice_process = start_voice(settings, log)
            # Secrets stay in the local config/env, never in command-line arguments.
            for service in ("backend", "gateway"):
                log = (runtime / f"{service}.log").open("ab")
                files.append(log)
                env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1")
                processes.append(subprocess.Popen([sys.executable, "-m", "hanser_qq", service, "--config", str(path)],
                                                    cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT))
                url = settings.backend.url if service == "backend" else f"http://127.0.0.1:{settings.gateway.port}"
                headers = ({"X-Hanser-QQ-Token": settings.backend.token} if service == "backend"
                           else {"X-Hanser-QQ-Admin-Token": settings.gateway.admin_token})
                deadline = time.monotonic() + (3660 if service == "backend" else 45)
                with httpx.Client(trust_env=False, timeout=2) as client:
                    while True:
                        if processes[-1].poll() is not None:
                            raise RuntimeError(f"{service} exited; see .runtime/{service}.log")
                        try:
                            response = client.get(url + "/health", headers=headers)
                            if response.status_code == 200:
                                health = response.json()
                                if service == "backend" and not health.get("chat_ready"):
                                    raise RuntimeError("QQ backend Persona/Style combination is not ready")
                                break
                        except httpx.RequestError:
                            pass
                        if time.monotonic() > deadline:
                            raise RuntimeError(f"{service} startup timed out; see .runtime/{service}.log")
                        time.sleep(0.25)
            print(f"Hanser QQ ready; reverse WS: ws://127.0.0.1:{settings.gateway.port}/onebot/v11/ws")
            print("Use 'status' to inspect QQ connection. Ctrl+C stops all owned services.")
            voice_reported = False
            while all(process.poll() is None for process in processes):
                if voice_process and voice_process.poll() is not None and not voice_reported:
                    print('Voice runtime exited; replies use text until restart. See .runtime/voice.log')
                    voice_reported = True
                time.sleep(0.5)
            raise RuntimeError("a QQ service exited; inspect .runtime logs")
        except KeyboardInterrupt:
            print("Stopping Hanser QQ...")
        finally:
            for process in reversed(([voice_process] if voice_process else []) + processes):
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            for log in files:
                log.close()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Independent Hanser QQ services")
    parser.add_argument("command", choices=("prepare", "backend", "gateway", "run", "status", "retry", "check", "chat",
                                            "proactive-preview", "voice-prepare",
                                            "corpus-build", "corpus-status", "corpus-search",
                                            "release-build", "release-publish", "release-rollback"))
    parser.add_argument("--config", type=Path, default=ROOT / "config.yml")
    parser.add_argument("--refresh-backend", action="store_true")
    parser.add_argument("--request-id")
    parser.add_argument("--allow-unknown", action="store_true", help="explicitly retry an uncertain QQ send; may duplicate a message")
    parser.add_argument("--bundle", type=Path, default=ROOT.parent / "audit_artifacts/deepseek_a13_full_v2_20261016_001")
    parser.add_argument("--source", type=Path, default=ROOT.parent / "audit_artifacts/local_corpus_full_20261013_004")
    parser.add_argument("--query")
    parser.add_argument("--conversation-id", help="existing QQ private conversation for a proactive preview; no QQ send")
    parser.add_argument("--label", type=int, choices=range(1, 14))
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--include-not-recommended", action="store_true")
    parser.add_argument("--release-id")
    parser.add_argument("--evaluation", type=Path)
    parser.add_argument("--user-trial", action="store_true", help="explicitly activate pending full-corpus originals as a user trial; retain evaluation result")
    args = parser.parse_args()
    path = args.config.resolve()
    if args.command == "prepare":
        if path != ROOT / "config.yml":
            parser.error("prepare uses this product's default config path")
        from .prepare import prepare
        print(json.dumps(prepare(ROOT, refresh_backend=args.refresh_backend), ensure_ascii=False, indent=2))
    elif args.command == "gateway":
        serve_gateway(path)
    elif args.command == "voice-prepare":
        from .voice_runtime import prepare_voice
        if path != ROOT / 'config.yml':
            parser.error('voice-prepare uses this product default config')
        print(json.dumps(prepare_voice(ROOT), ensure_ascii=False, indent=2))
    elif args.command == "backend":
        serve_backend(path)
    elif args.command == "run":
        run(path)
    elif args.command == "status":
        print(json.dumps(status(path), ensure_ascii=False, indent=2))
    elif args.command == "chat":
        chat(path, args.query)
    elif args.command == "proactive-preview":
        settings = load_settings(path)
        conversation = args.conversation_id
        if conversation is None:
            if len(settings.gateway.bot_ids) != 1 or len(settings.gateway.private_users) != 1:
                parser.error('proactive-preview requires --conversation-id when there is no single configured private target')
            conversation = f'qq:dm:{settings.gateway.bot_ids[0]}:{settings.gateway.private_users[0]}'
        import re
        match = re.fullmatch(r'qq:dm:(\d+):(\d+)', conversation)
        if not match:
            parser.error('proactive-preview only accepts a QQ private conversation')
        with httpx.Client(trust_env=False, timeout=settings.gateway.backend_timeout_seconds) as client:
            response = client.post(settings.backend.url + '/v1/proactive/preview',
                headers={'X-Hanser-QQ-Token': settings.backend.token}, json={
                    'request_id': 'qq-proactive-preview-' + uuid4().hex,
                    'user_id': f'qq:{match[1]}:{match[2]}', 'conversation_id': conversation})
            response.raise_for_status()
            print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    elif args.command == "check":
        from .readiness import check_prepared
        result = check_prepared(load_settings(path))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["production"]["chat_ready"]:
            raise SystemExit(1)
    elif args.command.startswith("corpus-"):
        from .corpus import build_corpus, corpus_status, search_corpus
        settings = load_settings(path)
        corpus_path = settings.storage_path.parent / "full_corpus.sqlite3"
        if args.command == "corpus-build":
            build_corpus(args.bundle, args.source, corpus_path)
            result = corpus_status(corpus_path)
        elif args.command == "corpus-status":
            result = corpus_status(corpus_path)
        else:
            if not args.query:
                parser.error("corpus-search requires --query")
            if not 1 <= args.limit <= 50:
                parser.error("--limit must be 1..50")
            result = search_corpus(corpus_path, args.query, limit=args.limit, label=args.label,
                                   include_not_recommended=args.include_not_recommended)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command.startswith("release-"):
        from .releases import build_release, publish_release, rollback_release
        if args.command == "release-rollback":
            result = rollback_release(ROOT)
        else:
            if not args.release_id:
                parser.error("release operation requires --release-id")
            if args.command == "release-build":
                result = build_release(ROOT, args.release_id)
            else:
                if not args.evaluation:
                    parser.error("release-publish requires --evaluation")
                result = publish_release(ROOT, args.release_id, args.evaluation, user_trial=args.user_trial)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "retry":
        if not args.request_id:
            parser.error("retry requires --request-id")
        settings = load_settings(path)
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.post(f"http://127.0.0.1:{settings.gateway.port}/v1/batches/{args.request_id}/retry",
                                   headers={"X-Hanser-QQ-Admin-Token": settings.gateway.admin_token},
                                   json={"allow_unknown": args.allow_unknown})
            response.raise_for_status()
            print(json.dumps(response.json(), ensure_ascii=False))


if __name__ == "__main__":
    main()
