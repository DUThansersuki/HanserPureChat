"""Prepare a reproducible local, source-backed inspection of a completed label run."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.local_jev_a_labels import write_json
from scripts.run_style_reaction_pipeline_v2 import all_windows, read_jsonl
from scripts.prepare_style_reaction_resume import root_window


def prepare(output: Path, resume: Path, seed: int = 20261012) -> None:
    inputs = {row["unit_id"]: row for row in read_jsonl(output / "inputs.jsonl")}
    labels = {row["unit_id"]: row for row in read_jsonl(output / "labels.jsonl")}
    if inputs.keys() != labels.keys():
        raise ValueError("Complete this version before preparing the final inspection")
    target = output / "inspection"
    target.mkdir(exist_ok=True)
    if (target / "selection.json").exists():
        raise FileExistsError("Inspection selection is already frozen")
    rng = random.Random(seed)
    identities = sorted(inputs)
    uniform = rng.sample(identities, min(80, len(identities)))
    vocabulary = json.loads((output / "labels.frozen.json").read_text(encoding="utf-8"))
    per_label = {key: rng.sample(
        matching := [key_id for key_id in identities if labels[key_id]["top1_label"] == key],
        min(8, len(matching))) for key in vocabulary}
    prior_eligible = [key for key in identities if inputs[key]["prior_runtime_eligible"]]
    selected = sorted(set(uniform + prior_eligible + [key for values in per_label.values() for key in values]))
    windows = all_windows(resume)
    blind, key_rows = [], []
    for unit_id in selected:
        item, label = inputs[unit_id], labels[unit_id]
        base = windows[root_window(item["window_id"], windows)]
        start = item["reaction_source_start_char"] - base["source_start_char"]
        end = item["reaction_source_end_char"] - base["source_start_char"]
        blind.append({"unit_id": unit_id, "filename": item["filename"],
                      "text": item["text"], "trigger_text": item["trigger_text"],
                      "context_before": base["source_span"][max(0, start-180):start],
                      "context_after": base["source_span"][end:end+180],
                      "context_scope": "available frozen base window, up to 180 chars each side"})
        key_rows.append({"unit_id": unit_id, "label": label["top1_label"],
                         "score": label["top1_score"], "gap": label["top1_top2_gap"],
                         "prior_extraction_labels": item["prior_extraction_behavior_ids"],
                         "prior_review_labels": item["prior_review_behavior_ids"],
                         "prior_review_decision": item["prior_review_decision"],
                         "prior_runtime_eligible": item["prior_runtime_eligible"],
                         "cohort_unreviewed": item["cohort_200"] and item["prior_review_decision"] == "unreviewed"})
    for filename, rows in [("blind_inputs.jsonl", blind), ("prediction_key.jsonl", key_rows)]:
        (target / filename).write_text("".join(json.dumps(row, ensure_ascii=False)+'\n' for row in rows), encoding="utf-8")
    write_json(target / "selection.json", {
        "seed": seed, "uniform_80": uniform, "up_to_8_per_predicted_label": per_label,
        "all_prior_runtime_eligible": prior_eligible, "unique_selected": len(selected),
        "scope": "development inspection, not human gold or unbiased accuracy for the combined sample",
        "model_calls": 0,
    })
    print(f"Frozen {len(selected)} source-backed inspection items")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.output, args.resume)
