"""Offline top-one A behavior labeling with the local jevos HTTP server.

This is a classification sidecar, not a semantic approval or production import.
No planner/responder gateway is constructed; only literal loopback is accepted.
"""
from __future__ import annotations

import argparse
import concurrent.futures
from collections import Counter
import hashlib
import http.client
import json
import math
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from scripts.run_style_reaction_pipeline_v2 import all_windows, read_jsonl, unique_units
from scripts.prepare_style_reaction_resume import root_window
from scripts.style_reaction_pipeline import BEHAVIOR_IDS

LABELS = {
    "direct_natural_reply": ("直接自然回复", "Answer the topic plainly and directly, without a distinctive joke, correction, advice or closing."),
    "playful_reframe": ("玩笑式重新解释", "Turn the premise into a joke through a playful reinterpretation, absurd inference or humorous literal reading."),
    "reasoned_independent_response": ("有理由的独立回应", "Express an independent judgment or disagreement with a reason, rather than simply agreeing."),
    "premise_correction": ("纠正对方前提", "Correct or reject an incorrect assumption, misunderstanding or factual premise."),
    "light_contextual_tease": ("轻度调侃", "Lightly tease or poke fun at the other person's specific words or behavior."),
    "concise_closing": ("简短收尾", "Briefly end the current topic with a short closing statement."),
    "acknowledge_specific_distress": ("回应具体困境", "Acknowledge someone's specific difficulty, sadness or distress with understanding."),
    "self_deprecation": ("自嘲", "Make a joke at one's own expense about one's limitations, mistakes or shortcomings."),
    "light_profanity_release": ("轻度粗口释放情绪", "Use mild profanity or swearing to release frustration or surprise."),
    "listen_without_forcing_advice": ("倾听而不强行建议", "Offer receptive listening or companionship without pushing advice or a solution."),
    "acknowledge_shared_detail": ("回应分享的具体细节", "React to a specific detail someone has shared, rather than turning it into advice."),
    "natural_close": ("自然告别", "Say goodbye or naturally end the conversation, without introducing a new task or question."),
    "update_corrected_premise": ("接受纠正并更新前提", "Accept someone else's correction and update one's own earlier understanding."),
    "provide_requested_help": ("提供所请求的帮助", "Provide useful instructions, explanation, suggestions or practical help in response to a request."),
    "brief_contextual_uptake": ("简短承接", "Briefly acknowledge, confirm or take up the preceding turn, with little new substantive content."),
}
INSTRUCTIONS = (
    "Classify the dominant conversational behavior performed by this Chinese reply. "
    "Judge what the speaker is doing, not the topic or the intent of a quoted person. "
    "Choose the single best-fitting category from the descriptions."
)


def request_json(endpoint: str, route: str, body: dict | None = None) -> dict:
    address = urlsplit(endpoint)
    if (address.scheme != "http" or address.hostname not in {"127.0.0.1", "::1"}
            or address.username or address.password or address.path not in {"", "/"}
            or address.query or address.fragment):
        raise ValueError("Only a literal local HTTP endpoint is allowed")
    connection = http.client.HTTPConnection(address.hostname, address.port or 8017, timeout=120)
    try:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        connection.request("POST" if body is not None else "GET", route, encoded,
                           {"Content-Type": "application/json"} if body is not None else {})
        response = connection.getresponse()
        data = response.read().decode("utf-8")
        if response.status != 200:
            raise RuntimeError(f"Local jevos HTTP {response.status}: {data[:1000]}")
        return json.loads(data)
    finally:
        connection.close()


def label_request(text: str, labels: dict) -> dict:
    return {"model": "jev-latest", "state": text, "questions": {"behavior": {
        "type": "choice", "instructions": INSTRUCTIONS,
        "criteria": {key: value[1] for key, value in labels.items()},
    }}}


def parse_label(response: dict, labels: dict, *, distribution_tolerance: float = 1e-5) -> dict:
    answer = response["answers"]["behavior"]
    scores = answer["probabilities"]
    if answer["type"] != "choice" or set(scores) != set(labels):
        raise ValueError("Local response does not match the frozen behavior vocabulary")
    if any(type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 1
           for value in scores.values()) or abs(sum(scores.values()) - 1) > distribution_tolerance:
        raise ValueError("Invalid local choice distribution")
    # Fixed vocabulary order breaks ties; no confidence threshold or score weighting.
    ordered = sorted(labels, key=lambda key: (-scores[key], list(labels).index(key)))
    if answer["choice"] not in labels or scores[answer["choice"]] != scores[ordered[0]]:
        raise ValueError("Returned choice is not a maximum-score label")
    return {"top1_label": ordered[0], "top1_label_zh": labels[ordered[0]][0],
            "top1_score": scores[ordered[0]], "label_scores": scores,
            "model_confidence": answer.get("confidence"),
            "top1_top2_gap": scores[ordered[0]] - scores[ordered[1]],
            "model": response["model"], "usage": response.get("usage", {})}


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare(resume: Path, cohort: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    if (output / "inputs.jsonl").exists():
        raise FileExistsError("Frozen input already exists; classify resumes it without re-preparing")
    assert tuple(LABELS) == BEHAVIOR_IDS
    windows = all_windows(resume)
    roots = set(json.loads((cohort / "selection.json").read_text(encoding="utf-8"))["window_ids"])
    reviews = {row["unit_id"]: row for row in read_jsonl(resume / "semantic_reviews.jsonl")}
    projected = {row["unit_id"]: row for row in read_jsonl(resume / "source_reaction_units.reviewed.jsonl")}
    inputs = []
    for unit in sorted(unique_units(resume), key=lambda row: (
            row["source_id"], row["reaction_source_start_char"], row["unit_id"])):
        review = reviews.get(unit["unit_id"], {})
        inputs.append({
            "unit_id": unit["unit_id"], "source_id": unit["source_id"], "filename": unit["filename"],
            "window_id": unit["window_id"],
            "reaction_source_start_char": unit["reaction_source_start_char"],
            "reaction_source_end_char": unit["reaction_source_end_char"],
            "text": unit["source_response_turn"], "trigger_text": unit.get("source_user_turn"),
            "cohort_200": root_window(unit["window_id"], windows) in roots,
            "prior_review_decision": review.get("decision", "unreviewed"),
            "prior_extraction_behavior_ids": unit.get("behavior_ids", []),
            "prior_review_behavior_ids": review.get("behavior_ids", []),
            "prior_risk_flags": unit.get("risk_flags", []),
            "prior_runtime_eligible": bool(projected.get(unit["unit_id"], {}).get("runtime_eligible")),
        })
    payload = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in inputs).encode("utf-8")
    (output / "inputs.jsonl").write_bytes(payload)
    write_json(output / "labels.frozen.json", LABELS)
    (output / "instructions.frozen.txt").write_text(INSTRUCTIONS, encoding="utf-8")
    write_json(output / "input_manifest.json", {
        "schema_version": "local-jevos-a-label-top1-v1", "inputs": len(inputs),
        "inputs_sha256": hashlib.sha256(payload).hexdigest(),
        "source_resume": str(resume), "cohort": str(cohort),
        "cohort_unreviewed": sum(row["cohort_200"] and row["prior_review_decision"] == "unreviewed" for row in inputs),
        "label_selection": "argmax; fixed-order tie break; no threshold",
        "input_to_model": "exact reaction text only; previous labels and verdicts hidden",
        "score_semantics": "jevos choice normalized relative scores, not measured correctness probabilities",
        "classification_is_semantic_approval": False, "external_model_calls": 0,
    })
    print(f"Frozen {len(inputs)} local classification inputs", flush=True)


def classify(output: Path, endpoint: str, limit: int | None, workers: int) -> None:
    global INSTRUCTIONS
    labels = json.loads((output / "labels.frozen.json").read_text(encoding="utf-8"))
    INSTRUCTIONS = (output / "instructions.frozen.txt").read_text(encoding="utf-8")
    health = request_json(endpoint, "/health")
    health_file = output / "server_health.json"
    if health_file.exists():
        previous = json.loads(health_file.read_text(encoding="utf-8"))
        if previous.get("fingerprint") != health.get("fingerprint"):
            raise ValueError("Local model changed; use a new classification run")
    else:
        write_json(health_file, health)
    completed = {row["unit_id"] for row in read_jsonl(output / "labels.jsonl")}
    pending = [row for row in read_jsonl(output / "inputs.jsonl") if row["unit_id"] not in completed]
    if limit is not None:
        pending = pending[:limit]
    started = time.monotonic()

    def classify_one(row: dict) -> dict:
        begin = time.monotonic()
        response = request_json(endpoint, "/v1/systemone", label_request(row["text"], labels))
        return {"unit_id": row["unit_id"], **parse_label(response, labels),
                "elapsed_seconds": round(time.monotonic() - begin, 4),
                "raw_response": response}

    with (output / "labels.jsonl").open("a", encoding="utf-8") as result_file:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            # Bound outstanding work so an invalid response stops the batch promptly.
            number = 0
            for start in range(0, len(pending), workers):
                for result in pool.map(classify_one, pending[start:start+workers]):
                    number += 1
                    result_file.write(json.dumps(result, ensure_ascii=False) + "\n")
                    result_file.flush()
                    if number % 25 == 0 or number == len(pending):
                        print(f"Labeled {len(completed)+number}; new {number}/{len(pending)}; "
                              f"{time.monotonic()-started:.1f}s", flush=True)
    summarize(output)


def summarize(output: Path) -> dict:
    inputs = {row["unit_id"]: row for row in read_jsonl(output / "inputs.jsonl")}
    labels = read_jsonl(output / "labels.jsonl")
    if len({row["unit_id"] for row in labels}) != len(labels):
        raise ValueError("Duplicate classification checkpoint IDs")
    views = [{**inputs[row["unit_id"]], **{key: value for key, value in row.items() if key != "raw_response"},
              "a_candidate_text": inputs[row["unit_id"]]["text"],
              "a_behavior_ids": [row["top1_label"]], "classification_status": "local_top1_labeled",
              "semantic_approval_transferred": False} for row in labels]
    with (output / "a_candidates.jsonl").open("w", encoding="utf-8") as stream:
        for row in views:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    cohort = [row for row in views if row["cohort_200"] and row["prior_review_decision"] == "unreviewed"]
    comparable = [row for row in views if row["prior_extraction_behavior_ids"]]
    summary = {
        "inputs": len(inputs), "labeled": len(labels), "remaining": len(inputs)-len(labels),
        "model": dict(Counter(row["model"] for row in labels)),
        "top1_labels": dict(Counter(row["top1_label"] for row in labels).most_common()),
        "cohort_unreviewed_labeled": len(cohort),
        "cohort_unreviewed_top1_labels": dict(Counter(row["top1_label"] for row in cohort).most_common()),
        "top1_in_previous_extraction_labels": sum(row["top1_label"] in row["prior_extraction_behavior_ids"] for row in comparable),
        "previous_label_comparable": len(comparable),
        "low_gap_lt_0_05_observation_only": sum(row["top1_top2_gap"] < .05 for row in labels),
        "tokens": sum(row.get("usage", {}).get("input_tokens", 0) for row in labels),
        "classification_only": True, "production_modified": False, "external_model_calls": 0,
    }
    write_json(output / "summary.json", summary)
    write_json(output / "examples.json", {
        key: [{"unit_id": row["unit_id"], "text": row["text"], "score": row["top1_score"],
               "previous_labels": row["prior_extraction_behavior_ids"],
               "risk_flags": row["prior_risk_flags"]} for row in views if row["top1_label"] == key][:8]
        for key in LABELS
    })
    # An inspectable A-side lookup, ordered by source rather than confidence.
    write_json(output / "a_label_lookup_demo.json", {
        "selection": "top1 label equality; original source order; no score weighting",
        "classification_only": True,
        "labels": {key: [{"unit_id": row["unit_id"], "source_id": row["source_id"],
                          "a_text": row["text"], "prior_review_decision": row["prior_review_decision"],
                          "prior_runtime_eligible": row["prior_runtime_eligible"]}
                         for row in views if row["top1_label"] == key][:3] for key in LABELS},
    })
    browser_rows = [{key: row[key] for key in (
        "unit_id", "source_id", "filename", "text", "top1_label", "top1_label_zh",
        "top1_score", "prior_review_decision", "prior_risk_flags", "cohort_200",
        "prior_runtime_eligible", "prior_extraction_behavior_ids")}
        for row in views]
    data = json.dumps(browser_rows, ensure_ascii=False).replace("<", "\\u003c")
    page = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>本地 JEV A 候选检查</title><style>
body{font:15px system-ui,sans-serif;margin:24px;color:#20252d;background:#f6f8fb}
input,select,button{font:inherit;padding:8px;margin:4px;border:1px solid #bbc5d4;border-radius:6px}
article{background:white;border:1px solid #dce2ea;border-radius:8px;padding:14px;margin:10px 0}
pre{white-space:pre-wrap;font:inherit;line-height:1.6;margin-bottom:4px}
small{color:#526174}h1{font-size:22px}.tag{font-weight:650;color:#184c7e}
</style><h1>本地 JEV：最高分行为标签与 A 候选</h1>
<p>按每条文本的最高分标签分类。分数不参与筛选或排序；原文、原风险和审核结论保留。
这是离线分类候选，行为标签不等于事实或来源审核通过。</p>
<select id="label"><option value="">全部行为</option></select>
<select id="scope"><option value="all">全部已打标</option><option value="cohort">200 窗口未审候选</option></select>
<input id="query" placeholder="搜索原文、文件名或 ID" size="35">
<p id="stats"></p><button id="prev">上一页</button><button id="next">下一页</button><span id="page"></span>
<main id="results"></main><script>
const rows=__DATA__;let offset=0;const size=40;
const el=id=>document.getElementById(id);
const counts={};for(const r of rows)counts[r.top1_label]=(counts[r.top1_label]||0)+1;
for(const [key,value] of Object.entries(counts).sort((a,b)=>b[1]-a[1])){
 const o=document.createElement('option');o.value=key;
 o.textContent=rows.find(r=>r.top1_label===key).top1_label_zh+' ('+value+')';el('label').append(o);
}
function draw(){const q=el('query').value.toLowerCase();const filtered=rows.filter(r=>
 (!el('label').value||r.top1_label===el('label').value)&&
 (el('scope').value!=='cohort'||r.cohort_200&&r.prior_review_decision==='unreviewed')&&
 (!q||(r.text+' '+r.filename+' '+r.unit_id).toLowerCase().includes(q)));
 offset=Math.min(offset,Math.max(0,Math.ceil(filtered.length/size)-1));
 el('stats').textContent='已打标 '+rows.length+' 条；当前筛选 '+filtered.length+' 条';
 el('page').textContent=' 第 '+(offset+1)+' / '+Math.max(1,Math.ceil(filtered.length/size))+' 页';
 el('results').replaceChildren();
 for(const r of filtered.slice(offset*size,(offset+1)*size)){
  const a=document.createElement('article'),t=document.createElement('div'),p=document.createElement('pre'),s=document.createElement('small');
  t.className='tag';t.textContent=r.top1_label_zh+' · '+r.top1_label;
  p.textContent=r.text;s.textContent=r.filename+' | '+r.unit_id+' | 原审核 '+r.prior_review_decision+
   ' | 原标签 '+r.prior_extraction_behavior_ids.join(', ')+' | 原风险 '+(r.prior_risk_flags.join(', ')||'无标记')+
   ' | 最高相对分数 '+(r.top1_score*100).toFixed(1)+'%';a.append(t,p,s);el('results').append(a);
 }
 el('prev').disabled=offset===0;el('next').disabled=(offset+1)*size>=filtered.length;
}
for(const id of ['label','scope','query'])el(id).addEventListener('input',()=>{offset=0;draw()});
el('prev').onclick=()=>{offset--;draw()};el('next').onclick=()=>{offset++;draw()};draw();
</script></html>"""
    (output / "REVIEW.html").write_text(page.replace("__DATA__", data), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return summary


def calibrate(output: Path, endpoint: str) -> None:
    global INSTRUCTIONS
    labels = json.loads((output / "labels.frozen.json").read_text(encoding="utf-8"))
    INSTRUCTIONS = (output / "instructions.frozen.txt").read_text(encoding="utf-8")
    cases = json.loads((output / "calibration_cases.json").read_text(encoding="utf-8"))
    results = []
    for case in cases:
        begin = time.monotonic()
        response = request_json(endpoint, "/v1/systemone", label_request(case["text"], labels))
        result = {**case, **parse_label(response, labels), "raw_response": response,
                  "elapsed_seconds": round(time.monotonic() - begin, 4)}
        result["matches_designed_expectation"] = result["top1_label"] in case["expected_labels"]
        results.append(result)
        print(f"{case['case_id']}: {result['top1_label']} {result['top1_score']:.3f} "
              f"({result['elapsed_seconds']:.2f}s)", flush=True)
    write_json(output / "calibration_results.json", results)
    write_json(output / "calibration_summary.json", {
        "cases": len(results),
        "matches_designed_expectation": sum(row["matches_designed_expectation"] for row in results),
        "elapsed_seconds": sum(row["elapsed_seconds"] for row in results),
        "scope": "designed Chinese development fixtures; not human gold or corpus accuracy",
        "external_model_calls": 0,
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "classify", "report", "calibrate"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--cohort", type=Path)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8017")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if args.workers < 1 or (args.limit is not None and args.limit < 1):
        parser.error("workers and limit must be positive")
    if args.action == "prepare":
        if not args.resume or not args.cohort:
            parser.error("prepare requires --resume and --cohort")
        prepare(args.resume, args.cohort, args.output)
    elif args.action == "classify":
        classify(args.output, args.endpoint, args.limit, args.workers)
    elif args.action == "calibrate":
        calibrate(args.output, args.endpoint)
    else:
        summarize(args.output)


if __name__ == "__main__":
    main()
