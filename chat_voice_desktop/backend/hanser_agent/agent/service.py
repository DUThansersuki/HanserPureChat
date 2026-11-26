from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from ..config import PerformanceConfig
from ..failures import ServiceFailure
from ..memory import MemoryStore, PostTurnPipeline
from ..memory.extractor import answer_length_preference
from ..models import (
    ChatRequest,
    ChatResponse,
    PersonaRequestSettings,
    RelationshipState,
    SceneState,
)
from ..persona.expression import build_language_control, observe_recent_expressions
from ..persona.permissions import infer_expression_permissions
from ..persona.policy import build_guidance
from ..persona.schemas import ExplicitPreferenceEvent
from ..persona.signals import build_turn_signals
from ..responder import (
    HanserResponder,
    OutputPreferences,
    PerformancePolicyResolver,
    ReplySnapshot,
    SpeechTicket,
)
from ..responder.performance import effective_preferences
from .context_builder import ContextBuilder
from .conversation import ConversationOwnershipError, ConversationStore
from .planner import DialoguePlanner
from .request_state import RequestStateStore
from .tools.memory_search import MemorySearchTool
from .tools.style_search import StyleSearchTool
from .tools.wiki_search import WikiSearchTool


CHAT_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")


@dataclass(slots=True)
class _UserLockEntry:
    lock: asyncio.Lock
    users: int = 0


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
        performance_config: PerformanceConfig | None = None,
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
        self.performance_config = performance_config or PerformanceConfig()
        self.performance_policy = PerformancePolicyResolver()
        self._user_locks: dict[str, _UserLockEntry] = {}

    @asynccontextmanager
    async def _user_lock(self, user_id: str):
        entry = self._user_locks.setdefault(
            user_id,
            _UserLockEntry(lock=asyncio.Lock()),
        )
        entry.users += 1
        try:
            async with entry.lock:
                yield
        finally:
            entry.users -= 1
            if entry.users == 0:
                self._user_locks.pop(user_id, None)

    async def send(self, request: ChatRequest) -> ChatResponse:
        async with self._user_lock(request.user_id):
            return await self._send(request)

    async def _send(self, request: ChatRequest) -> ChatResponse:
        trace_id = str(uuid4())
        request_id = request.request_id or trace_id
        request_identity = {
            "user_id": request.user_id,
            "conversation_id": request.conversation_id,
            "message": request.message,
            "persona_settings": request.persona_settings.model_dump(mode="json"),
            "output_preferences": request.output_preferences.model_dump(mode="json"),
            "render_profile_revision": request.render_profile_revision,
        }
        request_hash = hashlib.sha256(
            json.dumps(
                request_identity,
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        frozen_request: dict[str, object] = {
            "persona_settings": request.persona_settings.model_dump(mode="json"),
            "output_preferences": effective_preferences(
                request.output_preferences,
                structured_enabled=self.performance_config.structured_performance_enabled,
                speech_enabled=self.performance_config.speech_runtime_enabled,
                dynamic_live2d_enabled=self.performance_config.dynamic_live2d_enabled,
                offline_export_enabled=self.performance_config.offline_export_enabled,
            ).model_dump(mode="json"),
            "render_profile_revision": (
                request.render_profile_revision
                or self.performance_config.render_profile_revision
            ),
            "structured_performance": self.performance_config.structured_performance_enabled,
            "persona_combination": {
                "package_id": self.context_builder.persona_compiler.package_id,
                "compiler_version": self.context_builder.persona_compiler.VERSION,
                "schema_version": self.context_builder.persona_compiler.package_schema_version,
                "style_generation": getattr(self.style_tool, "pinned_generation", None),
            },
        }
        if self.request_state is not None:
            claim = self.request_state.claim(
                request_id=request_id,
                user_id=request.user_id,
                conversation_id=request.conversation_id,
                request_hash=request_hash,
                trace_id=trace_id,
                effective_request=frozen_request,
            )
            if claim.cached_response is not None:
                return claim.cached_response
            trace_id = claim.trace_id
            frozen_request = {
                **frozen_request,
                **(claim.effective_request or {}),
            }

        output_preferences = OutputPreferences.model_validate(
            frozen_request["output_preferences"]
        )
        persona_settings = PersonaRequestSettings.model_validate(
            frozen_request["persona_settings"]
        )
        structured_performance = bool(
            frozen_request["structured_performance"]
        ) and output_preferences.requests_performance()
        render_profile_revision = str(frozen_request["render_profile_revision"])

        timings: dict[str, float] = {}
        degraded: list[str] = []
        try:
            user_message_id = str(uuid4())
            started = time.perf_counter()
            summary = self.post_turn.summarizer.update(request.conversation_id, user_id=request.user_id)
            continuity_context = self.post_turn.summarizer.context(
                request.conversation_id, user_id=request.user_id, query=request.message, summary=summary,
            )
            history = self.conversations.get_context_history(
                request.conversation_id,
                user_id=request.user_id,
                summary_through_index=(
                    summary.through_message_index if summary else None
                ),
            )
            relationship = self.memory_store.get_relationship(request.user_id)
            scene = self.memory_store.get_scene(request.conversation_id)
            timings["load_context_state"] = time.perf_counter() - started

            started = time.perf_counter()
            planner_gateway = getattr(self.planner, "model_gateway", None)
            planner_records = getattr(planner_gateway, "call_records", [])
            planner_call_start = len(planner_records)
            planner_capture = getattr(planner_gateway, "start_call_capture", None)
            planner_finish = getattr(planner_gateway, "finish_call_capture", None)
            planner_capture_token = planner_capture() if planner_capture else None
            try:
                plan = await self.planner.plan(
                    request.message, history, continuity_context
                )
            finally:
                planner_call_records = (
                    planner_finish(planner_capture_token)
                    if planner_finish and planner_capture_token is not None
                    else planner_records[planner_call_start:]
                )
            degraded.extend(plan.degraded_reasons)
            timings["planner"] = time.perf_counter() - started

            turn_signals = None
            behavior_decision = None
            expression_observation = None
            language_control = None
            expression_permissions: dict[str, str] = {}
            scene_for_turn = scene
            effective_persona = self.context_builder.persona_compiler.effective_settings
            if self.context_builder.persona_compiler.is_v2 and effective_persona is not None:
                durable_preferences = [
                    item
                    for item in self.memory_store.list_memories(user_id=request.user_id)
                    if item.type == "user_preference"
                    and item.predicate == "expression_permission"
                    and item.memory_scope == "global"
                ]
                prior_permission_events = self.memory_store.list_permission_events(
                    user_id=request.user_id, conversation_id=request.conversation_id
                )
                turn_signals = build_turn_signals(
                    request.message,
                    current_message_ref=user_message_id,
                    adult_innuendo_opt_in=persona_settings.adult_innuendo_opt_in,
                    planner_payload=plan.persona_signals,
                    history_messages=history,
                    prior_preference_events=prior_permission_events,
                )
                turn_signals.preference_events = self.memory_store.filter_permission_events(
                    user_id=request.user_id, events=turn_signals.preference_events
                )
                prior_permission_events = [
                    event for event in prior_permission_events
                    if event in turn_signals.preference_events
                ]
                degraded.extend(turn_signals.degraded_reasons)
                scene_for_turn = self._scene_for_current_turn(
                    scene, request.message, turn_signals
                )
                expression_history = self.conversations.get_recent_assistant_turns(
                    request.conversation_id,
                    user_id=request.user_id,
                    limit=effective_persona.observation_turns,
                )
                expression_observation = observe_recent_expressions(
                    expression_history,
                    window_turns=effective_persona.observation_turns,
                    recency_decay=effective_persona.recency_decay,
                )
                language_control = build_language_control(
                    expression_observation,
                    turn_signals,
                    planner_dialogue_function=plan.dialogue_function,
                    planner_greeting_turn=plan.greeting_turn,
                )
                plan.continuation_policy = language_control.continuation_policy
                expression_permissions = infer_expression_permissions(
                    [
                        *prior_permission_events,
                        *[
                            item for item in turn_signals.preference_events
                            if item.source_message_id == user_message_id
                        ],
                    ],
                    current_message_id=user_message_id,
                    persistent_preferences=durable_preferences,
                    explicit_overrides=(
                        {"innuendo": "allow"}
                        if persona_settings.adult_innuendo_opt_in
                        else None
                    ),
                )
                behavior_decision = build_guidance(
                    turn_signals,
                    permissions=expression_permissions,
                    observations=expression_observation,
                    effective_persona=effective_persona,
                    response_mode=plan.response_mode,
                    fact_sensitivity=plan.fact_sensitivity,
                    need_wiki=plan.need_wiki,
                    behavior_priors=self.context_builder.persona_compiler.behavior_priors,
                    pacing_key=f"{request.user_id}:{request.conversation_id}",
                    successful_assistant_turns=(
                        self.conversations.message_count(
                            request.conversation_id,
                            user_id=request.user_id,
                        )
                        // 2
                    ),
                    current_message=request.message,
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
                        recent_example_ids=expression_observation.recent_example_ids,
                        repetition_penalty=effective_persona.repetition_penalty,
                    )
                task_specs.append(("style", style_search))
            if plan.need_memory:
                task_specs.append(("memory", self.memory_tool.search(
                    query=plan.memory_query or request.message,
                    user_id=request.user_id,
                    conversation_id=request.conversation_id,
                )))
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
            chat_time = datetime.now(CHAT_TIMEZONE)
            current_length = answer_length_preference(request.message)
            length_memory = self.memory_store.get_answer_length_preference(
                user_id=request.user_id, conversation_id=request.conversation_id,
            )
            length_value = current_length[0] if current_length else (
                length_memory.object_value if length_memory else None
            )
            context = self.context_builder.build(
                current_message=request.message,
                history=history,
                plan=plan,
                wiki_evidence=(wiki_result.evidence if wiki_result else []),
                style_examples=(style_result.examples if style_result else []),
                memories=([item.memory for item in memory_result.memories] if memory_result else []),
                address_options=self.memory_store.list_address_options(user_id=request.user_id),
                conversation_summary=continuity_context,
                relationship_state=relationship,
                scene_state=scene_for_turn,
                turn_signals=turn_signals,
                behavior_decision=behavior_decision,
                effective_persona=effective_persona,
                language_control=language_control,
                current_time=chat_time,
                structured_performance=structured_performance,
                answer_length_preference=(
                    "short" if length_value and "简短" in length_value else
                    "detailed" if length_value else None
                ),
                answer_length_source="本轮明确要求" if current_length else "已保存的回答长度偏好",
            )
            timings["context"] = time.perf_counter() - started

            started = time.perf_counter()
            responder_gateway = getattr(self.responder, "model_gateway", None)
            responder_records = getattr(responder_gateway, "call_records", [])
            responder_call_start = len(responder_records)
            responder_capture = getattr(responder_gateway, "start_call_capture", None)
            responder_finish = getattr(responder_gateway, "finish_call_capture", None)
            responder_capture_token = responder_capture() if responder_capture else None
            try:
                if self.performance_config.structured_performance_enabled:
                    generated = await self.responder.respond(
                        context,
                        structured_performance=structured_performance,
                    )
                else:
                    generated = await self.responder.respond(context)
            finally:
                responder_call_records = (
                    responder_finish(responder_capture_token)
                    if responder_finish and responder_capture_token is not None
                    else responder_records[responder_call_start:]
                )
            timings["responder"] = time.perf_counter() - started
            if structured_performance:
                degraded.extend(generated.performance_degraded_reasons)
            allowed_performance = self.performance_policy.resolve(
                generated.performance if structured_performance else None,
                behavior_decision,
            )
            semantic_text = generated.semantic_text or generated.text
            assistant_message_id = str(uuid4())
            requested_consumers = request.output_preferences.requests_performance()
            speech_ticket = None
            if request.output_preferences.speech:
                speech_ticket = SpeechTicket(
                    status="eligible" if output_preferences.speech else "unavailable",
                    reason=(
                        None
                        if output_preferences.speech
                        else "speech_feature_disabled"
                    ),
                )
            response = ChatResponse(
                text=generated.text,
                keywords=plan.keywords if plan.need_wiki else [],
                anchored=wiki_result.anchored if wiki_result else [],
                sources=wiki_result.candidates if wiki_result else [],
                request_id=request_id,
                reply_id=assistant_message_id,
                speech=speech_ticket,
                trace_id=trace_id,
                status="degraded" if degraded else "ok",
                degraded_reasons=list(dict.fromkeys(degraded)),
            )

            payload = {
                "user_id": request.user_id,
                "conversation_id": request.conversation_id,
                "user_message_id": user_message_id,
                "user_message": request.message,
                "previous_relationship": relationship.model_dump(mode="json"),
                "previous_scene": scene.model_dump(mode="json"),
                "permission_events": (
                    [
                        item.model_dump(mode="json")
                        for item in turn_signals.preference_events
                        if item.source_message_id == user_message_id
                    ]
                    if turn_signals is not None else []
                ),
            }
            snapshot = ReplySnapshot(
                request_id=request_id,
                reply_id=assistant_message_id,
                user_id=request.user_id,
                conversation_id=request.conversation_id,
                semantic_text=semantic_text,
                display_text=response.text,
                performance=(generated.performance if structured_performance else None),
                allowed_performance=allowed_performance,
                output_preferences=output_preferences,
                allow_tts=output_preferences.speech,
                language=self._language_of(semantic_text),
                constraints_ref=allowed_performance.constraints_ref,
                render_profile_revision=render_profile_revision,
                text_source=(
                    "validated_semantic"
                    if structured_performance
                    else "legacy_text_source"
                ),
                persona_trace={
                    "input": {
                        "current_message_id": user_message_id,
                        "current_time": chat_time.isoformat(timespec="seconds"),
                        "timezone": "Asia/Shanghai",
                        "persona_settings": persona_settings.model_dump(mode="json"),
                        "visible_history": [
                            {"message_id": item.message_id, "role": item.role}
                            for item in history
                        ],
                    },
                    "signals": (
                        turn_signals.model_dump(mode="json")
                        if turn_signals is not None else None
                    ),
                    "effective_persona": (
                        effective_persona.model_dump(mode="json")
                        if effective_persona is not None else None
                    ),
                    "permissions": {
                        "events": (
                            [item.model_dump(mode="json") for item in turn_signals.preference_events]
                            if turn_signals is not None else []
                        ),
                        "effective": expression_permissions,
                    },
                    "policy": (
                        behavior_decision.model_dump(mode="json")
                        if behavior_decision is not None else None
                    ),
                    "language": {
                        "observation": (
                            expression_observation.model_dump(mode="json")
                            if expression_observation is not None else None
                        ),
                        "control": (
                            language_control.model_dump(mode="json")
                            if language_control is not None else None
                        ),
                    },
                    "retrieval": {
                        "retrieved_style_ids": getattr(
                            style_result, "retrieved_style_ids", []
                        ),
                        "selected_style_ids": (
                            [item.id for item in style_result.examples]
                            if style_result is not None else []
                        ),
                        "injected_style_ids": context.injected_style_ids,
                        "not_injected_due_budget": [
                            item.id for item in style_result.examples
                            if item.id not in context.injected_style_ids
                        ] if style_result is not None else [],
                        "matched_by": getattr(style_result, "matched_by", {}),
                        "injected_matched_by": context.style_matched_by,
                        "injected_payload_kinds": context.style_payload_kinds,
                        "payload_composition": self._payload_composition(
                            context.style_payload_kinds
                        ),
                        "pattern_redundant_with_behavior_decision": (
                            context.style_pattern_redundant_ids
                        ),
                        "style_budget_drops": [
                            item.model_dump(mode="json")
                            for item in context.drop_ledger
                            if item.item.startswith("style_example:")
                        ],
                        "scores": getattr(style_result, "scores", {}),
                        "score_components": getattr(style_result, "score_components", {}),
                        "match_details": getattr(style_result, "match_details", {}),
                        "excluded": getattr(style_result, "excluded", {}),
                        "empty_reason": getattr(style_result, "empty_reason", None),
                        "candidate_counts": getattr(style_result, "candidate_counts", {}),
                        "exclusion_counts": getattr(style_result, "exclusion_counts", {}),
                        "generation": getattr(style_result, "generation", None),
                        "revision": getattr(style_result, "revision", None),
                        "recent_example_ids": (
                            expression_observation.recent_example_ids
                            if expression_observation is not None else []
                        ),
                    },
                    "combination": {
                        "package_id": context.persona.package_id,
                        "compiler_version": context.persona.compiler_version,
                        "schema_version": context.persona.schema_version,
                        "persona_source_sha256": context.persona_source_sha256,
                        "persona_render_sha256": context.persona_render_sha256,
                        "prompt_sha256": context.prompt_sha256,
                        "style_generation": getattr(self.style_tool, "pinned_generation", None),
                        "style_revision": getattr(style_result, "revision", None),
                    },
                    "generation": {
                        "raw_text": generated.raw_text,
                        "semantic_text": semantic_text,
                        "final_text": response.text,
                        "status": generated.generation_status,
                        "policy": getattr(
                            self.responder, "rag_generation_policy", "M0"
                        ),
                        "path": self._generation_path(generated),
                        "validator_actions": generated.validator_actions,
                        "planner_calls": len(planner_call_records),
                        "planner_cache_hit": plan.planner_cache_hit,
                        "planner_fast_path": plan.planner_fast_path,
                        "planner_failure_kind": plan.planner_failure_kind,
                        "planner_route_basis": plan.planner_route_basis,
                        "planner_input_estimated_tokens": plan.planner_input_estimated_tokens,
                        "planner_history_messages": plan.planner_history_messages,
                        "planner_records": [
                            {
                                key: record.get(key)
                                for key in (
                                    "provider", "model", "kind", "status",
                                    "error_type", "latency_seconds",
                                    "finish_reason", "content_chars", "reasoning_chars",
                                )
                            }
                            for record in planner_call_records
                        ],
                        "responder_attempts": generated.attempts,
                        "responder_calls": len(responder_call_records),
                        "token_usage": self._token_usage(
                            [*planner_call_records, *responder_call_records]
                        ),
                        "latency_seconds": {
                            key: value for key, value in timings.items()
                            if key in {"planner", "style", "memory", "wiki", "context", "responder"}
                        },
                    },
                },
            )

            started = time.perf_counter()
            self.conversations.append_turn(
                conversation_id=request.conversation_id,
                user_id=request.user_id,
                user_text=request.message,
                assistant_text=response.text,
                model_name=self.responder.model_name,
                trace_id=trace_id,
                persona_version=context.persona.version,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
                reply_snapshot=snapshot,
                response=response,
                request_hash=request_hash,
                post_turn_payload=payload,
                style_example_ids=(
                    context.injected_style_ids
                ),
            )
            timings["persist_reply"] = time.perf_counter() - started
            started = time.perf_counter()
            try:
                await self.post_turn.process(
                    user_id=request.user_id,
                    conversation_id=request.conversation_id,
                    user_message_id=user_message_id,
                    user_message=request.message,
                    previous_relationship=relationship,
                    previous_scene=scene,
                    permission_events=(
                        [
                            item for item in turn_signals.preference_events
                            if item.source_message_id == user_message_id
                        ]
                        if turn_signals is not None else []
                    ),
                )
                self.conversations.mark_post_turn_completed(request_id)
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

    @staticmethod
    def _token_usage(records: list[dict[str, object]]) -> dict[str, object]:
        fields = (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "input_cache_hit_tokens",
            "input_cache_miss_tokens",
        )
        available = {
            field: [int(record[field]) for record in records if isinstance(record.get(field), int)]
            for field in fields
        }
        return {
            "status": (
                "available"
                if records and all(available[field] for field in fields[:3])
                else "partial_or_unavailable"
            ),
            **{
                field: sum(values) if values else None
                for field, values in available.items()
            },
            "calls": len(records),
        }

    @staticmethod
    def _payload_composition(payloads: dict[str, str]) -> str:
        kinds = set(payloads.values())
        if not kinds:
            return "none"
        if kinds == {"pattern_only"}:
            return "pattern"
        if kinds == {"reviewed_fragment"}:
            return "fragment"
        return "mixed"

    @staticmethod
    def _generation_path(generated) -> str:
        actions = set(generated.validator_actions)
        if "bounded_reaction_only_realizer" in actions:
            return "bounded_template_realizer"
        if generated.attempts > 1 or any(
            action.startswith("retry_") for action in actions
        ):
            return (
                "retry_fallback"
                if generated.generation_status == "contract_fallback"
                else "retry_model_generation"
            )
        if generated.generation_status == "contract_fallback":
            return "bounded_fallback"
        return "normal_model_generation"

    @staticmethod
    def _language_of(text: str) -> str:
        has_japanese = any(
            "\u3040" <= character <= "\u30ff" for character in text
        )
        has_cjk = any("\u4e00" <= character <= "\u9fff" for character in text)
        has_latin = any(character.isascii() and character.isalpha() for character in text)
        present = sum((has_japanese, has_cjk, has_latin))
        if present > 1:
            return "mixed"
        if has_japanese:
            return "ja"
        if has_cjk:
            return "zh"
        if has_latin:
            return "en"
        return "unknown"

    @staticmethod
    def _scene_for_current_turn(
        previous: SceneState,
        message: str,
        signals,
    ) -> SceneState:
        emotion = signals.get("user_emotion")
        value = emotion.value if emotion is not None and emotion.status == "observed" else None
        if value in {"distressed", "negative", "angry"}:
            mood = "supportive" if value != "angry" else "careful"
            energy = 0.35
            emotional = message[:80]
        elif value == "positive":
            mood, energy, emotional = "upbeat", 0.7, message[:80]
        else:
            mood = "neutral"
            energy = round((previous.energy + 0.5) / 2, 3)
            emotional = None
        return SceneState(
            current_topic=message[:60],
            mood=mood,
            energy=energy,
            response_tempo="slow" if energy < 0.4 else "normal",
            emotional_context=emotional,
            unresolved_threads=previous.unresolved_threads,
        )

    async def retry_post_turn(self, failure_id: str) -> dict[str, object]:
        if self.request_state is None:
            raise KeyError(failure_id)
        row = self.request_state.get_post_turn_failure(failure_id)
        if row is None:
            raise KeyError(failure_id)
        payload = json.loads(str(row["payload_json"]))
        async with self._user_lock(payload["user_id"]):
            return await self._retry_post_turn(failure_id)

    async def _retry_post_turn(self, failure_id: str) -> dict[str, object]:
        row = self.request_state.get_post_turn_failure(failure_id)
        if str(row["status"]) == "completed":
            return {"id": failure_id, "status": "completed"}
        payload = json.loads(str(row["payload_json"]))
        try:
            await self.post_turn.process(
                user_id=payload["user_id"],
                conversation_id=payload["conversation_id"],
                user_message_id=payload["user_message_id"],
                user_message=payload["user_message"],
                previous_relationship=self.memory_store.get_relationship(payload["user_id"]),
                previous_scene=self.memory_store.get_scene(payload["conversation_id"]),
                permission_events=[
                    ExplicitPreferenceEvent.model_validate(item)
                    for item in payload.get("permission_events", [])
                ],
            )
        except Exception as exc:
            self.request_state.update_post_turn_error(failure_id, exc)
            raise ServiceFailure(
                "post_turn_retry_failed",
                "post-turn retry failed and remains pending",
                retryable=True,
            ) from exc
        self.request_state.mark_post_turn_completed(failure_id)
        self.conversations.mark_post_turn_completed(str(row["request_id"]))
        return {"id": failure_id, "status": "completed"}

    def pending_post_turn_failures(self) -> list[dict[str, object]]:
        return self.request_state.list_post_turn_failures() if self.request_state else []
