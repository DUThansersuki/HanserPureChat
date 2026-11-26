from __future__ import annotations

import os
import asyncio
from contextlib import suppress
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import db
from .agent import ChatAgentService
from .agent.context_builder import ContextBuilder
from .agent.conversation import ConversationOwnershipError, ConversationStore
from .agent.planner import DialoguePlanner
from .agent.request_state import RequestStateStore
from .agent.recovery import recover_post_turn_in_background
from .agent.tools.memory_search import MemorySearchTool
from .agent.tools.style_search import StyleSearchTool
from .agent.tools.wiki_search import WikiSearchTool
from .config import Settings, load_settings
from .failures import ServiceFailure
from .memory import (
    MemoryCandidateExtractor,
    MemoryRetriever,
    MemoryStore,
    MemoryWriteGate,
    PostTurnPipeline,
)
from .memory.state_engine import CharacterStateEngine
from .memory.summarizer import ConversationSummarizer
from .model_gateway import ModelGateway, build_model_gateway
from .memory_api import attach_memory_routes
from .models import ChatRequest, ChatResponse, VoiceJobCreateRequest
from .persona import PersonaCompiler
from .persona.packages import loaded_persona_status, resolve_persona_package
from .responder import HanserResponder, StyleValidator
from .retrieval import (
    BM25OnlyRetriever,
    BM25Reranker,
)
from .render_bridge import RenderBridge, RenderBridgeFailure


class ChatService(Protocol):
    async def send(self, request: ChatRequest) -> ChatResponse:
        ...


def build_desktop_chat_agent(
    settings: Settings,
    model_gateway: ModelGateway,
    memory_store: MemoryStore,
    *,
    persona_dir_override: Path | None = None,
) -> ChatAgentService:
    package_dir = Path(__file__).parent
    persona_root = package_dir / "prompts" / "persona"
    persona_dir = persona_dir_override or resolve_persona_package(persona_root, settings.persona.active_package)
    persona_compiler = PersonaCompiler(
        persona_dir,
        settings_lifecycle=(
            "production"
            if settings.persona.active_package == "persona-v2-production"
            else "preview"
        ),
    )
    if persona_compiler.is_v2 and not settings.style.reviewed_only:
        raise ValueError("Persona v2 requires style.reviewed_only=true")

    wiki_retriever = BM25OnlyRetriever(
        db_path=settings.db_path,
        userdict_path=settings.userdict_path,
        config=settings.retrieval,
    )
    wiki_tool = WikiSearchTool(
        settings=settings,
        reranker=BM25Reranker(),
        retriever=wiki_retriever,
    )
    style_tool = StyleSearchTool(
        settings=settings,
        strict_v2=persona_compiler.is_v2,
        pinned_generation=(
            persona_compiler.manifest.style_generation
            if persona_compiler.is_v2
            else None
        ),
    )
    memory_retriever = MemoryRetriever(
        store=memory_store,
        config=settings.memory,
        userdict_path=settings.userdict_path,
    )
    conversations = ConversationStore(
        settings.db_path,
        max_messages=settings.memory.recent_messages,
    )
    post_turn = PostTurnPipeline(
        store=memory_store,
        extractor=MemoryCandidateExtractor(),
        write_gate=MemoryWriteGate(settings.memory),
        retriever=memory_retriever,
        summarizer=ConversationSummarizer(
            conversations=conversations,
            memories=memory_store,
            config=settings.memory,
        ),
        state_engine=CharacterStateEngine(),
    )
    return ChatAgentService(
        conversations=conversations,
        planner=DialoguePlanner(model_gateway),
        wiki_tool=wiki_tool,
        style_tool=style_tool,
        memory_tool=MemorySearchTool(memory_retriever),
        memory_store=memory_store,
        post_turn=post_turn,
        context_builder=ContextBuilder(
            persona_compiler,
            settings.context,
            provider_context_window=settings.responder.context_window,
            provider_max_output_tokens=settings.responder.max_tokens,
        ),
        responder=HanserResponder(
            model_gateway=model_gateway,
            validator=StyleValidator(persona_dir / "style_constraints.yaml"),
            structured_performance_enabled=(
                settings.performance.structured_performance_enabled
            ),
        ),
        request_state=RequestStateStore(settings.db_path),
        performance_config=settings.performance,
    )


def create_desktop_app(
    *,
    settings: Settings | None = None,
    model_gateway: ModelGateway | None = None,
    chat_agent: ChatService | None = None,
    desktop_token: str | None = None,
    render_bridge: RenderBridge | None = None,
) -> FastAPI:
    active_settings = settings or load_settings(os.environ.get("HANSER_CONFIG"))
    expected_token = (
        desktop_token
        if desktop_token is not None
        else os.environ.get("HANSER_DESKTOP_TOKEN", "")
    )
    owned_model_gateway = model_gateway is None and chat_agent is None
    active_model_gateway = model_gateway

    with db.connect(active_settings.db_path) as conn:
        db.init_db(conn)

    memory_store = MemoryStore(active_settings.db_path)
    conversations = getattr(
        chat_agent,
        "conversations",
        ConversationStore(
            active_settings.db_path,
            max_messages=active_settings.memory.recent_messages,
        ),
    )
    active_render_bridge = render_bridge or RenderBridge(
        conversations,
        active_settings.performance,
    )
    if chat_agent is None:
        active_model_gateway = active_model_gateway or build_model_gateway(
            active_settings
        )
        chat_agent = build_desktop_chat_agent(
            active_settings,
            active_model_gateway,
            memory_store,
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        recovery = (
            asyncio.create_task(recover_post_turn_in_background(chat_agent))
            if hasattr(chat_agent, "pending_post_turn_failures") else None
        )
        try:
            yield
        finally:
            if recovery is not None:
                recovery.cancel()
                with suppress(asyncio.CancelledError):
                    await recovery
            if render_bridge is None:
                await active_render_bridge.close()
            if owned_model_gateway and active_model_gateway is not None:
                await active_model_gateway.close()

    application = FastAPI(
        title="Hanser Chat Voice Desktop Backend",
        version="0.1.0",
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def desktop_process_boundary(request: Request, call_next):
        if expected_token and request.headers.get("x-hanser-desktop-token") != expected_token:
            return JSONResponse(
                status_code=401,
                content={"detail": "desktop process token required"},
            )
        return await call_next(request)

    @application.exception_handler(ConversationOwnershipError)
    async def conversation_owner_conflict(_, exc: ConversationOwnershipError):
        return JSONResponse(
            status_code=409,
            content={
                "detail": "conversation_id is already owned by another user",
                "conversation_id": exc.conversation_id,
            },
        )

    @application.exception_handler(ServiceFailure)
    async def structured_service_failure(_, exc: ServiceFailure):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": {
                    "code": exc.code,
                    "message": exc.safe_message,
                    "retryable": exc.retryable,
                    "trace_id": exc.trace_id,
                }
            },
        )

    @application.get("/health")
    async def health() -> dict[str, object]:
        with db.connect(active_settings.db_path) as conn:
            active_style = conn.execute(
                "SELECT generation FROM active_index_generations WHERE collection=?",
                ("style_examples",),
            ).fetchone()
        active_style_generation = active_style["generation"] if active_style else None
        loaded = loaded_persona_status(chat_agent)
        expected_style_generation = loaded.get("loaded_style_generation")
        if not loaded:
            expected_style_generation = (
                active_settings.persona.candidate_style_generation
                if active_settings.persona.active_package != "persona-v1-production" else None
            )
        return {
            "ok": True,
            "chat_ready": (
                expected_style_generation is None
                or active_style_generation == expected_style_generation
            ),
            "mode": "chat-voice-desktop",
            "retrieval_profile": "external-llm-plus-lexical",
            "local_models_loaded": False,
            "model": active_settings.default_model,
            "persona_package": active_settings.persona.active_package,
            **loaded,
            "style_generation": expected_style_generation or "legacy-v1",
            "active_style_generation": active_style_generation,
        }

    @application.post("/v1/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest) -> ChatResponse:
        request.output_preferences.text = True
        request.output_preferences.dynamic_live2d = False
        request.output_preferences.offline_performance = False
        request.render_profile_revision = (
            active_settings.performance.render_profile_revision
        )
        return await chat_agent.send(request)

    @application.get("/v1/maintenance/post-turn-failures")
    async def pending_post_turn_failures() -> list[dict[str, object]]:
        return chat_agent.pending_post_turn_failures() if hasattr(chat_agent, "pending_post_turn_failures") else []

    @application.post("/v1/maintenance/post-turn-failures/{failure_id}/retry")
    async def retry_post_turn_failure(failure_id: str) -> dict[str, object]:
        if not hasattr(chat_agent, "retry_post_turn"):
            raise HTTPException(status_code=404, detail="post-turn failure not found")
        try:
            return await chat_agent.retry_post_turn(failure_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="post-turn failure not found") from exc

    @application.get("/v1/voice/runtime")
    async def voice_runtime(user_id: str = "local-user") -> dict[str, object]:
        try:
            return await active_render_bridge.get_runtime(user_id)
        except RenderBridgeFailure as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    @application.post("/v1/voice/jobs", status_code=202)
    async def create_voice_job(body: VoiceJobCreateRequest) -> dict[str, object]:
        try:
            result = await active_render_bridge.create_voice_job(
                reply_id=body.reply_id,
                user_id=body.user_id,
                mode=body.mode,
                rendition_id=body.rendition_id,
                variant_salt=body.variant_salt,
            )
        except RenderBridgeFailure as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
        job_id = result.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            raise HTTPException(status_code=502, detail="voice runtime omitted job_id")
        snapshot = conversations.get_reply_snapshot(
            body.reply_id,
            user_id=body.user_id,
        )
        if snapshot is None:
            raise HTTPException(status_code=404, detail="reply snapshot not found")
        conversations.record_voice_job(
            user_id=body.user_id,
            reply_id=body.reply_id,
            rendition_id=body.rendition_id,
            job_id=job_id,
            render_profile_revision=snapshot.render_profile_revision,
        )
        return result

    @application.get("/v1/voice/replies/{reply_id}")
    async def get_reply_voice(
        reply_id: str,
        user_id: str = "local-user",
        rendition_id: str = "default",
    ) -> dict[str, object]:
        try:
            link = conversations.get_voice_job_link(
                reply_id,
                user_id=user_id,
                rendition_id=rendition_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="reply voice job not found") from exc
        return {
            "reply_id": link.reply_id,
            "rendition_id": link.rendition_id,
            "job_id": link.job_id,
            "render_profile_revision": link.render_profile_revision,
            "created_at": link.created_at,
            "updated_at": link.updated_at,
        }

    @application.get("/v1/voice/jobs/{job_id}")
    async def get_voice_job(
        job_id: str,
        user_id: str = "local-user",
    ) -> dict[str, object]:
        try:
            return await active_render_bridge.get_job(job_id, user_id)
        except RenderBridgeFailure as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    @application.get("/v1/voice/jobs/{job_id}/events")
    async def voice_job_events(
        job_id: str,
        after: int = Query(default=0, ge=0),
        user_id: str = "local-user",
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        try:
            stream = await active_render_bridge.stream_events(
                job_id,
                user_id,
                after=after,
                last_event_id=last_event_id,
            )
        except RenderBridgeFailure as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
        return StreamingResponse(
            stream.body,
            status_code=stream.status_code,
            headers=stream.headers,
            media_type="text/event-stream",
        )

    @application.post("/v1/voice/jobs/{job_id}/cancel")
    async def cancel_voice_job(
        job_id: str,
        user_id: str = "local-user",
    ) -> dict[str, object]:
        try:
            return await active_render_bridge.cancel_job(job_id, user_id)
        except RenderBridgeFailure as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    @application.post("/v1/voice/jobs/{job_id}/playback")
    async def record_voice_playback(
        job_id: str,
        payload: dict[str, object],
        user_id: str = "local-user",
    ) -> dict[str, object]:
        try:
            return await active_render_bridge.record_playback(job_id, user_id, payload)
        except RenderBridgeFailure as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    @application.get("/v1/voice/artifacts/{artifact_id}")
    async def voice_artifact(
        artifact_id: str,
        request: Request,
        user_id: str = "local-user",
    ) -> StreamingResponse:
        try:
            stream = await active_render_bridge.stream_artifact(
                artifact_id,
                user_id,
                range_header=request.headers.get("range"),
            )
        except RenderBridgeFailure as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
        return StreamingResponse(
            stream.body,
            status_code=stream.status_code,
            headers=stream.headers,
        )

    @application.get("/v1/conversations")
    async def list_conversations(
        user_id: str = "local-user",
        limit: int = Query(default=20, ge=1, le=50),
        ending_before: str | None = None,
    ) -> dict[str, object]:
        try:
            items, has_more = conversations.list_conversations(
                user_id=user_id,
                limit=limit,
                ending_before=ending_before,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail="conversation cursor not found",
            ) from exc
        return {
            "conversations": [
                {
                    "id": item.id,
                    "user_id": item.user_id,
                    "title": item.title,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                }
                for item in items
            ],
            "has_more": has_more,
        }

    @application.get("/v1/conversations/{conversation_id}")
    async def get_conversation(
        conversation_id: str,
        user_id: str = "local-user",
    ) -> dict[str, object]:
        conversation = conversations.get_conversation(
            conversation_id,
            user_id=user_id,
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        messages = conversations.get_messages(conversation_id, user_id=user_id)
        reply_metadata = conversations.get_reply_metadata(conversation_id, user_id=user_id)
        assistant_snapshots = conversations.get_reply_snapshots(
            [item.id for item in messages if item.role == "assistant"],
            user_id=user_id,
        )
        return {
            "id": conversation.id,
            "user_id": conversation.user_id,
            "title": conversation.title,
            "created_at": conversation.created_at,
            "updated_at": conversation.updated_at,
            "messages": [
                {
                    "id": item.id,
                    "role": item.role,
                    "content": item.content,
                    "created_at": item.created_at,
                    "turn_index": item.turn_index,
                    **reply_metadata.get(item.id, {}),
                    **(
                        {
                            "reply_id": item.id,
                            **({"speech": {"schema_version": "1.1", "status": "eligible"}}
                               if assistant_snapshots[item.id].allow_tts else {}),
                            "render_profile_revision": (
                                assistant_snapshots[item.id].render_profile_revision
                            ),
                        }
                        if item.id in assistant_snapshots
                        else {}
                    ),
                }
                for item in messages
            ],
        }

    attach_memory_routes(application, store=memory_store, agent=chat_agent,
                         retriever=getattr(getattr(chat_agent, "post_turn", None), "retriever", None))
    return application
