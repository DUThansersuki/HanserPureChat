import asyncio
import sys
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from .config import Settings
from .corpus import corpus_status, search_corpus
from .readiness import production_status


def install_backend(package_path: Path):
    package_path = package_path.resolve()
    if not (package_path / "hanser_agent" / "__init__.py").is_file():
        raise ValueError("backend snapshot missing; run prepare first")
    if not (package_path / "baseline.json").is_file():
        raise ValueError("QQ requires an explicitly prepared backend snapshot, not a desktop source import")
    loaded = sys.modules.get("hanser_agent")
    if loaded is not None and not Path(loaded.__file__).resolve().is_relative_to(package_path):
        raise RuntimeError("another Hanser backend is already imported into this process")
    if str(package_path) not in sys.path:
        sys.path.insert(0, str(package_path))


def create_app(config: Settings, *, agent=None, gateway=None, settings=None, prepare_models=False) -> FastAPI:
    install_backend(config.backend.package_path)
    from hanser_agent import db
    from hanser_agent.config import load_settings
    from hanser_agent.desktop_api import build_desktop_chat_agent
    from hanser_agent.agent.conversation import ConversationOwnershipError
    from hanser_agent.agent.recovery import recover_post_turn_in_background
    from hanser_agent.failures import ServiceFailure
    from hanser_agent.memory.store import MemoryStore
    from hanser_agent.model_gateway import build_model_gateway
    from hanser_agent.models import ChatRequest, ChatResponse
    from hanser_agent.persona.packages import loaded_persona_status

    class QQChatRequest(ChatRequest):
        # Required QQ namespace, including independent group identities.
        user_id: str = Field(pattern=r"^qq:\d+:(?:\d+|group:\d+:member:\d+)$")
        conversation_id: str = Field(pattern=r"^qq:(?:dm:\d+:\d+|group:\d+:\d+:member:\d+)$")
        request_id: str = Field(min_length=1, max_length=128)
        message: str = Field(min_length=1, max_length=32000)
        speech_required: bool = False

    class QQChatResponse(ChatResponse):
        segments: list[str] | None = None
        voice: dict | None = None
        voices: list[dict] | None = None
        voice_status: str | None = None
        voice_reason: str | None = None

    active_settings = settings or load_settings(config.backend.config_path)
    from .model_routes import apply_responder_gateway, model_route_status
    apply_responder_gateway(active_settings, config)
    if prepare_models:
        from hanser_agent.local_models import ensure_ollama_models
        ensure_ollama_models(active_settings.planner, active_settings.responder)
    # A QQ product must never write the source seed or a desktop database.
    data_root = config.storage_path.parent.resolve()
    if not active_settings.db_path.resolve().is_relative_to(data_root):
        raise ValueError("QQ database must be inside the independent runtime data directory")
    active_settings.performance.speech_runtime_enabled = config.gateway.voice_reply.enabled
    active_settings.performance.voice_runtime_base_url = config.voice_runtime.url
    active_settings.performance.internal_token = config.voice_runtime.token
    active_settings.performance.render_profile_revision = "hanser-qq-neutral-v1"
    with db.connect(active_settings.db_path) as conn:
        db.init_db(conn)
    owns_gateway = agent is None and gateway is None
    model_gateway = gateway
    if agent is None:
        model_gateway = model_gateway or build_model_gateway(active_settings)
        agent = build_desktop_chat_agent(active_settings, model_gateway, MemoryStore(active_settings.db_path))
    loaded = loaded_persona_status(agent)
    selector_path = data_root / "active_release.json"
    runtime_release = None
    if selector_path.exists():
        from .releases import read_json, release_path, validate_engine
        from .runtime_style import attach_release_engine
        runtime_release = read_json(selector_path)
        validate_engine(runtime_release)
        release_manifest = read_json(release_path(Path(__file__).resolve().parents[1], runtime_release["release_id"]) / "release.json")
        if loaded.get("loaded_style_generation") != release_manifest["style_generation"]:
            raise ValueError("QQ active release and loaded Persona disagree")
        agent = attach_release_engine(agent, active_settings, release_manifest,
                                      data_root / "releases" / runtime_release["release_id"] / "corpus.sqlite3",
                                      user_trial=runtime_release.get("mode") == "user_trial",
                                      speech_habits_enabled=config.gateway.speech_habits_enabled,
                                      expression_polish_enabled=config.gateway.expression_polish_enabled,
                                      rag_selector=config.rag_selector)
    from .retrieval_audit import attach_audit, ACTIVE_AUDIT, write_audit
    attach_audit(agent)
    production = production_status(active_settings, loaded)
    corpus_path = data_root / "full_corpus.sqlite3"
    from .proactive_backend import ProactiveService, ProactiveRequest, ProactiveAck
    initiative = ProactiveService(agent, config, active_settings)
    from .voice import QQVoiceRenderer
    voice = QQVoiceRenderer(agent.conversations, active_settings.performance, config)

    @asynccontextmanager
    async def lifespan(app):
        recovery = asyncio.create_task(recover_post_turn_in_background(agent)) if hasattr(agent, "pending_post_turn_failures") else None
        try:
            yield
        finally:
            await initiative.close()
            await voice.close()
            if recovery:
                recovery.cancel()
                with suppress(asyncio.CancelledError):
                    await recovery
            if owns_gateway and model_gateway:
                await model_gateway.close()

    app = FastAPI(title="Hanser QQ Backend", lifespan=lifespan)

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        import secrets
        if not secrets.compare_digest(request.headers.get("X-Hanser-QQ-Token", ""), config.backend.token):
            return JSONResponse(status_code=401, content={"detail": "QQ backend token required"})
        return await call_next(request)

    @app.exception_handler(ServiceFailure)
    async def failure(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"detail": {
            "code": exc.code, "message": exc.safe_message, "retryable": exc.retryable, "trace_id": exc.trace_id}})

    @app.exception_handler(ConversationOwnershipError)
    async def ownership(request, exc):
        return JSONResponse(status_code=409, content={"detail": {"code": "conversation_owner_conflict", "retryable": False}})

    @app.get("/health")
    async def health():
        nonlocal production
        with db.connect(active_settings.db_path) as conn:
            row = conn.execute("SELECT generation,revision FROM active_index_generations WHERE collection='style_examples'").fetchone()
        current = (row[0], row[1]) if row else (None, None)
        if current != (production["style_generation"], production["style_revision"]):
            production = production_status(active_settings, loaded)
        habits = getattr(agent.context_builder, "speech_habits_profile", None)
        return {"ok": True, "mode": "qq-headless", "chat_ready": production["chat_ready"],
                "production": production, "corpus": corpus_status(corpus_path),
                "speech_habits": {"enabled": bool(habits), "version": habits["version"] if habits else None,
                                  "formal_approval": False},
                "expression_polish": {"enabled": agent.responder.expression_polish_enabled
                                      if hasattr(agent.responder, 'expression_polish_enabled') else False,
                                      "version": "qq-expression-polish-v1", "formal_approval": False},
                "voice": await voice.status(),
                "model_routes": model_route_status(active_settings, config),
                "runtime_corpus": {"enabled": runtime_release is not None, "release": runtime_release}, **loaded}

    @app.get("/v1/maintenance/corpus/search")
    def corpus_search(q: str = Query(min_length=1, max_length=1000),
                      limit: int = Query(default=10, ge=1, le=50),
                      label: int | None = Query(default=None, ge=1, le=13),
                      include_not_recommended: bool = False):
        try:
            return search_corpus(corpus_path, q, limit=limit, label=label,
                                 include_not_recommended=include_not_recommended)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/v1/chat", response_model=QQChatResponse, response_model_exclude_none=True)
    async def chat(body: QQChatRequest):
        parts = body.conversation_id.split(":")
        expected_user = (f"qq:{parts[2]}:{parts[3]}" if parts[1] == "dm"
                         else f"qq:{parts[2]}:group:{parts[3]}:member:{parts[5]}")
        if body.user_id != expected_user:
            return JSONResponse(status_code=422, content={"detail": {"code": "qq_identity_mismatch", "retryable": False}})
        body.output_preferences.text = True
        body.output_preferences.speech = config.gateway.voice_reply.enabled and body.output_preferences.speech
        body.output_preferences.dynamic_live2d = False
        body.output_preferences.offline_performance = False
        body.persona_settings.adult_innuendo_opt_in = False
        await initiative.interrupt(body.user_id, body.conversation_id)
        from .text_core import INITIATIVE_CONTROL
        control = INITIATIVE_CONTROL.set(initiative.control_receipt(body))
        audit = {"searches": {name: {"status": "not_requested"} for name in ("style", "wiki", "memory")}}
        audit_token = ACTIVE_AUDIT.set(audit)
        try:
            response = await agent.send(body)
        finally:
            ACTIVE_AUDIT.reset(audit_token)
            INITIATIVE_CONTROL.reset(control)
        snapshot = agent.conversations.get_reply_snapshot(response.reply_id, user_id=body.user_id)
        write_audit(data_root / "retrieval_logs", body, snapshot, audit)
        additions = {}
        if config.gateway.segmented_reply.enabled:
            from .segmentation import render_reply_segments
            additions['segments'] = render_reply_segments(snapshot, body.message, response.text,
                config.gateway.segmented_reply, agent.responder.display_adapter)
        if body.output_preferences.speech:
            additions.update(await voice.render(response.reply_id, body.user_id))
        if body.speech_required and not additions.get("voices") and not additions.get("voice"):
            return JSONResponse(status_code=503, content={"detail": {
                "code": "voice_required_unavailable", "retryable": False,
                "message": "纯语音合成未完成，已保留回复快照；重试会复用文字结果。"}})
        return QQChatResponse(**response.model_dump(), **additions)

    @app.post('/v1/proactive/generate')
    async def proactive_generate(body: ProactiveRequest):
        return await initiative.generate(body)

    @app.post('/v1/proactive/preview')
    async def proactive_preview(body: ProactiveRequest):
        return await initiative.generate(body, preview=True)

    @app.post('/v1/proactive/validate')
    def proactive_validate(body: ProactiveRequest):
        return {'valid': initiative.valid(body)}

    @app.post('/v1/proactive/ack')
    async def proactive_ack(body: ProactiveAck):
        return await initiative.acknowledge(body)

    @app.get("/v1/maintenance/post-turn-failures")
    async def post_turn_failures():
        return {"failures": [{key: row.get(key) for key in ("id", "request_id", "status", "error_code")}
                             for row in agent.pending_post_turn_failures()]}

    @app.post("/v1/maintenance/post-turn-failures/{failure_id}/retry")
    async def retry_post_turn(failure_id: str):
        await agent.retry_post_turn(failure_id)
        return {"ok": True, "failure_id": failure_id}

    app.state.agent = agent
    app.state.voice = voice
    return app
