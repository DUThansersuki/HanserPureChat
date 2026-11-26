"""CUDA batch A-side classification using the local Laya multilingual checkpoint."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import time

BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parents[1]
sys.path.insert(0, str(BACKEND))
from scripts.local_jev_a_labels import LABELS, parse_label, summarize, write_json
from scripts.run_style_reaction_pipeline_v2 import read_jsonl

MODEL_ID = "convaiinnovations/laya-multilingual"
MODEL_REVISION = "e4e9ddf21a7b1903b7acffd8814ad4307bf63a67"
INSTRUCTION = "这句中文回复主要在做什么？选择最贴切的回应行为。"
# Neutral short keys avoid spending the 256-token option budget on long internal IDs.
# Only descriptions change for the multilingual encoder; the 15 canonical categories are retained.
DESCRIPTIONS = [
    "直接回答当前话题", "把前提重新解释成玩笑", "有理由地表达独立判断",
    "纠正对方的错误前提", "轻轻调侃对方", "简短结束当前话题",
    "理解对方的具体困境", "拿自己的缺点开玩笑", "用轻度粗口释放情绪",
    "倾听陪伴而不强推建议", "回应对方分享的具体细节", "自然告别结束对话",
    "接受对方纠正并更新理解", "给出所请求的实用帮助", "简短确认承接上文",
]


def prepare(cpu_run: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    if (output / "inputs.jsonl").exists():
        raise FileExistsError("GPU input is already frozen")
    for name in ["inputs.jsonl", "labels.frozen.json", "calibration_cases.json"]:
        shutil.copyfile(cpu_run / name, output / name)
    labels = json.loads((output / "labels.frozen.json").read_text(encoding="utf-8"))
    if list(labels) != list(LABELS):
        raise ValueError("GPU run must retain the same 15 canonical behavior IDs")
    mapping = {chr(65+i): key for i, key in enumerate(labels)}
    questions = {"behavior": {"type": "choice", "instructions": INSTRUCTION,
                              "criteria": {chr(65+i): value for i, value in enumerate(DESCRIPTIONS)}}}
    write_json(output / "questions.frozen.json", questions)
    write_json(output / "option_key_map.json", mapping)
    prior = json.loads((cpu_run / "input_manifest.json").read_text(encoding="utf-8-sig"))
    write_json(output / "input_manifest.json", {**prior,
        "schema_version": "local-laya-cuda-a-label-top1-v1", "input_reused_from": str(cpu_run),
        "model_id": MODEL_ID, "model_revision": MODEL_REVISION,
        "instructions": INSTRUCTION, "criteria_language": "Chinese, short neutral keys A-O",
        "classification_is_semantic_approval": False})
    print(f"Reused {prior['inputs']} exact input records; no extraction calls", flush=True)


def canonical_result(response: dict, mapping: dict, labels: dict) -> dict:
    answer = response["answers"]["behavior"]
    if set(answer["probabilities"]) != set(mapping):
        raise ValueError("CUDA model omitted an option")
    normalized = {**response, "model": MODEL_ID, "answers": {"behavior": {
        **answer, "choice": mapping[answer["choice"]],
        "probabilities": {mapping[key]: score for key, score in answer["probabilities"].items()},
    }}}
    # Laya publishes scores rounded to four decimal places: at most K*0.00005 sum error.
    return parse_label(normalized, labels, distribution_tolerance=len(mapping)*.00005+1e-6)


def run(args: argparse.Namespace) -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["USE_TF"] = "0"
    sys.path.insert(0, str(args.runtime_source))
    import torch
    import laya
    if not torch.cuda.is_available():
        raise RuntimeError("This run requires CUDA; CPU substitution is not accepted")
    torch.set_num_threads(4)
    started = time.monotonic()
    agent = laya.load(str(args.model_dir.resolve()), device="cuda", backend="eager")
    if agent.device.type != "cuda":
        raise RuntimeError("Local model did not load onto CUDA")
    labels = json.loads((args.output / "labels.frozen.json").read_text(encoding="utf-8"))
    questions = json.loads((args.output / "questions.frozen.json").read_text(encoding="utf-8"))
    mapping = json.loads((args.output / "option_key_map.json").read_text(encoding="utf-8"))
    inputs = read_jsonl(args.output / "inputs.jsonl")
    # Check the exact encoded head and every state before the first corpus batch.
    internal = agent._to_internal(questions["behavior"])
    encoded = agent._encode_state(inputs[0]["text"], ["behavior"], {"behavior": internal},
                                  max_len=8192, head_max_len=256)[0]
    if encoded["options"]["options_distinct"] != 15 or encoded["options"]["tokens_per_option"] is not None:
        raise ValueError("The 15 option descriptions must fit without per-option truncation")
    write_json(args.output / "gpu_runtime.json", {
        "model_id": MODEL_ID, "model_revision": MODEL_REVISION,
        "runtime_source": str(args.runtime_source.resolve()),
        "model_path": str(args.model_dir.resolve()), "torch": torch.__version__,
        "cuda": torch.version.cuda, "gpu": torch.cuda.get_device_name(0),
        "device": str(agent.device), "amp_dtype": str(agent.dtype),
        "batch_size": args.batch_size, "max_len": 8192, "head_max_len": 256,
        "option_budget": encoded["options"], "load_seconds": round(time.monotonic()-started, 3),
        "external_model_calls": 0,
    })
    print(f"CUDA model ready: {torch.cuda.get_device_name(0)}; batch {args.batch_size}", flush=True)

    def predict(states: list[str]) -> tuple[list[dict], float]:
        before = agent.cpu_fallback_count
        torch.cuda.synchronize()
        begin = time.monotonic()
        results = agent.predict_batch(states, questions, batch_size=args.batch_size,
                                      lang="zh", max_len=8192, head_max_len=256)
        torch.cuda.synchronize()
        elapsed = time.monotonic()-begin
        if agent.cpu_fallback_count != before or agent.device.type != "cuda":
            raise RuntimeError("A CUDA batch fell back to CPU; results are not accepted as a GPU run")
        if len(results) != len(states):
            raise ValueError("CUDA result count differs from the input batch")
        if any(result.get("usage", {}).get("truncated") for result in results):
            raise ValueError("A reaction was truncated; preserve the full input before accepting labels")
        return results, elapsed

    if args.action == "calibrate":
        cases = json.loads((args.output / "calibration_cases.json").read_text(encoding="utf-8"))
        results, elapsed = predict([row["text"] for row in cases])
        rows = [{**case, **canonical_result(result, mapping, labels), "raw_response": result}
                for case, result in zip(cases, results, strict=True)]
        for row in rows:
            row["matches_designed_expectation"] = row["top1_label"] in row["expected_labels"]
        write_json(args.output / "calibration_results.json", rows)
        summary = {"cases": len(rows), "matches_designed_expectation": sum(row["matches_designed_expectation"] for row in rows),
                   "elapsed_seconds": elapsed, "external_model_calls": 0,
                   "scope": "same designed Chinese fixtures; model and criteria language differ from CPU run"}
        write_json(args.output / "calibration_summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return

    completed = {row["unit_id"] for row in read_jsonl(args.output / "labels.jsonl")}
    pending = [row for row in inputs if row["unit_id"] not in completed]
    if args.limit:
        pending = pending[:args.limit]
    processed = 0
    begin = time.monotonic()
    with (args.output / "labels.jsonl").open("a", encoding="utf-8") as stream:
        for start in range(0, len(pending), args.batch_size):
            batch = pending[start:start+args.batch_size]
            results, elapsed = predict([row["text"] for row in batch])
            for item, response in zip(batch, results, strict=True):
                parsed = canonical_result(response, mapping, labels)
                stream.write(json.dumps({"unit_id": item["unit_id"], **parsed,
                    "elapsed_seconds": round(elapsed/len(batch), 6),
                    "device": "cuda", "raw_response": response}, ensure_ascii=False)+'\n')
            stream.flush()
            processed += len(batch)
            if processed % 128 == 0 or processed == len(pending):
                print(f"Labeled {len(completed)+processed}/{len(inputs)}; "
                      f"{time.monotonic()-begin:.1f}s", flush=True)
    write_json(args.output / "gpu_batch_summary.json", {
        "processed_this_call": processed, "elapsed_seconds": time.monotonic()-begin,
        "gpu_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
        "cpu_fallback_count": agent.cpu_fallback_count, "external_model_calls": 0})
    summarize(args.output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "calibrate", "classify"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cpu-run", type=Path)
    parser.add_argument("--runtime-source", type=Path, default=PROJECT/'.runtime/laya-gpu/source')
    parser.add_argument("--model-dir", type=Path, default=PROJECT/'.runtime/laya-gpu/model')
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.batch_size < 1 or (args.limit is not None and args.limit < 1):
        parser.error("batch-size and limit must be positive")
    if args.action == "prepare":
        if args.cpu_run is None:
            parser.error("prepare requires --cpu-run")
        prepare(args.cpu_run, args.output)
    else:
        run(args)


if __name__ == "__main__":
    main()
