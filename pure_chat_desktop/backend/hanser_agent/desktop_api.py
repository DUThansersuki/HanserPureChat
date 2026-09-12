from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from . import db
from .agent import ChatAgentService
from .agent.context_builder import ContextBuilder
from .agent.conversation import ConversationOwnershipError, ConversationStore
from .agent.planner import DialoguePlanner
from .agent.request_state import RequestStateStore
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
from .models import ChatRequest, ChatResponse
from .persona import PersonaCompiler
from .responder import HanserResponder, StyleValidator
from .retrieval import (
    HybridRetriever,
    SQLiteVectorStore,
    build_embedder,
    build_reranker,
)


class ChatService(Protocol):
    async def send(self, request: ChatRequest) -> ChatResponse:
        ...


def build_desktop_chat_agent(
    settings: Settings,
    model_gateway: ModelGateway,
    memory_store: MemoryStore,
) -> ChatAgentService:
    package_dir = Path(__file__).parent
    persona_root = package_dir / "prompts" / "persona"
    package_registry = {
        "persona-v1-production": persona_root,
        "persona-v2-production": (
            persona_root / "candidates" / "hanser-persona-v2-candidate"
        ),
        "hanser-persona-v2-candidate": (
            persona_root / "candidates" / "hanser-persona-v2-candidate"
        ),
    }
    persona_dir = package_registry[settings.persona.active_package]
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
            structured_performance_enabled=False,
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
        embedder = getattr(getattr(chat_agent, "style_tool", None), "embedder", None)
        warmup = getattr(embedder, "warmup", None)
        if warmup is not None:
            await warmup()
        yield
        if owned_model_gateway and active_model_gateway is not None:
            await active_model_gateway.close()

    application = FastAPI(
        title="Hanser Pure Chat Desktop Backend",
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
        expected_style_generation = (
            active_settings.persona.candidate_style_generation
            if active_settings.persona.active_package != "persona-v1-production"
            else None
        )
        return {
            "ok": True,
            "chat_ready": (
                expected_style_generation is None
                or active_style_generation == expected_style_generation
            ),
            "mode": "pure-chat-desktop",
            "model": active_settings.default_model,
            "persona_package": active_settings.persona.active_package,
            "style_generation": expected_style_generation or "legacy-v1",
            "active_style_generation": active_style_generation,
        }

    @application.post("/v1/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest) -> ChatResponse:
        request.output_preferences.text = True
        request.output_preferences.speech = False
        request.output_preferences.dynamic_live2d = False
        request.output_preferences.offline_performance = False
        return await chat_agent.send(request)

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

    return application

