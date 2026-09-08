from __future__ import annotations

import logging
import re
from pathlib import Path

from ..model_gateway import ModelGateway
from ..models import (
    ChatMessage,
    DialoguePlan,
)


class DialoguePlanner:

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

    async def plan(
        self,
        message: str,
        history: list[ChatMessage],
        summary: str | None = None,
    ) -> DialoguePlan:

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
                    DialoguePlan,
                )
            else:
                plan, provider_reasons = await generate_with_status(
                    self.profile_name,
                    messages,
                    DialoguePlan,
                )
                degraded_reasons.extend(provider_reasons)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "planner structured output failed; using deterministic fallback",
                extra={"error": str(exc)},
            )
            plan = self._fallback_plan(message, history)
            degraded_reasons.append("planner_deterministic_fallback")

        if plan.need_wiki:
            try:
                await self.model_gateway.unload(self.profile_name)
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "planner model unload failed",
                    extra={"error": str(exc)},
                )
                degraded_reasons.append("planner_unload_failed")
        plan.degraded_reasons = list(dict.fromkeys(degraded_reasons))
        return plan

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
