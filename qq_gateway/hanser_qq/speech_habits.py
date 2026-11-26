"""Offline corpus inventory and a small, source-backed QQ trial voice palette."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sqlite3
from contextlib import closing


PHRASES = (
    "我觉得", "是不是", "诶", "欸", "确实", "是吧", "反正", "好吧", "哎呀", "是这样的",
    "啊这", "嘿嘿", "离谱", "怎么回事", "好不好", "对吧", "对不对", "行吧", "真的假的",
    "我跟你说", "我跟你讲", "怎么说呢", "好家伙", "笑死", "救命", "憨憨", "捏",
    "你们", "大家", "兄弟们", "卧槽", "我靠", "然后", "就是", "拜拜", "没事",
)


def mine(corpus: Path) -> dict:
    """Read every disposition; count only the active labels 1–12 source lane."""
    with closing(sqlite3.connect(corpus.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        manifest = json.loads(conn.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])
        records = [(bucket, label, json.loads(data)) for bucket, label, data in
                   conn.execute("SELECT bucket,label_number,record_json FROM units ORDER BY source_id,unit_id")]
    eligible = [row for bucket, label, row in records if bucket == "classified" and 1 <= label <= 12]
    phrases = []
    for phrase in PHRASES:
        hits = [row for row in eligible if phrase in row["text"]]
        evidence, sampled_sources = [], set()
        for row in hits:
            if row["source_id"] in sampled_sources:
                continue
            for span in row["source_spans"]:
                at = span["text"].find(phrase)
                if at < 0:
                    continue
                evidence.append({"unit_id": row["unit_id"], "source_id": row["source_id"],
                    "filename": row["filename"], "source_revision": row["source_revision"],
                    "attribution_basis": row["attribution_basis"], "source_review_status": row["review_status"],
                    "start": span["start"] + at, "end": span["start"] + at + len(phrase), "text": phrase,
                    "context": span["text"][max(0, at - 30):at + len(phrase) + 35]})
                sampled_sources.add(row["source_id"])
                break
            if len(evidence) == 4:
                break
        phrases.append({"phrase": phrase, "occurrences": sum(row["text"].count(phrase) for row in hits),
            "units": len(hits), "documents": len({row["source_id"] for row in hits}), "evidence": evidence})
    starts, ends = Counter(), Counter()
    for row in eligible:
        for clause in re.split(r"[，,。！？!?；;\s]+", row["text"]):
            if len(clause) >= 4:
                for length in (3, 4, 5):
                    if len(clause) >= length:
                        starts[clause[:length]] += 1
                        ends[clause[-length:]] += 1
    return {"version": "qq-speech-habits-inventory-v1", "source_generation": manifest["generation"],
        "scanned_units": len(records), "counted_units": len(eligible),
        "counted_documents": len({row["source_id"] for row in eligible}),
        "scope": "classified labels 1–12; other dispositions counted in scan only",
        "interpretation": "Lexical substring hits include reported speech and compound words; not character-unique markers, reviewed discourse-use counts or prescribed output frequencies.",
        "formal_approval": False,
        "phrases": sorted(phrases, key=lambda row: (-row["documents"], -row["occurrences"])),
        "common_clause_starts": starts.most_common(40), "common_clause_ends": ends.most_common(40)}


def load_profile() -> dict:
    return json.loads(Path(__file__).with_name("speech_habits.v1.json").read_text(encoding="utf-8"))


def trial_guidance(profile, message, history, plan, signals) -> str:
    from hanser_agent.persona.output_contract import extract_exact_output, extract_required_verbatim_spans
    if (plan.response_mode not in {"casual", "playful"} or plan.need_wiki or plan.fact_sensitivity == "high"
        or extract_exact_output(message) is not None or extract_required_verbatim_spans(message)):
        return ""
    if signals and any(signals.observed_bool(name) is True for name in ("distress", "explicit_stop", "disable_humor")):
        return ""
    recent = "\n".join(item.content for item in history[-8:] if item.role == "assistant")
    available = [item for item in profile["habits"] if item["phrase"] not in recent]
    if not available:
        return ""
    palette = "；".join(f"{item['phrase']}（{item['use']}）" for item in available)
    return (f"\n[QQ SPEECH HABITS {profile['version']}; user_trial; source_generation={profile['source_generation']}]\n"
        "这是用户要求加强的语料表达习惯，仅调整措辞，不提供人物经历、喜好、关系或当前事实。\n"
        "先直接接住这句话，再表达自己的判断；需要解释时像边想边说，可以自然修正或收束。\n"
        f"可用的短接话与语气：{palette}。这些词不具有人物独占性，以合适语境和整体节奏体现角色。\n"
        "合适时轻轻带一处即可，也可以完全不用；不要堆叠、轮换打卡或每次以我觉得开头。"
        "只有有依据时才认同，反问不替代明确回答，不为口癖多追问；近期说过的表达降低优先级。"
        "用户要求普通、认真、停止玩笑或指定输出格式时，优先遵从；不主动加昵称、捏和直播群体称呼。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read-only speech habit inventory; no model calls or index changes")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = mine(args.corpus)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({key: report[key] for key in ("source_generation", "scanned_units", "counted_units", "counted_documents")}))
