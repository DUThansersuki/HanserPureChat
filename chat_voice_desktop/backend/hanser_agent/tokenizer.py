from __future__ import annotations

from pathlib import Path
from threading import Lock

import jieba

STOP_WORDS = {
    "的", "了", "是", "在", "和", "就", "都", "也", "不", "我", "你", "他", "她",
    "它", "我们", "你们", "他们", "她们", "这个", "那个", "什么", "怎么", "为什么",
    "然后", "现在", "可以", "没有", "自己", "这样", "那样", "一个", "一下", "还有",
    "知道", "觉得", "真的", "已经", "因为", "所以", "如果", "但是", "还是", "就是",
    "是不是", "感觉", "有点", "一会", "起来",
}

_loaded: set[Path] = set()
_lock = Lock()


def ensure_userdict(path: str | Path | None) -> None:
    if not path:
        return
    p = Path(path).resolve()
    if not p.exists() or p in _loaded:
        return
    with _lock:
        if p not in _loaded:
            jieba.load_userdict(str(p))
            _loaded.add(p)


def tokenize(text: str, userdict_path: str | Path | None = None) -> list[str]:
    ensure_userdict(userdict_path)
    out: list[str] = []
    for word in jieba.cut(text or ""):
        w = word.strip()
        if not w or len(w) == 1 or w in STOP_WORDS:
            continue
        if not any(ch.isalnum() for ch in w):
            continue
        out.append(w)
    return out
