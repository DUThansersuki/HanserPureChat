from __future__ import annotations

import logging
import re
import hashlib
import json
from collections import OrderedDict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..failures import ServiceFailure
from ..model_gateway import ModelGateway
from ..models import (
    ChatMessage,
    DialoguePlan,
)


class PlannerDecision(BaseModel):
    """Small semantic wire contract used only for the external planner call."""

    model_config = ConfigDict(extra="ignore")

    intent: Literal[
        "chitchat", "wiki_fact", "followup_fact", "user_memory",
        "relationship", "mixed", "unknown",
    ]
    mode: Literal["casual", "factual", "emotional", "playful", "storytelling"] = "casual"
    length: Literal["short", "medium", "long"] = "short"
    dialogue_function: Literal[
        "share", "confirm", "correct", "advice", "playful", "end", "other", "unknown",
    ] = "unknown"
    query: str | None = Field(default=None, min_length=1)
    keywords: list[str] = Field(default_factory=list, max_length=5)
    signals: dict[str, object] = Field(default_factory=dict)

    @field_validator("signals", mode="before")
    @classmethod
    def tolerate_unavailable_signals(cls, value: object) -> object:
        return value if isinstance(value, dict) else {}

    @field_validator("mode", mode="before")
    @classmethod
    def normalize_observed_mode_confusion(cls, value: object) -> object:
        # Reproduced with "朋友说她以前做过翻译，我该怎么回应？": the
        # provider put the dialogue function "advice" in the optional tone
        # field. Preserve the valid core intent and use the neutral tone.
        return "casual" if value == "advice" else value

    @field_validator("query", mode="before")
    @classmethod
    def tolerate_blank_query(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            return value.strip() or None
        return None

    @field_validator("keywords", mode="before")
    @classmethod
    def tolerate_unavailable_keywords(cls, value: object) -> object:
        if not isinstance(value, list):
            return []
        return list(dict.fromkeys(
            item.strip() for item in value if isinstance(item, str) and item.strip()
        ))[:5]


class DialoguePlanner:

    _FAST_GREETING = re.compile(
        r"^(?:你?好(?:呀|啊|哇)?|嗨(?:呀)?|哈喽|早上好|上午好|中午好|下午好|晚上好)[！!。,.，~～ ]*$"
    )
    _FAST_END = re.compile(
        r"^(?:晚安|拜拜|先走了|先这样|不聊了|下次聊|回头聊)[！!。,.，~～ ]*$"
    )
    _CACHE_LIMIT = 256

    _FACT_TOPICS = (
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
    _FACT_INTENTS = {"wiki_fact", "followup_fact", "mixed"}
    _DIRECT_FACT_CUES = (
        "什么时候", "哪年", "日期", "生日", "哪个", "哪一", "多少", "是谁",
    )
    _QUESTION_CUES = (
        "吗", "么", "什么", "哪", "谁", "为何", "为什么", "怎么", "是否",
        "有没有", "能不能", "知道", "告诉", "介绍", "讲讲", "说说", "聊聊", "谈谈",
        "查一下", "搜一下",
        "时间", "名字", "?", "？",
    )
    _PERSONA_STORY_TOPICS = (
        "故事", "往事", "回忆", "印象", "一件事", "以前的事", "过去的事",
    )
    _PERSONA_STORY_REQUEST_CUES = (
        "讲讲", "说说", "聊聊", "谈谈", "介绍", "告诉", "分享",
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
        profile = getattr(model_gateway, "profiles", {}).get(profile_name)
        self.input_token_budget = int(
            getattr(profile, "input_token_budget", 3072)
        )
        self._profile_cache_identity = {
            field: getattr(profile, field, None)
            for field in (
                "provider", "endpoint", "model", "max_tokens", "temperature",
                "top_p", "context_window", "input_token_budget", "think",
            )
        }
        self._cache: OrderedDict[str, dict[str, object]] = OrderedDict()

    async def plan(
        self,
        message: str,
        history: list[ChatMessage],
        summary: str | None = None,
    ) -> DialoguePlan:

        fast_plan = self._fast_plan(message)
        if fast_plan is not None:
            return fast_plan

        cache_key = self._cache_key(message, history, summary)
        cached = self._cache.get(cache_key)
        if cached is not None:
            self._cache.move_to_end(cache_key)
            plan = DialoguePlan.model_validate(cached)
            plan.planner_cache_hit = True
            plan.planner_fast_path = False
            plan.planner_failure_kind = None
            plan.planner_route_basis = "cache"
            return plan

        messages = self.build_messages(message, history, summary)
        input_tokens = self._estimate_messages_tokens(messages)
        visible_history = sum(1 for item in messages if item.role != "system") - 1

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
            plan = self._to_dialogue_plan(plan, message, history)
            plan = self._enforce_wiki_gate(plan, message, history)
        except Exception as exc:
            failure_kind = self._failure_kind(exc)
            logging.getLogger(__name__).warning(
                "planner structured output failed; using deterministic fallback",
                extra={
                    "planner_failure_kind": failure_kind,
                    "error_type": type(exc).__name__,
                },
            )
            plan = self._fallback_plan(message, history)
            degraded_reasons.extend([
                "planner_deterministic_fallback",
                f"planner_failure:{failure_kind}",
            ])
            plan.planner_failure_kind = failure_kind
            plan.planner_route_basis = "fallback"

        plan.degraded_reasons = list(dict.fromkeys(degraded_reasons))
        plan.planner_input_estimated_tokens = input_tokens
        plan.planner_history_messages = visible_history
        if not plan.degraded_reasons:
            self._cache[cache_key] = plan.model_dump(mode="json")
            self._cache.move_to_end(cache_key)
            while len(self._cache) > self._CACHE_LIMIT:
                self._cache.popitem(last=False)
        return plan

    def _fast_plan(self, message: str) -> DialoguePlan | None:
        value = message.strip()
        greeting = bool(self._FAST_GREETING.fullmatch(value))
        ending = bool(self._FAST_END.fullmatch(value))
        if not greeting and not ending:
            return None
        return DialoguePlan(
            intent="chitchat",
            need_wiki=False,
            need_memory=False,
            standalone_query=message.strip(),
            keywords=[],
            response_mode="casual",
            fact_sensitivity="low",
            target_length="short",
            dialogue_function="end" if ending else "other",
            continuation_policy="stop" if ending else "optional_coda",
            greeting_turn=greeting,
            planner_fast_path=True,
            planner_route_basis="fast_path",
        )

    def _cache_key(
        self,
        message: str,
        history: list[ChatMessage],
        summary: str | None,
    ) -> str:
        payload = {
            "contract": "planner-decision-v5-query-request-diagnostics",
            "prompt": self.prompt,
            "profile": self._profile_cache_identity,
            "message": message,
            "summary": summary,
            "history": [item.model_dump(mode="json") for item in history[-8:]],
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def _to_dialogue_plan(
        self,
        plan: PlannerDecision | DialoguePlan,
        message: str,
        history: list[ChatMessage],
    ) -> DialoguePlan:
        if isinstance(plan, DialoguePlan):
            return plan
        fallback = self._fallback_plan(message, history)
        function = (
            plan.dialogue_function
            if plan.dialogue_function != "unknown"
            else fallback.dialogue_function
        )
        signals = dict(plan.signals)
        if plan.dialogue_function != "unknown" and "dialogue_function" not in signals:
            signals["dialogue_function"] = {
                "value": plan.dialogue_function,
                "confidence": "medium",
                "evidence_refs": ["current_user"],
            }
        semantic_fact = plan.intent in self._FACT_INTENTS
        fallback_fact_boundary = fallback.need_wiki and plan.intent in {"unknown", "chitchat"}
        need_wiki = semantic_fact or (
            fallback.need_wiki and (plan.intent == "unknown" or fallback_fact_boundary)
        )
        intent = plan.intent
        response_mode = plan.mode
        if not need_wiki and intent in self._FACT_INTENTS:
            intent = "chitchat"
        if not need_wiki and response_mode == "factual":
            response_mode = "casual"
        standalone_query = plan.query or message
        unresolved_followup = False
        if semantic_fact and plan.intent == "followup_fact" and not plan.query:
            context = [item.content for item in history[-4:] if item.content.strip()]
            if context and self._history_has_resolvable_fact_target(history):
                standalone_query = "\n".join([*context, f"当前追问：{message}"])
            else:
                need_wiki = False
                unresolved_followup = True
                intent = "chitchat"
                response_mode = "casual"
                signals.setdefault("unresolved_reference", {
                    "value": True,
                    "confidence": "high",
                    "evidence_refs": ["current_user"],
                })
        return DialoguePlan(
            intent=(
                fallback.intent
                if fallback.need_wiki and (plan.intent == "unknown" or fallback_fact_boundary)
                else intent
            ),
            need_wiki=need_wiki,
            need_memory=True,
            need_style_examples=True,
            standalone_query=standalone_query,
            keywords=(plan.keywords or self._fallback_keywords(standalone_query)) if need_wiki else [],
            response_mode=("factual" if need_wiki else response_mode),
            fact_sensitivity=("high" if need_wiki else "low"),
            target_length=(
                fallback.target_length
                if fallback.need_wiki and (plan.intent == "unknown" or fallback_fact_boundary)
                else plan.length
            ),
            dialogue_function=function,
            continuation_policy=(
                "clarification_required"
                if unresolved_followup
                else self._continuation_policy(function)
            ),
            persona_signals=signals,
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
        current = ChatMessage(
            role="user",
            content=(
                message
                + "\n\n[PLANNER OUTPUT] 这是路由判断任务 现在只输出一个JSON对象"
            ),
        )
        base_tokens = self._estimate_messages_tokens([*messages, current])
        if base_tokens > self.input_token_budget:
            raise ServiceFailure(
                "planner_input_too_large",
                "current message is too large for the planner input budget",
                retryable=False,
                status_code=413,
            )
        remaining = self.input_token_budget - base_tokens
        selected: list[ChatMessage] = []
        for item in reversed(history[-8:]):
            cost = self._estimate_message_tokens(item)
            if cost > remaining:
                break
            selected.append(item)
            remaining -= cost
        if summary and remaining > 16:
            summary_message = ChatMessage(
                role="system",
                content=f"[CONVERSATION SUMMARY 低优先级对话数据]\n{summary}",
            )
            cost = self._estimate_message_tokens(summary_message)
            if cost <= remaining:
                messages.append(summary_message)
        messages.extend(reversed(selected))
        messages.append(current)
        return messages

    @staticmethod
    def _estimate_message_tokens(message: ChatMessage) -> int:
        return max(1, (len(message.content.encode("utf-8")) + 2) // 3) + 4

    @classmethod
    def _estimate_messages_tokens(cls, messages: list[ChatMessage]) -> int:
        return sum(cls._estimate_message_tokens(item) for item in messages)

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
        explicit_fact = self._looks_like_fact_query(message)
        contextual_followup = self._looks_like_fact_query(previous_user) and any(
            cue in message for cue in self._FOLLOWUP_CUES
        )
        need_wiki = explicit_fact or contextual_followup
        standalone_query = (
            f"{previous_user} 后续问题 {message}"
            if contextual_followup
            else message
        )
        keywords = self._fallback_keywords(standalone_query)
        dialogue_function = self._dialogue_function(message)
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
            dialogue_function=dialogue_function,
            continuation_policy=self._continuation_policy(dialogue_function),
            persona_signals=(
                {
                    "dialogue_function": {
                        "value": dialogue_function,
                        "confidence": "medium",
                        "evidence_refs": ["current_user"],
                    }
                }
                if dialogue_function != "unknown"
                else {}
            ),
        )

    @staticmethod
    def _failure_kind(exc: Exception) -> str:
        name = type(exc).__name__
        if name == "ValidationError":
            return "schema_validation"
        if name == "JSONDecodeError":
            return "json_parse"
        code = getattr(exc, "code", None)
        if isinstance(code, str) and code:
            return code
        if isinstance(exc, (KeyError, IndexError, TypeError)):
            return "invalid_response"
        return name.casefold()

    @staticmethod
    def _dialogue_function(message: str) -> str:
        value = " ".join(message.split()).strip("，,。！？!?；;、~～ ")
        if any(marker in value for marker in (
            "你建议", "给我建议", "该怎么办", "帮我想办法", "怎么回复", "怎么回应",
        )):
            return "advice"
        if value in {"对", "对 就这样", "嗯 就这样", "就这样", "第二个", "第二个吧", "我也是"}:
            return "confirm"
        if any(marker in value for marker in (
            "我说错了", "我错了", "不是这个", "更正一下", "你理解错了", "其实是", "我改选",
        )):
            return "correct"
        if value in {"走了", "拜拜", "晚安", "先这样", "不聊了", "下次聊"} or any(
            marker in value for marker in ("该出门了", "晚点见", "先走了", "回头聊")
        ):
            return "end"
        if any(marker in value for marker in ("哈哈", "笑死", "逗你", "开玩笑", "喵")):
            return "playful"
        if value.startswith(("今天", "刚才", "我跟你说", "跟你说个事", "我发现")):
            return "share"
        if value.startswith(("好呀", "当然", "可以", "嗯", "诶呀不")):
            return "confirm"
        return "unknown"

    @staticmethod
    def _continuation_policy(dialogue_function: str) -> str:
        if dialogue_function in {"share", "confirm", "correct", "end"}:
            return "stop"
        if dialogue_function == "playful":
            return "continue_playful"
        return "optional_coda"

    def _fallback_keywords(self, text: str) -> list[str]:
        keywords: list[str] = []
        for cue in self._FACT_TOPICS:
            if cue in text and cue not in keywords:
                keywords.append(cue)
        for value in re.findall(r"[A-Za-z][A-Za-z0-9.-]+", text):
            if value not in keywords:
                keywords.append(value)
        return keywords[:5]

    @classmethod
    def _enforce_wiki_gate(
        cls,
        plan: DialoguePlan,
        message: str,
        history: list[ChatMessage],
    ) -> DialoguePlan:
        """Apply only narrow, reproduced corrections to the semantic route."""

        if not plan.need_wiki:
            return plan
        del history
        compact = "".join(message.split()).strip("！!。,.，~～")
        known_non_requests = (
            compact in {"生日快乐", "祝你生日快乐"}
            or compact.startswith(("我刚看完你的直播", "这个作品挺好听", "这个我也喜欢"))
        )
        if not known_non_requests:
            return plan
        plan.need_wiki = False
        plan.keywords = []
        if plan.intent in {"wiki_fact", "followup_fact", "mixed"}:
            plan.intent = "chitchat"
        if plan.response_mode == "factual":
            plan.response_mode = "casual"
            plan.fact_sensitivity = "low"
        return plan

    @classmethod
    def _looks_like_fact_query(cls, text: str) -> bool:
        if not text:
            return False
        if cls._looks_like_persona_story_request(text):
            return True
        compact = "".join(text.split())
        if compact.strip("！!。,.，~～") in {"生日快乐", "祝你生日快乐"}:
            return False
        if compact.startswith(("我跟你", "我给你", "让我跟你")):
            return False
        has_topic = any(cue in text for cue in cls._FACT_TOPICS)
        persona_ref = any(name in text.casefold() for name in (
            "hanser", "憨色", "憨憨", "你", "你的",
        ))
        return bool(
            has_topic
            and persona_ref
            and cls._looks_like_explicit_information_request(text)
        )

    @classmethod
    def _history_has_resolvable_fact_target(cls, history: list[ChatMessage]) -> bool:
        for item in history[-4:]:
            text = item.content
            if any(topic in text for topic in cls._FACT_TOPICS):
                return True
            if re.search(r"《[^》]{1,80}》", text):
                return True
            if item.role == "user" and any(ref in text.casefold() for ref in (
                "你", "hanser", "憨憨", "憨色",
            )) and cls._looks_like_explicit_information_request(text):
                return True
        return False

    @classmethod
    def _looks_like_persona_story_request(cls, text: str) -> bool:
        compact = "".join(text.casefold().split())
        if not any(topic in compact for topic in cls._PERSONA_STORY_TOPICS):
            return False
        persona_refs = ("你", "你的", "你自己", "hanser", "憨憨", "憨色")
        if not any(ref in compact for ref in persona_refs):
            return False
        asks_about_persona = any(
            f"{cue}{ref}" in compact
            for cue in cls._PERSONA_STORY_REQUEST_CUES
            for ref in persona_refs
        )
        persona_as_subject = any(
            compact.startswith(f"{ref}{cue}")
            for cue in cls._PERSONA_STORY_REQUEST_CUES
            for ref in persona_refs
        )
        return asks_about_persona or persona_as_subject

    @classmethod
    def _looks_like_explicit_information_request(cls, text: str) -> bool:
        if not text:
            return False
        if cls._looks_like_persona_story_request(text):
            return True
        return any(cue in text for cue in cls._QUESTION_CUES)
