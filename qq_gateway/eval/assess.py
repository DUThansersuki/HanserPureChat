"""Assess frozen real runs from explicit state gold and completed blind reviews."""
import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def interval(values):
    values = list(values)
    if not values:
        return None
    rng = random.Random(20261018)
    boot = sorted(mean(rng.choices(values, k=len(values))) for _ in range(10000))
    return {"mean": mean(values), "clusters": len(values), "ci95": [boot[249], boot[9749]]}


def objective(case, row):
    errors = []
    memories = row.get("memory_state", [])
    check = case.get("state_check", {})
    for forbidden in check.get("forbidden_contains", []):
        if any(forbidden in item["object_value"] for item in memories):
            errors.append("forbidden_preference:" + forbidden)
    for required in check.get("required", []):
        if not any(all(item.get(key) == value for key, value in required.items() if key != "context_tag")
                   and ("context_tag" not in required or required["context_tag"] in item.get("context_tags", []))
                   for item in memories):
            errors.append("missing_preference:" + json.dumps(required, ensure_ascii=False))
    if "operation_check" in case:
        action = row.get("latest_memory_action") or {}
        if any(action.get(key) != value for key, value in case["operation_check"].items()):
            errors.append("operation_not_performed")
    return errors


def assess(root, run, cases_path, ratings_path, destination):
    cases, rows, ratings = lines(cases_path), lines(run / "outputs.jsonl"), read(ratings_path)
    if isinstance(ratings, dict):
        ratings = ratings["ratings"]
    by = {(row["case_id"], row["variant"]): row for row in rows}
    review = {row["case_id"]: row for row in ratings}
    mapping, snapshot = read(run / "blind_mapping.json"), read(run / "snapshot.json")
    assert len(review) == len(cases), "all cases require blind review"
    expected = {(case["case_id"], variant) for case in cases for variant in ("A", "B")}
    missing = len(expected - set(by))
    hard, failures = {"A": [], "B": []}, []
    scores, preferences, objective_scores = defaultdict(list), defaultdict(list), defaultdict(list)
    group_scores, wins = defaultdict(list), Counter()
    objective_cases = []
    sources, source_groups = [], set()
    latency, calls, tokens, parse_fallbacks = defaultdict(list), Counter(), Counter(), Counter()
    for case in cases:
        cid, cluster = case["case_id"], case.get("sequence_id") or case["case_id"]
        rating = review[cid]
        assert rating["reason"] and all(1 <= rating["score_" + side] <= 5 for side in ("X", "Y"))
        masked = mapping[cid]
        inverse = {variant: side for side, variant in masked.items()}
        delta = rating["score_" + inverse["B"]] - rating["score_" + inverse["A"]]
        scores[cluster].append(delta)
        group_scores["multi_turn" if case.get("sequence_id") else case["group"]].append(delta)
        preference = rating["preference"]
        winner = masked[preference] if preference in ("X", "Y") else preference
        wins[winner] += 1
        preferences[cluster].append(1 if winner == "B" else -1 if winner == "A" else 0)
        checked = "state_check" in case or "operation_check" in case
        result = {}
        for variant in ("A", "B"):
            row = by.get((cid, variant), {})
            if row.get("status") != "MEASURED":
                failures.append({"case_id": cid, "variant": variant})
            for flag in rating.get("hard_" + inverse[variant], []):
                hard[variant].append({"case_id": cid, "reason": flag})
            if checked:
                errors = objective(case, row)
                result[variant] = {"pass": not errors, "errors": errors}
                if errors:
                    hard[variant].append({"case_id": cid, "reason": "state_operation_gold_failure", "errors": errors})
            latency[variant].append(row.get("seconds", 0))
            calls[variant] += row.get("actual_calls", 0)
            tokens[variant] += row.get("token_usage", {}).get("total_tokens") or 0
            generation = row.get("trace", {}).get("generation", {})
            parse_fallbacks[variant] += bool(generation.get("planner_fallback_reason"))
            if variant == "B":
                retrieval = row.get("trace", {}).get("retrieval", {})
                selected = retrieval.get("match_details", {}).get("full_corpus", {}).get("selected_source_fragments", {})
                injected = set(retrieval.get("injected_style_ids", []))
                actual = {key: value for key, value in selected.items() if key in injected}
                if actual:
                    sources.append({"case_id": cid, "group": case["group"], "fragments": actual})
                    source_groups.add(case["group"])
        if checked:
            objective_cases.append({"case_id": cid, **result})
            objective_scores[cluster].append(int(result["B"]["pass"]) - int(result["A"]["pass"]))
    objective_stats = interval(mean(v) for v in objective_scores.values())
    text_stats = interval(mean(v) for v in scores.values())
    text_groups = {group: mean(items) for group, items in group_scores.items()}
    gates = {
        "complete": not missing and not failures,
        "hard": not hard["B"],
        "objective": objective_stats is not None and objective_stats["ci95"][0] > 0,
        "text": text_stats["ci95"][0] >= -0.25 and all(
            text_groups.get(group, -999) >= -0.25 for group in ("daily", "emotion", "stance", "multi_turn")),
        "source": len(sources) >= 10 and len(source_groups) >= 4,
    }
    independent = None
    fresh = [case for case in cases if case.get("partition") == "holdout"]
    if fresh:
        fresh_text, fresh_objective = defaultdict(list), defaultdict(list)
        for case in fresh:
            cid = case["case_id"]
            cluster = case.get("sequence_id") or cid
            inverse = {variant: side for side, variant in mapping[cid].items()}
            rating = review[cid]
            fresh_text[cluster].append(rating["score_" + inverse["B"]] - rating["score_" + inverse["A"]])
            if "state_check" in case or "operation_check" in case:
                fresh_objective[cluster].append(int(not objective(case, by[cid, "B"])) - int(not objective(case, by[cid, "A"])))
        independent = {"case_count": len(fresh), "text": interval(mean(v) for v in fresh_text.values()),
                       "objective": interval(mean(v) for v in fresh_objective.values()),
                       "limitation": "Other cases are rerun regression, not independent hidden evidence"}
        gates["independent_objective"] = independent["objective"]["ci95"][0] > 0
        gates["independent_text"] = independent["text"]["ci95"][0] >= -0.25
    report = {
        "release_id": snapshot["release_id"], "decision": "PASS" if all(gates.values()) else "HOLD",
        "scope": "QQ engineering correctness and full-corpus bounded-fragment release; no global fidelity superiority claim",
        "gates": gates, "engine_sha256": snapshot["engine_sha256"], "payload_sha256": snapshot["payload_sha256"],
        "case_count": len(cases), "outputs": len(rows), "missing_outputs": missing,
        "request_failures": failures, "hard_failures": len(hard["B"]), "hard_by_variant": hard,
        "objective": {"paired_cluster_bootstrap": objective_stats,
                      "checked_cases": len(objective_cases), "passes": {variant: sum(item[variant]["pass"] for item in objective_cases)
                                                                          for variant in ("A", "B")}, "cases": objective_cases},
        "text": {"paired_cluster_bootstrap": text_stats, "group_mean_deltas": text_groups,
                 "preferences": dict(wins), "preference_cluster_bootstrap": interval(mean(v) for v in preferences.values())},
        "sources": {"actually_injected_cases": len(sources), "groups": sorted(source_groups), "cases": sources},
        "performance": {variant: {"calls": calls[variant], "tokens": tokens[variant], "p50_seconds": median(latency[variant]),
                                  "p95_seconds": sorted(latency[variant])[int(len(latency[variant]) * .95)],
                                  "planner_fallbacks": parse_fallbacks[variant]} for variant in ("A", "B")},
        "limitations": ["Agent proxy reviews, not human or audio validation", "No causal attribution to corpus alone",
                        "Full A candidates remain unapproved; only bounded reviewed fragments can inject", "Real QQ not connected"],
        "raw_run": str(run.resolve()), "cases": str(cases_path.resolve()), "ratings": str(ratings_path.resolve()),
        "independent_holdout": independent,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("decision", "gates", "hard_failures", "missing_outputs", "performance")}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--ratings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assess(Path(__file__).resolve().parents[1], args.run, args.cases, args.ratings, args.output)
