from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from . import db
from .agent import ChatAgentService
from .agent.context_builder import ContextBuilder
from .agent.conversation import ConversationOwnershipError, ConversationStore
from .agent.request_state import RequestStateStore
from .agent.planner import DialoguePlanner
from .agent.tools.style_search import StyleSearchTool
from .agent.tools.memory_search import MemorySearchTool
from .agent.tools.wiki_search import WikiSearchTool
from .config import Settings, load_settings
from .failures import ServiceFailure
from .model_gateway import ModelGateway, build_model_gateway
from .models import (
    ChatRequest,
    ChatResponse,
    VisualPlanCreateRequest,
    VoiceJobCreateRequest,
)
from .models import MemoryItem, MemoryPatch
from .memory import (
    MemoryCandidateExtractor,
    MemoryRetriever,
    MemoryStore,
    MemoryWriteGate,
    PostTurnPipeline,
)
from .memory.state_engine import CharacterStateEngine
from .memory.summarizer import ConversationSummarizer
from .persona import PersonaCompiler
from .retrieval import (
    HybridRetriever,
    SQLiteVectorStore,
    build_embedder,
    build_reranker,
)
from .responder import HanserResponder, StyleValidator
from .render_bridge import RenderBridge, RenderBridgeFailure


class ChatService(Protocol):
    async def send(self, request: ChatRequest) -> ChatResponse:
        ...


def build_chat_agent(
    settings: Settings,
    model_gateway: ModelGateway,
    memory_store: MemoryStore,
) -> ChatAgentService:
    package_dir = Path(__file__).parent
    prompt_dir = package_dir / "prompts"
    persona_root = prompt_dir / "persona"
    package_registry = {
        "persona-v1-production": persona_root,
        "hanser-persona-v2-candidate": persona_root / "candidates" / "hanser-persona-v2-candidate",
    }
    persona_dir = package_registry[settings.persona.active_package]
    persona_compiler = PersonaCompiler(persona_dir)
    if persona_compiler.is_v2 and not settings.style.reviewed_only:
        raise ValueError("Persona v2 requires style.reviewed_only=true")

    embedder = build_embedder(settings.embedding)
    vector_store = SQLiteVectorStore(settings.db_path)
    hybrid_retriever = HybridRetriever(
        db_path=settings.db_path,
        userdict_path=settings.userdict_path,
        config=settings.retrieval,
        embedder=embedder,
        vector_store=vector_store,
    )
    wiki_tool = WikiSearchTool(
        settings=settings,
        reranker=build_reranker(settings.reranker),
        retriever=hybrid_retriever,
    )
    style_tool = StyleSearchTool(
        settings=settings,
        embedder=embedder,
        vector_store=vector_store,
        strict_v2=persona_compiler.is_v2,
        pinned_generation=(
            settings.persona.candidate_style_generation
            if persona_compiler.is_v2
            else None
        ),
    )
    memory_retriever = MemoryRetriever(
        store=memory_store,
        embedder=embedder,
        vector_store=vector_store,
        config=settings.memory,
    )
    memory_tool = MemorySearchTool(memory_retriever)
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
    context_builder = ContextBuilder(
        persona_compiler,
        settings.context,
        provider_context_window=settings.responder.context_window,
        provider_max_output_tokens=settings.responder.max_tokens,
    )
    responder = HanserResponder(
        model_gateway=model_gateway,
        validator=StyleValidator(
            persona_dir / "style_constraints.yaml"
        ),
        structured_performance_enabled=(
            settings.performance.structured_performance_enabled
        ),
    )
    return ChatAgentService(
        conversations=conversations,
        planner=DialoguePlanner(model_gateway),
        wiki_tool=wiki_tool,
        style_tool=style_tool,
        memory_tool=memory_tool,
        memory_store=memory_store,
        post_turn=post_turn,
        context_builder=context_builder,
        responder=responder,
        request_state=RequestStateStore(settings.db_path),
        performance_config=settings.performance,
    )


def create_app(
    *,
    settings: Settings | None = None,
    model_gateway: ModelGateway | None = None,
    chat_agent: ChatService | None = None,
    memory_store: MemoryStore | None = None,
) -> FastAPI:
    active_settings = settings or load_settings(
        os.environ.get("HANSER_CONFIG")
    )
    owned_model_gateway = model_gateway is None and chat_agent is None
    active_model_gateway = model_gateway
    with db.connect(active_settings.db_path) as conn:
        db.init_db(conn)
    active_memory_store = memory_store or MemoryStore(active_settings.db_path)
    active_conversations = getattr(
        chat_agent,
        "conversations",
        ConversationStore(
            active_settings.db_path,
            max_messages=active_settings.memory.recent_messages,
        ),
    )
    render_bridge = RenderBridge(active_conversations, active_settings.performance)
    active_memory_retriever: MemoryRetriever | None = None
    if chat_agent is None:
        active_model_gateway = active_model_gateway or build_model_gateway(
            active_settings
        )
        chat_agent = build_chat_agent(
            active_settings,
            active_model_gateway,
            active_memory_store,
        )
        active_memory_retriever = chat_agent.memory_tool.retriever

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await render_bridge.close()
        if owned_model_gateway and active_model_gateway is not None:
            await active_model_gateway.close()

    application = FastAPI(
        title="Hanser Agent Backend",
        version="0.6.0",
        lifespan=lifespan,
    )

    @application.exception_handler(ConversationOwnershipError)
    async def conversation_owner_conflict(_, exc: ConversationOwnershipError):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=409,
            content={
                "detail": "conversation_id is already owned by another user",
                "conversation_id": exc.conversation_id,
            },
        )

    @application.exception_handler(ServiceFailure)
    async def structured_service_failure(_, exc: ServiceFailure):
        from fastapi.responses import JSONResponse

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
        return {
            "ok": True,
            "db_path": str(active_settings.db_path),
            "model": active_settings.default_model,
        }

    @application.post(
        "/v1/chat",
        response_model=ChatResponse,
    )
    async def chat(request: ChatRequest) -> ChatResponse:
        return await chat_agent.send(request)

    @application.post("/v1/voice/jobs", status_code=202)
    async def create_voice_job(body: VoiceJobCreateRequest) -> dict[str, object]:
        try:
            return await render_bridge.create_voice_job(
                reply_id=body.reply_id,
                user_id=body.user_id,
                mode=body.mode,
                rendition_id=body.rendition_id,
                variant_salt=body.variant_salt,
            )
        except RenderBridgeFailure as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    @application.get("/v1/voice/jobs/{job_id}")
    async def get_voice_job(
        job_id: str, user_id: str = "local-user"
    ) -> dict[str, object]:
        try:
            return await render_bridge.get_job(job_id, user_id)
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
            stream = await render_bridge.stream_events(
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
        job_id: str, user_id: str = "local-user"
    ) -> dict[str, object]:
        try:
            return await render_bridge.cancel_job(job_id, user_id)
        except RenderBridgeFailure as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    @application.post("/v1/voice/jobs/{job_id}/playback")
    async def record_voice_playback(
        job_id: str,
        payload: dict[str, object],
        user_id: str = "local-user",
    ) -> dict[str, object]:
        try:
            return await render_bridge.record_playback(job_id, user_id, payload)
        except RenderBridgeFailure as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    @application.get("/v1/voice/artifacts/{artifact_id}")
    async def voice_artifact(
        artifact_id: str,
        request: Request,
        user_id: str = "local-user",
    ) -> StreamingResponse:
        try:
            stream = await render_bridge.stream_artifact(
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

    @application.post("/v1/performance/visual-plans")
    async def create_visual_plan(
        body: VisualPlanCreateRequest,
    ) -> dict[str, object]:
        try:
            return await render_bridge.create_visual_plan(
                reply_id=body.reply_id,
                user_id=body.user_id,
                epoch=body.epoch,
            )
        except RenderBridgeFailure as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    @application.get("/v1/conversations")
    async def list_conversations(
        user_id: str = "local-user",
        limit: int = Query(default=20, ge=1, le=50),
        ending_before: str | None = None,
    ) -> dict[str, object]:
        try:
            conversations, has_more = active_conversations.list_conversations(
                user_id=user_id,
                limit=limit,
                ending_before=ending_before,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="conversation cursor not found") from exc
        return {
            "conversations": [
                {
                    "id": item.id,
                    "user_id": item.user_id,
                    "title": item.title,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                }
                for item in conversations
            ],
            "has_more": has_more,
        }

    @application.get("/v1/conversations/{conversation_id}")
    async def get_conversation(
        conversation_id: str,
        user_id: str = "local-user",
    ) -> dict[str, object]:
        conversation = active_conversations.get_conversation(
            conversation_id,
            user_id=user_id,
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        messages = active_conversations.get_messages(
            conversation_id,
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
                }
                for item in messages
            ],
        }

    @application.get("/v1/maintenance/post-turn-failures")
    async def pending_post_turn_failures() -> list[dict[str, object]]:
        if not hasattr(chat_agent, "pending_post_turn_failures"):
            return []
        return chat_agent.pending_post_turn_failures()

    @application.post("/v1/maintenance/post-turn-failures/{failure_id}/retry")
    async def retry_post_turn_failure(failure_id: str) -> dict[str, object]:
        if not hasattr(chat_agent, "retry_post_turn"):
            raise HTTPException(status_code=404, detail="post-turn failure not found")
        try:
            return await chat_agent.retry_post_turn(failure_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="post-turn failure not found") from exc

    @application.get("/v1/memories", response_model=list[MemoryItem])
    async def list_memories(
        user_id: str = "local-user",
        conversation_id: str | None = None,
        status: str = "active",
    ) -> list[MemoryItem]:
        return active_memory_store.list_memories(
            user_id=user_id,
            conversation_id=conversation_id,
            status=status,
        )

    @application.patch("/v1/memories/{memory_id}", response_model=MemoryItem)
    async def update_memory(
        memory_id: str,
        patch: MemoryPatch,
    ) -> MemoryItem:
        try:
            memory = active_memory_store.update_memory(memory_id, patch)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="memory not found") from exc
        if patch.content is not None and active_memory_retriever is not None:
            await active_memory_retriever.index(memory)
        return memory

    @application.delete("/v1/memories/{memory_id}")
    async def delete_memory(memory_id: str) -> dict[str, str]:
        try:
            active_memory_store.delete_memory(memory_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="memory not found") from exc
        if active_memory_retriever is not None:
            active_memory_retriever.delete_index(memory_id)
        return {"deleted": memory_id}

    return application
