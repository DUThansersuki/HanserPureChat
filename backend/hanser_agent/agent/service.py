from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from uuid import uuid4

from ..failures import ServiceFailure
from ..memory import MemoryStore, PostTurnPipeline
from ..models import ChatRequest, ChatResponse, RelationshipState, SceneState
from ..persona.expression import observe_recent_expressions
from ..persona.permissions import infer_expression_permissions
from ..persona.policy import build_guidance
from ..persona.signals import build_turn_signals
from ..responder import HanserResponder
from .context_builder import ContextBuilder
from .conversation import ConversationOwnershipError, ConversationStore
from .planner import DialoguePlanner
from .request_state import RequestStateStore
from .tools.memory_search import MemorySearchTool
from .tools.style_search import StyleSearchTool
from .tools.wiki_search import WikiSearchTool


class ChatAgentService:
    def __init__(
        self,
        *,
        conversations: ConversationStore,
        planner: DialoguePlanner,
        wiki_tool: WikiSearchTool,
        style_tool: StyleSearchTool,
        memory_tool: MemorySearchTool,
        memory_store: MemoryStore,
        post_turn: PostTurnPipeline,
        context_builder: ContextBuilder,
        responder: HanserResponder,
        request_state: RequestStateStore | None = None,
    ):
        self.conversations = conversations
        self.planner = planner
        self.wiki_tool = wiki_tool
        self.style_tool = style_tool
        self.memory_tool = memory_tool
        self.memory_store = memory_store
        self.post_turn = post_turn
        self.context_builder = context_builder
        self.responder = responder
        self.request_state = request_state

    async def send(self, request: ChatRequest) -> ChatResponse:
        trace_id = str(uuid4())
        request_id = request.request_id or trace_id
        request_hash = hashlib.sha256(
            json.dumps(
                {
                    "user_id": request.user_id,
                    "conversation_id": request.conversation_id,
                    "message": request.message,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        if self.request_state is not None:
            claim = self.request_state.claim(
                request_id=request_id,
                user_id=request.user_id,
                conversation_id=request.conversation_id,
                request_hash=request_hash,
                trace_id=trace_id,
            )
            if claim.cached_response is not None:
                return claim.cached_response
            trace_id = claim.trace_id

        timings: dict[str, float] = {}
        degraded: list[str] = []
        try:
            started = time.perf_counter()
            history = self.conversations.get_recent(
                request.conversation_id, user_id=request.user_id
            )
            summary = self.memory_store.get_summary(request.conversation_id)
            relationship = self.memory_store.get_relationship(request.user_id)
            scene = self.memory_store.get_scene(request.conversation_id)
            timings["load_context_state"] = time.perf_counter() - started

            started = time.perf_counter()
            plan = await self.planner.plan(
                request.message, history, summary.content if summary else None
            )
            degraded.extend(plan.degraded_reasons)
            timings["planner"] = time.perf_counter() - started

            turn_signals = None
            behavior_decision = None
            expression_observation = None
            effective_persona = self.context_builder.persona_compiler.effective_settings
            if self.context_builder.persona_compiler.is_v2 and effective_persona is not None:
                history_refs = [
                    f"conversation:{request.conversation_id}:visible:{index}"
                    for index, _ in enumerate(history)
                ]
                turn_signals = build_turn_signals(
                    request.message,
                    current_message_ref=f"request:{request_id}:current_user",
                    planner_payload=plan.persona_signals,
                    history_refs=history_refs,
                    history_texts=[item.content for item in history],
                )
                degraded.extend(turn_signals.degraded_reasons)
                expression_observation = observe_recent_expressions(
                    history,
                    window_turns=effective_persona.observation_turns,
                    recency_decay=effective_persona.recency_decay,
                )
                behavior_decision = build_guidance(
                    turn_signals,
                    permissions=infer_expression_permissions(
                        history,
                        request.message,
                    ),
                    observations=expression_observation,
                    effective_persona=effective_persona,
                    response_mode=plan.response_mode,
                    fact_sensitivity=plan.fact_sensitivity,
                    need_wiki=plan.need_wiki,
                )

            async def safe_tool(label: str, coroutine):
                tool_started = time.perf_counter()
                try:
                    return await coroutine, None
                except Exception as exc:
                    logging.getLogger(__name__).warning(
                        "optional context tool failed",
                        extra={"tool": label, "error_type": type(exc).__name__, "trace_id": trace_id},
                    )
                    return None, f"{label}_unavailable"
                finally:
                    timings[label] = time.perf_counter() - tool_started

            task_specs = []
            if plan.need_style_examples:
                if turn_signals is None:
                    style_search = self.style_tool.search(
                        plan.style_query or request.message,
                        plan,
                    )
                else:
                    style_search = self.style_tool.search(
                        request.message,
                        plan,
                        turn_signals=turn_signals,
                        behavior_decision=behavior_decision,
                        observations=expression_observation,
                    )
                task_specs.append(("style", style_search))
            if plan.need_memory:
                task_specs.append(("memory", self.memory_tool.search(query=plan.memory_query or request.message, user_id=request.user_id)))
            if plan.need_wiki:
                task_specs.append(("wiki", self.wiki_tool.search(plan.standalone_query, plan.keywords)))
            results = await asyncio.gather(
                *(safe_tool(label, coroutine) for label, coroutine in task_specs)
            ) if task_specs else []
            by_label = {
                label: result
                for (label, _), (result, reason) in zip(task_specs, results, strict=True)
                if result is not None
            }
            degraded.extend(
                reason
                for _, reason in results
                if reason is not None
            )
            style_result = by_label.get("style")
            memory_result = by_label.get("memory")
            wiki_result = by_label.get("wiki")
            if wiki_result is not None:
                degraded.extend(getattr(wiki_result, "degraded_reasons", []))

            started = time.perf_counter()
            context = self.context_builder.build(
                current_message=request.message,
                history=history,
                plan=plan,
                wiki_evidence=(wiki_result.evidence if wiki_result else []),
                style_examples=(style_result.examples if style_result else []),
                memories=([item.memory for item in memory_result.memories] if memory_result else []),
                address_options=self.memory_store.list_address_options(user_id=request.user_id),
                conversation_summary=summary.content if summary else None,
                relationship_state=relationship,
                scene_state=scene,
                turn_signals=turn_signals,
                behavior_decision=behavior_decision,
                effective_persona=effective_persona,
            )
            timings["context"] = time.perf_counter() - started

            started = time.perf_counter()
            generated = await self.responder.respond(context)
            timings["responder"] = time.perf_counter() - started
            response = ChatResponse(
                text=generated.text,
                keywords=plan.keywords if plan.need_wiki else [],
                anchored=wiki_result.anchored if wiki_result else [],
                sources=wiki_result.candidates if wiki_result else [],
                request_id=request_id,
                trace_id=trace_id,
                status="degraded" if degraded else "ok",
                degraded_reasons=list(dict.fromkeys(degraded)),
            )

            started = time.perf_counter()
            user_message_id, _ = self.conversations.append_turn(
                conversation_id=request.conversation_id,
                user_id=request.user_id,
                user_text=request.message,
                assistant_text=response.text,
                model_name=self.responder.model_name,
                trace_id=trace_id,
                persona_version=context.persona.version,
            )
            timings["persist_reply"] = time.perf_counter() - started

            payload = {
                "user_id": request.user_id,
                "conversation_id": request.conversation_id,
                "user_message_id": user_message_id,
                "user_message": request.message,
                "previous_relationship": relationship.model_dump(mode="json"),
                "previous_scene": scene.model_dump(mode="json"),
            }
            started = time.perf_counter()
            try:
                await self.post_turn.process(
                    user_id=request.user_id,
                    conversation_id=request.conversation_id,
                    user_message_id=user_message_id,
                    user_message=request.message,
                    previous_relationship=relationship,
                    previous_scene=scene,
                )
            except Exception as exc:
                logging.getLogger(__name__).exception(
                    "post-turn failed after reply persistence",
                    extra={"trace_id": trace_id, "request_id": request_id},
                )
                response.status = "degraded"
                response.post_turn_status = "pending_retry"
                response.degraded_reasons = list(
                    dict.fromkeys([*response.degraded_reasons, "post_turn_pending_retry"])
                )
                if self.request_state is not None:
                    response.post_turn_retry_id = self.request_state.record_post_turn_failure(
                        request_id=request_id,
                        trace_id=trace_id,
                        stage="post_turn",
                        payload=payload,
                        error=exc,
                    )
            timings["post_turn"] = time.perf_counter() - started
            if self.request_state is not None:
                self.request_state.complete(request_id, response, timings)
            return response
        except ConversationOwnershipError:
            if self.request_state is not None:
                self.request_state.fail(request_id, "conversation_owner_conflict", timings)
            raise
        except ServiceFailure as exc:
            exc.with_trace(trace_id)
            if self.request_state is not None:
                self.request_state.fail(request_id, exc.code, timings)
            raise
        except Exception as exc:
            if self.request_state is not None:
                self.request_state.fail(request_id, "request_failed", timings)
            raise ServiceFailure(
                "request_failed",
                "request failed before a reply was persisted",
                retryable=True,
                status_code=500,
                trace_id=trace_id,
            ) from exc

    async def retry_post_turn(self, failure_id: str) -> dict[str, object]:
        if self.request_state is None:
            raise KeyError(failure_id)
        row = self.request_state.get_post_turn_failure(failure_id)
        if row is None:
            raise KeyError(failure_id)
        if str(row["status"]) == "completed":
            return {"id": failure_id, "status": "completed"}
        payload = json.loads(str(row["payload_json"]))
        try:
            await self.post_turn.process(
                user_id=payload["user_id"],
                conversation_id=payload["conversation_id"],
                user_message_id=payload["user_message_id"],
                user_message=payload["user_message"],
                previous_relationship=RelationshipState.model_validate(payload["previous_relationship"]),
                previous_scene=SceneState.model_validate(payload["previous_scene"]),
            )
        except Exception as exc:
            self.request_state.update_post_turn_error(failure_id, exc)
            raise ServiceFailure(
                "post_turn_retry_failed",
                "post-turn retry failed and remains pending",
                retryable=True,
            ) from exc
        self.request_state.mark_post_turn_completed(failure_id)
        return {"id": failure_id, "status": "completed"}

    def pending_post_turn_failures(self) -> list[dict[str, object]]:
        return self.request_state.list_post_turn_failures() if self.request_state else []
