from __future__ import annotations

from ..models import RelationshipState, SceneState


class CharacterStateEngine:
    """Conservative structured state updates without free-text reasoning."""

    _POSITIVE = ("谢谢", "开心", "喜欢", "可爱", "哈哈", "嘿嘿")
    _TENSION = ("生气", "讨厌你", "烦死", "闭嘴", "别理我")
    _LOW_ENERGY = ("累", "困", "难过", "伤心", "哭", "压力")
    _HIGH_ENERGY = ("开心", "激动", "太好了", "哈哈", "赢了")

    def update_relationship(
        self,
        previous: RelationshipState,
        *,
        user_message: str,
        memory_writes: int,
    ) -> RelationshipState:
        tense = any(value in user_message for value in self._TENSION)
        return RelationshipState(
            familiarity=previous.familiarity,
            warmth=previous.warmth,
            trust=previous.trust,
            teasing_permission=previous.teasing_permission,
            shared_context_density=previous.shared_context_density,
            recent_tension=self._clamp(
                previous.recent_tension + 0.04
                if tense
                else previous.recent_tension - 0.01
            ),
        )

    def update_scene(
        self,
        previous: SceneState,
        *,
        user_message: str,
        unresolved_threads: list[str],
    ) -> SceneState:
        reported = any(marker in user_message for marker in ("她说", "他说", "他们说", "我在转述", "只是转述"))
        if not reported and any(value in user_message for value in self._LOW_ENERGY):
            mood, energy, emotional = "supportive", 0.35, user_message[:80]
        elif not reported and any(value in user_message for value in self._HIGH_ENERGY):
            mood, energy, emotional = "upbeat", 0.75, user_message[:80]
        else:
            mood, energy, emotional = "neutral", 0.5, None
        return SceneState(
            current_topic=user_message[:60],
            mood=mood,
            energy=energy,
            response_tempo="slow" if energy < 0.4 else "normal",
            emotional_context=emotional,
            unresolved_threads=unresolved_threads[:8],
        )

    @staticmethod
    def _clamp(value: float) -> float:
        return round(min(1.0, max(0.0, value)), 4)
