from __future__ import annotations

from .store import MemoryStore, utc_now
from ..agent.conversation import ConversationStore
from ..config import MemoryConfig
from ..models import ConversationSummary


class ConversationSummarizer:
    """Bounded extractive L1 summary; it does not create long-term facts."""

    def __init__(
        self,
        *,
        conversations: ConversationStore,
        memories: MemoryStore,
        config: MemoryConfig,
    ):
        self.conversations = conversations
        self.memories = memories
        self.config = config

    def update(self, conversation_id: str, *, user_id: str) -> ConversationSummary | None:
        message_count = self.conversations.message_count(
            conversation_id, user_id=user_id
        )
        current = self.memories.get_summary(conversation_id)
        if message_count < self.config.summary_trigger_messages:
            return current

        through_index = message_count - self.config.recent_messages - 1
        previous_index = current.through_message_index if current else -1
        if current and (
            through_index - previous_index
            < self.config.summary_interval_messages
        ):
            return current

        # Rebuild the covered range so later corrections can annotate older
        # statements instead of carrying a stale append-only summary forever.
        messages = self.conversations.messages_for_summary(
            conversation_id,
            user_id=user_id,
            after_index=-1,
            through_index=through_index,
        )
        annotations = self.memories.memory_annotations_by_source(conversation_id)
        additions = []
        for item in messages:
            labels = annotations.get(item.id, set())
            marker = ""
            if "deleted_from_long_term_memory" in labels:
                marker = "[已从长期记忆删除，仅保留历史记录] "
            elif "closed_event" in labels:
                marker = "[事件已结束] "
            elif "superseded_assertion" in labels and "current_assertion" not in labels:
                marker = "[历史说法，已被修订] "
            elif "current_assertion" in labels:
                marker = "[当前有效断言] "
            additions.append(
                f"{'用户' if item.role == 'user' else 'Hanser'}: "
                f"{marker}{self._compact(item.content)}"
            )
        content = self._fit_complete_entries(
            additions,
            self.config.summary_max_chars,
        )
        summary = ConversationSummary(
            conversation_id=conversation_id,
            content=content,
            through_message_index=through_index,
            updated_at=utc_now(),
        )
        self.memories.save_summary(summary)
        return summary

    @staticmethod
    def _compact(value: str) -> str:
        return " ".join(value.split())

    @staticmethod
    def _fit_complete_entries(entries: list[str], limit: int) -> str:
        selected: list[str] = []
        for entry in reversed(entries):
            candidate = [entry, *selected]
            omitted = len(entries) - len(candidate)
            prefix = f"[已省略{omitted}条较早消息]\n" if omitted > 0 else ""
            if len(prefix) + len("\n".join(candidate)) > limit:
                break
            selected = candidate
        omitted = len(entries) - len(selected)
        prefix = f"[已省略{omitted}条较早消息]\n" if omitted > 0 else ""
        body = "\n".join(selected)
        return (prefix + body)[:limit]
