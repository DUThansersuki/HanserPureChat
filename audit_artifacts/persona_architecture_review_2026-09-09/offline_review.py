"""Read saved evaluations and probe pure persona logic; no model or DB writes."""
from pathlib import Path
import collections
import json
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.dont_write_bytecode = True
from hanser_agent.models import ChatMessage
from hanser_agent.persona.signals import build_turn_signals
from hanser_agent.persona.permissions import infer_expression_permissions
from hanser_agent.persona.policy import build_guidance
from hanser_agent.persona.schemas import EffectivePersonaSettings
from hanser_agent.persona.expression import observe_recent_expressions
# Match the application's agent-first import order; responder-first has a circular import.
import hanser_agent.agent
from hanser_agent.responder.validator import StyleValidator
from hanser_agent.responder.service import HanserResponder

OUT = Path(__file__).parent
probes = []
settings = EffectivePersonaSettings()

def probe(name, message, history=(), payload=None):
    signals = build_turn_signals(message, current_message_ref="current", planner_payload=payload, history_texts=[x.content for x in history])
    perms = infer_expression_permissions(history, message)
    guidance = build_guidance(signals, perms, None, settings)
    item = {"id": name, "input": message, "history": [x.model_dump() for x in history],
            "observed": {k: v.model_dump() for k,v in signals.values.items() if v.status == "observed"},
            "permissions": perms, "hard_disallowed": guidance.expression_caps.hard_disallowed,
            "must_do": [x.requirement_id for x in guidance.must_do],
            "must_not": [x.requirement_id for x in guidance.must_not]}
    probes.append(item)
    return guidance

probe("quoted_permission", "他刚才说‘别开玩笑’，我只是转述")
probe("negated_permission", "我不是说别开玩笑，你可以自然聊")
probe("negated_allow", "不可以开玩笑", [ChatMessage(role="user",content="别开玩笑")])
probe("no_advice_scope", "先别给建议，陪我随便聊聊就好")
probe("profanity_scope", "别爆粗，但可以继续玩梗")
probe("current_referent_definition", "这个方案是先备份再升级，你分析一下风险")
probe("natural_history_definition", "这个方案有什么风险", [ChatMessage(role="user",content="我打算先备份，再升级数据库")])
probe("quoted_distress", "她说我很难受，我在转述她的话")
probe("planner_hardening", "今天普通聊聊", payload={"explicit_stop":{"value": True,"confidence":"low","evidence_refs":[]}})
long_history = [ChatMessage(role="user",content="以后别开玩笑")]
for i in range(8):
    long_history.extend([ChatMessage(role="user",content=f"普通话题{i}"),ChatMessage(role="assistant",content="好的")])
probe("permission_full_history", "晚上好", long_history)
probe("permission_recent_12", "晚上好", long_history[-12:])

validator = StyleValidator(ROOT / "backend/hanser_agent/prompts/persona/candidates/hanser-persona-v2-candidate/style_constraints.yaml")
g = probe("profanity_validation_context", "别爆粗")
v = validator.validate_output("这个办法很可靠", behavior_decision=g)
probes.append({"id":"reliable_false_profanity", "output":"这个办法很可靠", "violations":v.violations})
probes.append({"id":"lexical_false_markers", "output":"草莓蛋糕很好吃 这个办法很可靠", "observation":observe_recent_expressions([ChatMessage(role="assistant",content="草莓蛋糕很好吃 这个办法很可靠")]).model_dump()})
probes.append({"id":"permission_fallback_topic", "input":"刚才说披萨飞走了 我开玩笑的", "fallback":HanserResponder._permission_fallback_text([ChatMessage(role="user",content="刚才说披萨飞走了 我开玩笑的")],["disallowed_feature:humor"])})

def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

runs = ROOT / "backend/data/eval/persona_v2/runs"
selected = ["text_ab_full72_2026-09-08_001", "text_ab_ablation24_2026-09-08_001", "sequence_ab_6x12_2026-09-08_008", "sequence_ab_permission_final_2026-09-08_011"]
stats = {}
for run in selected:
    data = rows(runs/run/"outputs.jsonl")
    per_variant = {}
    for variant in ("A","B"):
        group = [r for r in data if r["variant"]==variant and r["status"]=="MEASURED"]
        lengths = [len(r["final_text"]) for r in group]
        latency = sorted(r["latency_seconds"] for r in group)
        per_variant[variant] = {"n":len(group), "chars_mean":round(statistics.mean(lengths),2),
          "chars_median":statistics.median(lengths), "responder_latency_median_s":statistics.median(latency),
          "responder_latency_p95_nearest_rank_s":latency[max(0, __import__('math').ceil(.95*len(latency))-1)],
          "attempts_gt1":sum(r.get("attempts",1)>1 for r in group),
          "bounded_fallbacks":sum("bounded_permission_fallback" in r.get("validator_actions",[]) for r in group),
          "literal_nickname_reply_count":sum("憨憨" in r["final_text"] for r in group),
          "style_unique_ids":len({x for r in group for x in r.get("style_example_ids",[])}),
          "top_style_ids":collections.Counter(x for r in group for x in r.get("style_example_ids",[])).most_common(5)}
    stats[run]=per_variant

corpus=rows(ROOT / "audit_artifacts/persona_v2_candidate_2026-09-08_011/style_examples.reviewed.jsonl")
eligible=[r for r in corpus if r["schema_review_status"]=="approved" and "style_runtime" in r["runtime_scope"]]
corpus_summary={"eligible":len(eligible), "source_type":dict(collections.Counter(r["source_type"] for r in eligible)),
    "legacy_scene_labels":dict(collections.Counter(r["scene"] for r in eligible)),
    "behavior_tags":dict(collections.Counter(t for r in eligible for t in r["behavior_tags"])),
    "expression_tags":dict(collections.Counter(t for r in eligible for t in r["expression_tags"])),
    "inspected_top_items":[r for r in corpus if r["id"] in ("style:2002","style:2505","style:2379")]}
summary={"probes":probes,"saved_output_stats":stats,"corpus_rows":len(corpus),"corpus_summary":corpus_summary}
(OUT/"offline_findings.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({"probe_count":len(probes),"saved_runs":len(stats),"eligible_rows":len(eligible),"model_calls":0},ensure_ascii=False))
