from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel

from ..config import MemoryConfig
from ..models import MemoryItem
from ..retrieval import Embedder, VectorStore
from .store import MemoryStore


MEMORY_COLLECTION = "memories"


class RetrievedMemory(BaseModel):
    memory: MemoryItem
    score: float
    semantic_score: float


class MemoryRetriever:
    def __init__(
        self,
        *,
        store: MemoryStore,
        embedder: Embedder,
        vector_store: VectorStore,
        config: MemoryConfig,
    ):
        self.store = store
        self.embedder = embedder
        self.vector_store = vector_store
        self.config = config

    async def index(self, memory: MemoryItem) -> None:
        vector = (await self.embedder.embed_documents([memory.content]))[0]
        self.vector_store.upsert(
            MEMORY_COLLECTION,
            self.embedder.model_name,
            [(memory.id, vector)],
        )
        self.store.set_embedding_ref(memory.id)

    def delete_index(self, memory_id: str) -> None:
        self.vector_store.delete_items(MEMORY_COLLECTION, [memory_id])

    async def search(
        self,
        *,
        query: str,
        user_id: str,
        conversation_id: str | None = None,
        top_k: int | None = None,
    ) -> list[RetrievedMemory]:
        eligible_ids = self.store.eligible_memory_ids(
            user_id=user_id, conversation_id=conversation_id
        )
        if not eligible_ids:
            return []
        vector = (await self.embedder.embed_queries([query]))[0]
        vector_hits = self.vector_store.search_filtered(
            MEMORY_COLLECTION,
            self.embedder.model_name,
            vector,
            item_ids=eligible_ids,
            top_k=self.config.candidate_pool_size,
        )
        semantic = {item.item_id: max(0.0, item.score) for item in vector_hits}
        memories = self.store.active_memories_by_ids(
            user_id=user_id,
            memory_ids=[item.item_id for item in vector_hits],
        )
        now = datetime.now(timezone.utc)
        ranked = [
            RetrievedMemory(
                memory=item,
                semantic_score=semantic[item.id],
                score=round(
                    semantic[item.id] * 0.45
                    + item.importance * 0.25
                    + self._recency(item, now) * 0.15
                    + item.confidence * 0.15,
                    6,
                ),
            )
            for item in memories
        ]
        selected = sorted(
            ranked,
            key=lambda item: item.score,
            reverse=True,
        )[: top_k or self.config.retrieval_top_k]
        self.store.mark_accessed([item.memory.id for item in selected])
        return selected

    @staticmethod
    def _recency(memory: MemoryItem, now: datetime) -> float:
        days = max(0.0, (now - memory.created_at).total_seconds() / 86400)
        return max(0.0, 1.0 - days / 365.0)
