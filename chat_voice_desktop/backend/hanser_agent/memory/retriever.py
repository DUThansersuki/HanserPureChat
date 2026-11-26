from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone

from pydantic import BaseModel

from ..config import MemoryConfig
from ..models import MemoryItem
from ..retrieval import bm25_scores, lexical_tokens
from .store import MemoryStore


class RetrievedMemory(BaseModel):
    memory: MemoryItem
    score: float
    semantic_score: float


class MemoryRetriever:
    _TOKEN_CACHE_LIMIT = 256

    def __init__(
        self,
        *,
        store: MemoryStore,
        config: MemoryConfig,
        userdict_path=None,
    ):
        self.store = store
        self.config = config
        self.userdict_path = userdict_path
        self._token_cache: OrderedDict[str, tuple[str, tuple[str, ...]]] = OrderedDict()

    async def index(self, memory: MemoryItem) -> None:
        self.store.set_retrieval_ref(memory.id, f"lexical:{memory.id}")

    def delete_index(self, memory_id: str) -> None:
        self._token_cache.pop(memory_id, None)

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
        memories = self.store.active_memories_by_ids(
            user_id=user_id,
            memory_ids=eligible_ids,
        )
        documents: dict[str, tuple[str, ...]] = {}
        for item in memories:
            source = " ".join(
                value
                for value in (
                    item.content,
                    item.memory_key,
                    item.subject,
                    item.predicate,
                    item.object_value,
                    " ".join(item.context_tags),
                )
                if value
            )
            cached = self._token_cache.get(item.id)
            if cached is None or cached[0] != source:
                cached = (source, lexical_tokens(source, self.userdict_path))
                self._token_cache[item.id] = cached
            self._token_cache.move_to_end(item.id)
            documents[item.id] = cached[1]
        while len(self._token_cache) > self._TOKEN_CACHE_LIMIT:
            self._token_cache.popitem(last=False)
        semantic = bm25_scores(
            query,
            documents,
            userdict_path=self.userdict_path,
        )
        if not semantic:
            return []
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
            if item.id in semantic
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
