from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .store import MemoryStore, utc_now
from ..agent.conversation import ConversationStore
from ..config import MemoryConfig
from ..models import ConversationSummary
from .continuity import ConversationContinuity, continuity_tokens


class ConversationSummarizer:
    """Live state plus source-backed interaction recall, within a fixed budget."""

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
        self.continuity = ConversationContinuity(memories)

    def update(self, conversation_id: str, *, user_id: str) -> ConversationSummary | None:
        self.continuity.index_new(user_id=user_id, conversation_id=conversation_id)
        message_count = self.conversations.message_count(
            conversation_id, user_id=user_id
        )
        current = self.memories.get_summary(conversation_id)
        if message_count < self.config.summary_trigger_messages:
            return current

        through_index = message_count - self.config.recent_messages - 1
        if through_index % 2 == 0:
            through_index -= 1  # A summary boundary never splits a saved pair.
        previous_index = current.through_message_index if current else -1
        if current and (
            through_index - previous_index
            < self.config.summary_interval_messages
        ):
            through_index = previous_index
        content = self._compose(user_id=user_id, conversation_id=conversation_id,
                                through_index=through_index)
        if current and current.through_message_index == through_index and current.content == content:
            return current
        summary = ConversationSummary(
            conversation_id=conversation_id,
            content=content,
            through_message_index=through_index,
            updated_at=utc_now(),
        )
        self.memories.save_summary(summary)
        return summary

    def context(self, conversation_id: str, *, user_id: str, query: str,
                summary: ConversationSummary | None) -> str | None:
        # Backfill pre-existing conversations once; subsequent calls seek from
        # per-conversation checkpoints rather than re-tokenizing old history.
        self.continuity.index_new(user_id=user_id)
        content = self._compose(user_id=user_id, conversation_id=conversation_id,
                                through_index=summary.through_message_index if summary else -1,
                                query=query)
        return content or None

    def _compose(self, *, user_id: str, conversation_id: str, through_index: int,
                 query: str = "") -> str:
        facts = self.continuity.facts(user_id=user_id, conversation_id=conversation_id)
        recalled = self.continuity.search(user_id=user_id, conversation_id=conversation_id,
                                         through_index=through_index, query=query) if query else []
        recent = self.continuity.turns(user_id=user_id, conversation_id=conversation_id,
                                      through_index=through_index)
        entries = []
        for fact in facts:
            status = "事项已结束" if fact["status"] == "closed" else (
                "未决事项" if fact["type"] == "unresolved_thread" else "当前记忆")
            entries.append(f'[{status} memory={fact["id"]} 记录时间={self._source_time(fact["created_at"])}] {fact["content"]}')
        # Higher priority entries precede ordinary recent interactions. Keep a
        # slot for recall even with many active facts, and never treat a quote
        # as proof of a real-world shared experience.
        turns = list({row["id"]: row for row in [*recalled, *reversed(recent)]}.values())
        budget = self.config.summary_max_chars
        facts_budget = budget // 2 if turns else budget
        state = self._take_priority(entries, facts_budget)
        recall = self._render_turns(turns, conversation_id=conversation_id, query=query,
                                    budget=budget - len(state) - (1 if state else 0))
        return "\n".join(value for value in (state, recall) if value)

    def _render_turns(self, turns: list[dict], *, conversation_id: str, query: str, budget: int) -> str:
        selected = []
        for turn in turns:
            label = "旧会话" if turn["conversation_id"] != conversation_id else "本会话"
            header = (f'[{label}已保存互动 {turn["conversation_id"]} 时间={self._source_time(turn["occurred_at"])} '
                      f'sources={turn["id"]},{turn["assistant_message_id"]}] ')
            remaining = budget - len("\n".join(selected)) - (1 if selected else 0)
            quote_budget = min(remaining, 420) - len(header) - len("用户原话： Hanser当时回答：")
            if quote_budget < 40:
                continue
            # Truncate only the quotes, never message IDs or speaker labels.
            user = self._focused_excerpt(turn["user_text"], query, quote_budget // 2)
            assistant = self._focused_excerpt(turn["assistant_text"], query, quote_budget - quote_budget // 2)
            selected.append(header + f"用户原话：{user} Hanser当时回答：{assistant}")
        return "\n".join(selected)

    @staticmethod
    def _source_time(value: str) -> str:
        return datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M+08")

    @classmethod
    def _focused_excerpt(cls, value: str, query: str, limit: int) -> str:
        terms = set(continuity_tokens(query)) if query else set()
        sentences = [piece.strip() for piece in re.split(r"(?<=[。！？!?；;\n])", value) if piece.strip()]
        if terms and len(sentences) > 1:
            scored = [(len(terms.intersection(continuity_tokens(piece))), index, piece)
                      for index, piece in enumerate(sentences)]
            ranked = sorted(scored, key=lambda item: (-item[0], item[1]))
            if ranked[0][0]:
                selected = sorted(ranked[:2], key=lambda item: item[1])
                value = " … ".join(item[2] for item in selected)
        # Center a single oversized sentence on its most specific query term.
        if terms and len(value) > limit:
            for term in sorted(terms, key=lambda item: (-len(item), item)):
                position = value.casefold().find(term)
                if position >= 0:
                    start = max(0, position - limit // 3)
                    return ("…" if start else "") + value[start:start + limit - 2] + "…"
        return cls._excerpt(value, limit)

    @classmethod
    def _take_priority(cls, entries: list[str], budget: int) -> str:
        selected = []
        for entry in entries:
            remaining = budget - len("\n".join(selected)) - (1 if selected else 0)
            if remaining < 80:
                break
            # Source IDs are retained; truncation applies to quoted content.
            selected.append(cls._excerpt(entry, min(remaining, max(180, budget // 2))))
        return "\n".join(selected)

    @staticmethod
    def _compact(value: str) -> str:
        return " ".join(value.split())

    @staticmethod
    def _excerpt(value: str, limit: int) -> str:
        value = ConversationSummarizer._compact(value)
        if len(value) <= limit:
            return value
        marker = " …[中段省略]… "
        remaining = max(0, limit - len(marker))
        front = remaining * 2 // 3
        return value[:front] + marker + value[-(remaining - front):]

    @staticmethod
    def _fit_complete_entries(entries: list[str], limit: int) -> str:
        selected: list[str] = []
        # Preserve a bounded beginning and end of an oversized entry rather
        # than letting the newest message suppress all usable context.
        entries = [ConversationSummarizer._excerpt(entry, max(40, limit // 3)) for entry in entries]
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
