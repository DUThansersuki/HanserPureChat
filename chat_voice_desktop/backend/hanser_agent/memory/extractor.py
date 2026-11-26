from __future__ import annotations

import re

from ..config import MemoryConfig
from ..models import MemoryCandidate, MemoryDecision
from ..persona.signals import extract_explicit_preference_events


_PERSONAL_NAME = re.compile(
    r"我叫([A-Za-z0-9_\u4e00-\u9fff]{1,20}?)"
    r"(?=(?:也)?(?:可以)?叫我|[，,。！？、/\s]|$)"
)
_NICKNAME = re.compile(
    r"(?:以后|之后|往后|平时|也)?(?:可以)?叫我"
    r"([A-Za-z0-9_\u4e00-\u9fff]{1,20}?)"
    r"(?=(?:也)?(?:可以)?叫我|每句话|[，,。！？、/\s]|$)"
)
_NEGATIVE_ADDRESS = re.compile(
    r"(?:以后)?(?:别|不要|不许|不用)(?:再)?叫我"
    r"(?P<value>[A-Za-z0-9_\u4e00-\u9fff]{1,20})"
    r"(?=[，,。！？、/\s]|$)"
)
_FAN_NAMES = {"毛怪", "毛怪们"}
_PREFERENCE_OBJECT = (
    r"(?P<object>[^，。！？,;\s]{1,40}?)"
    r"(?=也喜欢|还喜欢|\s+我(?:现在|其实|真的)?"
    r"(?:更|最|也|还)?(?:不(?:太)?)?喜欢|[，。！？,;]|$)"
)
_DOUBLE_NEG_LIKE = re.compile(r"我不是不(?:太)?喜欢" + _PREFERENCE_OBJECT)
_DISLIKE = re.compile(r"我(?:现在|其实|真的|之前)?不(?:太)?喜欢" + _PREFERENCE_OBJECT)
_LIKE = re.compile(r"(?:我)?(?:现在|其实|真的)?(?:更|最|也|还)?喜欢" + _PREFERENCE_OBJECT)
_REMEMBER = re.compile(r"(?:记住|你要记得)[：:，, ]*([^。！？\n]{2,80})")
_TIME = re.compile(r"今天|今晚|明天|明晚|后天|这周|下周|周[一二三四五六日天]|\d{1,2}[月号日点]")
_QUESTION = re.compile(r"[？?]|(?:吗|么|是不是|还记得|记得).{0,4}$")
_HYPOTHETICAL = re.compile(r"(?:如果|假如|要是|倘若|万一)")
_CORRECTION = re.compile(r"(?:说错了?|更正|纠正|改成|其实|之前说错)")
_QUOTED = re.compile(r"“[^”]*”|「[^」]*」|\"[^\"]*\"")
_SHARED_EVENT = re.compile(r"(?:我们|咱们)(?:刚才|之前|上次)([^。！？\n]{2,80})")
_EVENTS = ("考试", "面试", "答辩", "旅行", "出差", "约会", "手术", "复诊", "生日", "比赛", "演出", "开学", "毕业")


def user_claims(message: str) -> list[str]:
    """Keep local user assertions; quoted and reported clauses do not own facts.

    Reporting scope survives comma-separated clauses. An explicit contrast or
    the end of a quoted report can introduce the user's own assertion again.
    """
    quoted = list(_QUOTED.finditer(message))
    visible = _QUOTED.sub(lambda match: " " * len(match.group()), message)
    claims: list[str] = []
    reported = False
    own_subject = False
    previous_start = 0
    for match in re.finditer(
        r"[^，,。！？!?；;\n]+?(?:[，,。！？!?；;\n]|(?=(?:但是|但|不过|而|至于)我)|$)",
        visible,
    ):
        clause = match.group().strip()
        contrast = bool(re.match(r"(?:但是|但|不过|而|至于)(?:我|我的)", clause))
        quote_ended = any(previous_start < item.end() <= match.start() for item in quoted)
        if contrast or quote_ended:
            reported = False
        subject = re.match(
            r"(?:但是|但|不过|而|至于|其实|现在|之前|以后|平时)?"
            r"我(?!的?(?:朋友|同学|同事|家人|妈妈|爸爸|哥哥|姐姐|弟弟|妹妹))",
            clause,
        )
        if subject and not reported:
            own_subject = True
        elif not re.match(r"(?:也|还|更|最|不(?:太)?喜欢|喜欢|可以叫我)", clause):
            own_subject = False
        instruction = bool(re.match(
            r"(?:以后|之后|往后|平时|这次|本次|这个|这段|长期|默认|一直|通常)?"
            r"(?:聊天|对话|会话)?(?:都|请|希望你)?(?:回答|简短回答|详细回答|少说|多说|别|不要|不许|不用|叫我|可以叫我)",
            clause,
        ))
        report = re.search(r"(?:说[：:]?|表示|告诉我|写道|转述|声称|提到)", clause)
        # Speech verbs in direct instructions and personal assertions do not
        # introduce a third-party narrator. Explicit retelling still does.
        if report and not _CORRECTION.search(clause) and (
            "转述" in clause or not (instruction or subject)
        ):
            reported = True
        event_local = bool(re.match(r"(?:今天|今晚|明天|明晚|后天|这周|下周|考试|面试|答辩|旅行|出差|约会|手术|复诊|生日|比赛|演出|开学|毕业)", clause))
        if not reported and (own_subject or instruction or event_local or _HYPOTHETICAL.search(clause)):
            claims.append(clause)
        previous_start = match.start()
        if clause.endswith(tuple("。！？!?；;\n")):
            reported = False
            own_subject = False
    return claims


def answer_length_preference(message: str) -> tuple[str, str] | None:
    selected = None
    priority = {"global": 0, "conversation": 1, "current_turn": 2}
    for clause in user_claims(message):
        if _QUESTION.search(clause) or _HYPOTHETICAL.search(clause):
            continue
        value = MemoryCandidateExtractor._length_preference(clause)
        if value is None:
            continue
        if re.search(r"(?:这次|本次|这个|这段)(?:聊天|对话|会话)", clause):
            scope = "conversation"
        elif re.search(r"这次|本次|这条|这一条|当前|今天|先", clause):
            scope = "current_turn"
        elif re.search(r"以后|之后|往后|平时|长期|默认|一直|通常|我偏好|我喜欢", clause):
            scope = "global"
        else:
            scope = "current_turn"
        if selected is None or priority[scope] >= priority[selected[1]]:
            selected = value, scope
    return selected


class MemoryCandidateExtractor:
    """Extract assertions with their semantics; the gate decides eligibility."""

    def extract(self, *, user_id: str, conversation_id: str, message_id: str, message: str) -> list[MemoryCandidate]:
        items: list[MemoryCandidate] = []
        for clause in user_claims(message):
            items.extend(self._extract_claim(
                user_id=user_id, conversation_id=conversation_id,
                message_id=message_id, message=clause, owned_visible=clause,
            ))
        # Requests and claimed shared history remain visible to the gate, which
        # rejects them as unsupported evidence; they are not personal assertions.
        if _REMEMBER.search(message) or _SHARED_EVENT.search(message):
            items.extend(item for item in self._extract_claim(
                user_id=user_id, conversation_id=conversation_id,
                message_id=message_id, message=message,
            ) if item.predicate in {"requested_memory", "shared_event"})
        return list({item.memory_key: item for item in items}.values())

    def _extract_claim(self, *, user_id: str, conversation_id: str, message_id: str,
                       message: str, owned_visible: str | None = None) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []
        visible = _NEGATIVE_ADDRESS.sub("", owned_visible if owned_visible is not None else "\n".join(user_claims(message)))
        base_assertion = self._assertion_type(visible if visible.strip() else message)

        for event in extract_explicit_preference_events(
            message, source_message_id=message_id
        ):
            if event.scope != "user":
                continue
            target_key = self._key(event.target) if event.target else "*"
            content = (
                f"用户长期表达偏好：{event.feature}={event.decision}"
                + (f"，对象={event.target}" if event.target else "")
            )
            candidates.append(self._candidate(
                user_id, conversation_id, message_id, "user_preference",
                f"preference:expression:{event.feature}:{target_key}",
                content, 0.95, 0.99,
                assertion_type="user_instruction",
                polarity="negative" if event.decision == "deny" else "positive",
                subject="user", predicate="expression_permission",
                object_value=event.decision, memory_scope="global",
            ))

        addresses: list[tuple[str, str, float, list[str]]] = []
        for match in _PERSONAL_NAME.finditer(visible):
            addresses.append((match.group(1), "personal_name", 1.0, ["personal"]))
        for match in _NICKNAME.finditer(visible):
            value = match.group(1)
            kind = "fan_identity" if value in _FAN_NAMES else "nickname"
            normalized = "毛怪" if kind == "fan_identity" else value
            tags = ["fan", "playful"] if kind == "fan_identity" else ["casual"]
            addresses.append(
                (normalized, kind, 0.75 if kind == "fan_identity" else 0.9, tags)
            )
        for value, kind, priority, tags in addresses:
            candidates.append(self._candidate(
                user_id, conversation_id, message_id, "user_fact",
                f"address:{kind}:{self._key(value)}",
                f"用户名字是{value}" if kind == "personal_name" else f"用户可被称为{value}",
                0.9, 0.98,
                assertion_type=base_assertion, polarity="positive", subject="user",
                predicate="preferred_address", object_value=value,
                address_kind=kind, context_tags=tags, address_priority=priority,
            ))

        length = answer_length_preference(message)
        if length and length[1] != "current_turn":
            length_preference, length_scope = length
            candidates.append(self._candidate(
                user_id, conversation_id, message_id, "user_preference",
                "preference:answer_length", length_preference, 0.85, 0.95,
                assertion_type=base_assertion, polarity="positive", subject="user",
                predicate="answer_length", object_value=length_preference,
                memory_scope=length_scope,
            ))

        for value, polarity, _span in self._preferences(visible):
            assertion_type = base_assertion
            if polarity == "negative" and assertion_type == "user_assertion":
                assertion_type = "negated_assertion"
            if _DOUBLE_NEG_LIKE.search(visible) and polarity == "positive":
                assertion_type = "correction"
            candidates.append(self._candidate(
                user_id, conversation_id, message_id, "user_preference",
                f"preference:like:{self._key(value)}",
                f"用户{'不' if polarity == 'negative' else ''}喜欢{value}",
                0.7, 0.9 if polarity == "negative" else 0.88,
                assertion_type=assertion_type, polarity=polarity, subject="user",
                predicate="likes", object_value=value,
            ))

        event = next((value for value in _EVENTS if value in visible), None)
        if event and _TIME.search(visible):
            event_assertion = base_assertion
            if self._is_negated_event(visible, event):
                event_assertion = "negated_assertion"
            candidates.append(self._candidate(
                user_id, conversation_id, message_id, "unresolved_thread",
                f"unresolved:{event}", visible.strip(), 0.88, 0.95,
                assertion_type=event_assertion,
                polarity="negative" if event_assertion == "negated_assertion" else "positive",
                subject="user", predicate="has_upcoming_event", object_value=event,
            ))

        remembered = _REMEMBER.search(message)
        if remembered:
            value = remembered.group(1).strip()
            candidates.append(self._candidate(
                user_id, conversation_id, message_id, "user_fact",
                f"instruction:{self._key(value)[:32]}", value, 0.8, 0.92,
                assertion_type="user_instruction", polarity="neutral", validity="unverified",
                subject="user", predicate="requested_memory", object_value=value,
            ))

        shared = _SHARED_EVENT.search(message)
        if shared:
            value = shared.group(0).strip()
            candidates.append(self._candidate(
                user_id, conversation_id, message_id, "shared_event",
                f"shared:{self._key(shared.group(1))[:32]}", value, 0.72, 0.85,
                assertion_type=base_assertion, polarity="positive", validity="unverified",
                subject="user_and_hanser", predicate="shared_event",
                object_value=shared.group(1).strip(),
            ))

        return list({item.memory_key: item for item in candidates}.values())

    def closed_memory_keys(self, message: str) -> list[str]:
        message = "\n".join(
            clause for clause in user_claims(message)
            if self._assertion_type(clause) not in {"question", "hypothetical"}
        )
        keys: list[str] = []
        for event in _EVENTS:
            event_present = event in message or (event == "考试" and "考完试" in message)
            if not event_present:
                continue
            completed = any(marker in message for marker in (
                f"{event}完", f"{event}结束", f"{event}取消",
                "已经考完", "已经结束", "不去了", "不用了",
            ))
            if completed or self._is_negated_event(message, event):
                keys.append(f"unresolved:{event}")
        return keys

    def retired_address_values(self, message: str) -> list[str]:
        visible = "\n".join(
            clause for clause in user_claims(message)
            if self._assertion_type(clause) not in {"question", "hypothetical"}
        )
        values: list[str] = []
        for match in _NEGATIVE_ADDRESS.finditer(visible):
            value = match.group("value")
            normalized = "毛怪" if value in _FAN_NAMES else value
            if normalized not in values:
                values.append(normalized)
        return values

    @staticmethod
    def _assertion_type(message: str) -> str:
        if _HYPOTHETICAL.search(message):
            return "hypothetical"
        if _QUESTION.search(message):
            return "question"
        if _CORRECTION.search(message):
            return "correction"
        return "user_assertion"

    @staticmethod
    def _preferences(message: str) -> list[tuple[str, str, tuple[int, int]]]:
        found: list[tuple[str, str, tuple[int, int]]] = []
        occupied: list[tuple[int, int]] = []
        for regex, polarity in ((_DOUBLE_NEG_LIKE, "positive"), (_DISLIKE, "negative")):
            for match in regex.finditer(message):
                value = match.group("object").strip()
                if value:
                    found.append((value, polarity, match.span()))
                    occupied.append(match.span())
        for match in _LIKE.finditer(message):
            if any(start <= match.start() < end for start, end in occupied):
                continue
            if re.search(r"(?:不|没)(?:太)?$", message[max(0, match.start() - 3):match.start()]):
                continue
            value = match.group("object").strip()
            if value:
                found.append((value, "positive", match.span()))
        found.sort(key=lambda item: item[2][0])
        return found

    @staticmethod
    def _is_negated_event(message: str, event: str) -> bool:
        return bool(re.search(rf"(?:没有|没|不用|取消|不去).{{0,4}}{re.escape(event)}", message))

    @staticmethod
    def _length_preference(message: str) -> str | None:
        if any(value in message for value in ("回答短一点", "简短回答", "少说一点")):
            return "用户偏好简短回答"
        if any(value in message for value in ("回答详细一点", "详细回答", "多说一点")):
            return "用户偏好详细回答"
        return None

    @staticmethod
    def _key(value: str) -> str:
        return re.sub(r"\s+", "", value).casefold()

    @staticmethod
    def _candidate(
        user_id: str, conversation_id: str, message_id: str, memory_type: str,
        memory_key: str, content: str, importance: float, confidence: float, *,
        assertion_type: str, polarity: str, validity: str = "asserted",
        subject: str | None = None, predicate: str | None = None,
        object_value: str | None = None,
        address_kind: str | None = None,
        context_tags: list[str] | None = None,
        address_priority: float = 0.0,
        memory_scope: str = "global",
    ) -> MemoryCandidate:
        return MemoryCandidate(
            user_id=user_id, conversation_id=conversation_id, type=memory_type,
            memory_key=memory_key, content=content, importance=importance,
            confidence=confidence, source_message_ids=[message_id],
            assertion_type=assertion_type, polarity=polarity, validity=validity,
            subject=subject, predicate=predicate, object_value=object_value,
            address_kind=address_kind, context_tags=context_tags or [],
            address_priority=address_priority,
            memory_scope=memory_scope,
        )


class MemoryWriteGate:
    def __init__(self, config: MemoryConfig):
        self.config = config

    def evaluate(self, candidates: list[MemoryCandidate]) -> list[MemoryDecision]:
        return [self._decision(item) for item in candidates]

    def select(self, candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
        return [item.candidate for item in self.evaluate(candidates) if item.accepted]

    def _decision(self, item: MemoryCandidate) -> MemoryDecision:
        if item.assertion_type == "question":
            return MemoryDecision(candidate=item, accepted=False, reason="question_not_assertion")
        if item.assertion_type == "hypothetical":
            return MemoryDecision(candidate=item, accepted=False, reason="hypothetical_not_assertion")
        if item.assertion_type == "user_instruction" and item.predicate != "expression_permission":
            return MemoryDecision(candidate=item, accepted=False, reason="remember_request_is_not_evidence")
        if item.type in {"shared_event", "relationship_event", "episode"} and (
            item.assertion_type != "confirmed_conversation_event" or item.validity != "verified"
        ):
            return MemoryDecision(candidate=item, accepted=False, reason="shared_reality_unverified")
        if item.type == "unresolved_thread" and item.polarity == "negative":
            return MemoryDecision(candidate=item, accepted=False, reason="event_negated_or_closed")
        if item.importance < self.config.min_importance:
            return MemoryDecision(candidate=item, accepted=False, reason="importance_below_threshold")
        if item.confidence < self.config.min_confidence:
            return MemoryDecision(candidate=item, accepted=False, reason="confidence_below_threshold")
        return MemoryDecision(candidate=item, accepted=True, reason="accepted")
