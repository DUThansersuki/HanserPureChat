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
        positive = any(value in user_message for value in self._POSITIVE)
        tense = any(value in user_message for value in self._TENSION)
        playful = any(value in user_message for value in ("哈哈", "笨", "傻", "逗你"))
        return RelationshipState(
            familiarity=self._clamp(previous.familiarity + 0.01),
            warmth=self._clamp(previous.warmth + (0.01 if positive else 0.0)),
            trust=self._clamp(previous.trust + (0.01 if memory_writes else 0.0)),
            teasing_permission=self._clamp(
                previous.teasing_permission + (0.015 if playful else 0.0)
            ),
            shared_context_density=self._clamp(
                previous.shared_context_density + (0.01 if memory_writes else 0.0)
            ),
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
        if any(value in user_message for value in self._LOW_ENERGY):
            mood, energy, emotional = "supportive", 0.35, user_message[:80]
        elif any(value in user_message for value in self._HIGH_ENERGY):
            mood, energy, emotional = "upbeat", 0.75, user_message[:80]
        else:
            mood, energy, emotional = previous.mood, 0.5, None
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
