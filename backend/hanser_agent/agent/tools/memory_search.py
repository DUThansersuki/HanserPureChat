from __future__ import annotations

from pydantic import BaseModel, Field

from ...memory.retriever import MemoryRetriever, RetrievedMemory


class MemorySearchResult(BaseModel):
    memories: list[RetrievedMemory] = Field(default_factory=list)


class MemorySearchTool:
    name = "memory_search"
    description = "检索与当前对话相关的用户长期记忆和共同经历。"

    def __init__(self, retriever: MemoryRetriever):
        self.retriever = retriever

    async def search(
        self,
        *,
        query: str,
        user_id: str,
        conversation_id: str | None = None,
    ) -> MemorySearchResult:
        return MemorySearchResult(
            memories=await self.retriever.search(
                query=query,
                user_id=user_id,
                conversation_id=conversation_id,
            )
        )
