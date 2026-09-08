from __future__ import annotations

from collections.abc import Mapping, Sequence


_DENY_MARKERS: dict[str, tuple[str, ...]] = {
    "humor": ("别开玩笑", "不要开玩笑", "别玩梗", "别顺着这个梗", "别逗我"),
    "teasing": ("别逗我", "不要逗我", "别吐槽我", "不要吐槽我"),
    "profanity": ("别说脏话", "不要说脏话", "别爆粗", "不要爆粗"),
    "innuendo": ("别开黄腔", "不要开黄腔", "不要性暗示"),
    "cutesy": ("别卖萌", "不要卖萌", "别撒娇", "不要撒娇", "别装可爱"),
    "address": ("别叫我昵称", "不要叫我昵称", "别再叫", "不要再叫"),
}

_ALLOW_MARKERS: dict[str, tuple[str, ...]] = {
    "humor": ("可以开玩笑", "可以玩梗", "可以顺着这个梗"),
    "teasing": ("可以吐槽我", "可以轻轻吐槽", "可以逗我"),
    "profanity": ("可以说脏话", "可以爆粗"),
    "innuendo": ("可以开黄腔", "可以性暗示"),
    "cutesy": ("可以卖萌", "可以撒娇", "可以装可爱"),
    "address": ("可以叫我",),
}

_PROSPECTIVE_MARKERS = ("下次", "以后", "等我明确说", "等我说")


def infer_expression_permissions(
    history: Sequence[object],
    current_message: str,
    *,
    explicit_overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Recover narrow, explicit permissions from this user's visible history."""

    permissions: dict[str, str] = {}
    messages: list[str] = []
    for item in history:
        role = getattr(item, "role", None)
        content = getattr(item, "content", None)
        if role == "user" and isinstance(content, str):
            messages.append(content)
    messages.append(current_message)

    for message in messages:
        for feature, markers in _DENY_MARKERS.items():
            if any(marker in message for marker in markers):
                permissions[feature] = "deny"
        if any(marker in message for marker in _PROSPECTIVE_MARKERS):
            continue
        for feature, markers in _ALLOW_MARKERS.items():
            if any(marker in message for marker in markers):
                permissions[feature] = "allow"

    for feature, value in (explicit_overrides or {}).items():
        if value in {"allow", "deny", "unknown"}:
            permissions[str(feature)] = str(value)
    return permissions
