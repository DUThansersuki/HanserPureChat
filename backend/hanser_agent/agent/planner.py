from __future__ import annotations

import logging
import re
import hashlib
import json
from collections import OrderedDict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..model_gateway import ModelGateway
from ..models import (
    ChatMessage,
    DialoguePlan,
)


class PlannerDecision(BaseModel):
    """Small, strict wire contract used only for the external planner call."""

    model_config = ConfigDict(extra="forbid")

    intent: Literal[
        "chitchat", "wiki_fact", "followup_fact", "user_memory",
        "relationship", "mixed", "unknown",
    ]
    wiki: bool
    memory: bool = True
    query: str | None = None
    keywords: list[str] = Field(default_factory=list, max_length=5)
    mode: Literal["casual", "factual", "emotional", "playful", "storytelling"]
    sensitivity: Literal["low", "medium", "high"]
    length: Literal["short", "medium", "long"]
    signals: dict[str, object] = Field(default_factory=dict)


class DialoguePlanner:

    _FAST_CHITCHAT = re.compile(
        r"^(?:你?好(?:呀|啊|哇)?|嗨(?:呀)?|哈喽|早上好|上午好|中午好|下午好|晚上好|晚安)[！!。,.，~～ ]*$"
    )
    _CACHE_LIMIT = 256

    _FACT_CUES = (
        "什么时候",
        "哪年",
        "日期",
        "生日",
        "退出",
        "加入",
        "配音",
        "作品",
        "直播",
        "经历",
        "大学",
        "翻译",
        "斗鱼",
        "B站",
        "虚拟",
    )
    _FOLLOWUP_CUES = (
        "为什么",
        "后来",
        "最早",
        "之前",
        "之后",
        "那她",
        "那是",
        "这个",
    )

    def __init__(
        self,
        model_gateway: ModelGateway,
        profile_name: str = "planner",
    ):
        self.model_gateway = model_gateway
        self.profile_name = profile_name

        prompt_path = (
            Path(__file__).parent.parent
            / "prompts"
            / "planner.md"
        )

        self.prompt = prompt_path.read_text(
            encoding="utf-8"
        )
        self._cache: OrderedDict[str, dict[str, object]] = OrderedDict()
        self.last_cache_hit = False
        self.last_fast_path = False

    async def plan(
        self,
        message: str,
        history: list[ChatMessage],
        summary: str | None = None,
    ) -> DialoguePlan:

        self.last_cache_hit = False
        self.last_fast_path = False
        fast_plan = self._fast_plan(message)
        if fast_plan is not None:
            self.last_fast_path = True
            return fast_plan

        cache_key = self._cache_key(message, history, summary)
        cached = self._cache.get(cache_key)
        if cached is not None:
            self._cache.move_to_end(cache_key)
            self.last_cache_hit = True
            return DialoguePlan.model_validate(cached)

        messages = self.build_messages(message, history, summary)

        degraded_reasons: list[str] = []
        try:
            generate_with_status = getattr(
                self.model_gateway, "generate_json_with_status", None
            )
            if generate_with_status is None:
                plan = await self.model_gateway.generate_json(
                    self.profile_name,
                    messages,
                    PlannerDecision,
                )
            else:
                plan, provider_reasons = await generate_with_status(
                    self.profile_name,
                    messages,
                    PlannerDecision,
                )
                degraded_reasons.extend(provider_reasons)
            plan = self._to_dialogue_plan(plan, message)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "planner structured output failed; using deterministic fallback",
                extra={"error": str(exc)},
            )
            plan = self._fallback_plan(message, history)
            degraded_reasons.append("planner_deterministic_fallback")

        profiles = getattr(self.model_gateway, "profiles", {})
        active_profile = profiles.get(self.profile_name) if isinstance(profiles, dict) else None
        if plan.need_wiki and getattr(active_profile, "provider", None) == "ollama":
            try:
                await self.model_gateway.unload(self.profile_name)
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "planner model unload failed",
                    extra={"error": str(exc)},
                )
                degraded_reasons.append("planner_unload_failed")
        plan.degraded_reasons = list(dict.fromkeys(degraded_reasons))
        if not plan.degraded_reasons:
            self._cache[cache_key] = plan.model_dump(mode="json")
            self._cache.move_to_end(cache_key)
            while len(self._cache) > self._CACHE_LIMIT:
                self._cache.popitem(last=False)
        return plan

    def _fast_plan(self, message: str) -> DialoguePlan | None:
        if not self._FAST_CHITCHAT.fullmatch(message.strip()):
            return None
        return DialoguePlan(
            intent="chitchat",
            need_wiki=False,
            standalone_query=message.strip(),
            keywords=[],
            response_mode="casual",
            fact_sensitivity="low",
            target_length="short",
        )

    def _cache_key(
        self,
        message: str,
        history: list[ChatMessage],
        summary: str | None,
    ) -> str:
        payload = {
            "contract": "planner-decision-v1",
            "prompt": self.prompt,
            "message": message,
            "summary": summary,
            "history": [item.model_dump(mode="json") for item in history[-8:]],
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _to_dialogue_plan(plan: PlannerDecision | DialoguePlan, message: str) -> DialoguePlan:
        if isinstance(plan, DialoguePlan):
            return plan
        query = (plan.query or message).strip() or message
        return DialoguePlan(
            intent=plan.intent,
            need_wiki=plan.wiki,
            need_memory=plan.memory,
            need_style_examples=True,
            standalone_query=query,
            keywords=plan.keywords if plan.wiki else [],
            response_mode=plan.mode,
            fact_sensitivity=plan.sensitivity,
            target_length=plan.length,
            persona_signals=plan.signals,
        )

    def build_messages(
        self,
        message: str,
        history: list[ChatMessage],
        summary: str | None = None,
    ) -> list[ChatMessage]:
        messages = [
            ChatMessage(
                role="system",
                content=self.prompt,
            ),
        ]
        if summary:
            messages.append(
                ChatMessage(
                    role="system",
                    content=f"[CONVERSATION SUMMARY]\n{summary}",
                )
            )
        messages.extend(history[-8:])
        messages.append(ChatMessage(role="user", content=message))
        return messages

    def _fallback_plan(
        self,
        message: str,
        history: list[ChatMessage],
    ) -> DialoguePlan:
        previous_user = next(
            (
                item.content
                for item in reversed(history)
                if item.role == "user"
            ),
            "",
        )
        explicit_fact = any(cue in message for cue in self._FACT_CUES)
        contextual_followup = bool(previous_user) and any(
            cue in message for cue in self._FOLLOWUP_CUES
        )
        need_wiki = explicit_fact or contextual_followup
        standalone_query = (
            f"{previous_user} 后续问题 {message}"
            if contextual_followup
            else message
        )
        keywords = self._fallback_keywords(standalone_query)
        return DialoguePlan(
            intent=(
                "followup_fact"
                if contextual_followup
                else "wiki_fact"
                if need_wiki
                else "chitchat"
            ),
            need_wiki=need_wiki,
            standalone_query=standalone_query,
            keywords=keywords if need_wiki else [],
            response_mode="factual" if need_wiki else "casual",
            fact_sensitivity="high" if need_wiki else "low",
            target_length="medium" if need_wiki else "short",
        )

    def _fallback_keywords(self, text: str) -> list[str]:
        keywords: list[str] = []
        for cue in self._FACT_CUES:
            if cue in text and cue not in keywords:
                keywords.append(cue)
        for value in re.findall(r"[A-Za-z][A-Za-z0-9.-]+", text):
            if value not in keywords:
                keywords.append(value)
        return keywords[:5]
