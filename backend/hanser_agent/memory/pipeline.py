from __future__ import annotations

from pydantic import BaseModel, Field

from ..models import (
    ConversationSummary,
    MemoryDecision,
    MemoryItem,
    RelationshipState,
    SceneState,
)
from .extractor import MemoryCandidateExtractor, MemoryWriteGate
from .retriever import MemoryRetriever
from .state_engine import CharacterStateEngine
from .store import MemoryStore
from .summarizer import ConversationSummarizer
from ..persona.schemas import ExplicitPreferenceEvent


class PostTurnResult(BaseModel):
    memory_writes: list[MemoryItem] = Field(default_factory=list)
    memory_decisions: list[MemoryDecision] = Field(default_factory=list)
    lifecycle_updates: list[MemoryItem] = Field(default_factory=list)
    summary: ConversationSummary | None = None
    relationship_state: RelationshipState
    scene_state: SceneState


class PostTurnPipeline:
    def __init__(
        self,
        *,
        store: MemoryStore,
        extractor: MemoryCandidateExtractor,
        write_gate: MemoryWriteGate,
        retriever: MemoryRetriever,
        summarizer: ConversationSummarizer,
        state_engine: CharacterStateEngine,
    ):
        self.store = store
        self.extractor = extractor
        self.write_gate = write_gate
        self.retriever = retriever
        self.summarizer = summarizer
        self.state_engine = state_engine

    async def process(
        self,
        *,
        user_id: str,
        conversation_id: str,
        user_message_id: str,
        user_message: str,
        previous_relationship: RelationshipState,
        previous_scene: SceneState,
        permission_events: list[ExplicitPreferenceEvent] | None = None,
    ) -> PostTurnResult:
        if self.store.is_post_turn_committed(user_message_id):
            for memory in self.store.memories_by_source_message(user_message_id):
                if self.store.needs_embedding(memory.id):
                    await self.retriever.index(memory)
            return PostTurnResult(
                relationship_state=self.store.get_relationship(user_id),
                scene_state=self.store.get_scene(conversation_id),
                summary=self.store.get_summary(conversation_id),
            )
        candidates = self.extractor.extract(
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=user_message_id,
            message=user_message,
        )
        decisions = self.write_gate.evaluate(candidates)
        writes: list[MemoryItem] = []
        for candidate in [item.candidate for item in decisions if item.accepted]:
            memory, created = self.store.upsert_candidate(candidate)
            if created:
                writes.append(memory)

        lifecycle_updates: list[MemoryItem] = []
        for memory_key in self.extractor.closed_memory_keys(user_message):
            lifecycle_updates.extend(self.store.close_unresolved(
                user_id=user_id,
                conversation_id=conversation_id,
                memory_key=memory_key,
                source_message_id=user_message_id,
                content=user_message.strip(),
            ))
        for object_value in self.extractor.retired_address_values(user_message):
            lifecycle_updates.extend(self.store.retire_address(
                user_id=user_id,
                conversation_id=conversation_id,
                object_value=object_value,
                source_message_id=user_message_id,
                content=user_message.strip(),
            ))

        relationship = self.state_engine.update_relationship(
            previous_relationship,
            user_message=user_message,
            memory_writes=len(writes),
        )
        self.store.save_relationship(user_id, relationship)
        unresolved = [
            item.content
            for item in self.store.list_memories(user_id=user_id)
            if item.type == "unresolved_thread"
        ]
        scene = self.state_engine.update_scene(
            previous_scene,
            user_message=user_message,
            unresolved_threads=unresolved,
        )
        self.store.save_scene(conversation_id, scene)
        summary = self.summarizer.update(conversation_id, user_id=user_id)
        self.store.save_permission_events(
            user_id=user_id,
            conversation_id=conversation_id,
            events=permission_events or [],
        )
        self.store.mark_post_turn_committed(
            user_message_id=user_message_id,
            conversation_id=conversation_id,
        )
        for memory in writes:
            if self.store.needs_embedding(memory.id):
                await self.retriever.index(memory)
        return PostTurnResult(
            memory_writes=writes,
            memory_decisions=decisions,
            lifecycle_updates=lifecycle_updates,
            summary=summary,
            relationship_state=relationship,
            scene_state=scene,
        )
