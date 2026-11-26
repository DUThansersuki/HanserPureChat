"""CUDA batch classification with Jevify/Qwen and one shared fixed task prefix.

No text is generated. All 15 answer-token scores are projected together, and
the maximum is retained. Source text, review status and release gates stay separate.
"""
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
from scripts.local_jev_a_labels import INSTRUCTIONS, LABELS, parse_label, summarize, write_json
from scripts.run_style_reaction_pipeline_v2 import read_jsonl

MODEL_ID = "Qwen/Qwen2.5-1.5B-Instruct"
REVISION = "989aa7980e4cf806f80c7fef2b1adb7bc71aa306"


def prepare(cpu_run: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    if (output / "inputs.jsonl").exists():
        raise FileExistsError("This run's input already exists")
    for name in ["inputs.jsonl", "labels.frozen.json", "calibration_cases.json"]:
        shutil.copyfile(cpu_run / name, output / name)
    write_json(output / "questions.frozen.json", {"behavior": {
        "type": "choice", "instructions": INSTRUCTIONS,
        "criteria": {key: value[1] for key, value in LABELS.items()},
    }})
    prior = json.loads((cpu_run / "input_manifest.json").read_text(encoding="utf-8-sig"))
    write_json(output / "input_manifest.json", {**prior,
        "schema_version": "local-jevify-qwen-cuda-a-label-top1-v1", "input_reused_from": str(cpu_run),
        "model_id": MODEL_ID, "model_revision": REVISION,
        "template": "fixed instructions and options before JSON-encoded reaction text",
        "probability_semantics": "uncalibrated answer-letter probabilities conditioned on the 15 labels",
        "classification_is_semantic_approval": False})
    print(f"Frozen {prior['inputs']} reused source records for CUDA", flush=True)


def run(args: argparse.Namespace) -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["USE_TF"] = "0"
    sys.path.insert(0, str(args.runtime_source))
    import torch
    from jevify.engine import DecisionEngine, SYSTEM, common_prefix_length
    from jevify.schema import question_from_dict

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for this classification run")
    torch.set_num_threads(4)
    loaded_at = time.monotonic()
    engine = DecisionEngine.from_pretrained(
        str(args.model_dir.resolve()), device="cuda", dtype="float16", local_files_only=True,
        max_input_tokens=8192, shared_attention="off", branch_batch_size=args.batch_size,
    )
    if engine.device.type != "cuda":
        raise RuntimeError("The model is not on CUDA")
    questions = json.loads((args.output / "questions.frozen.json").read_text(encoding="utf-8"))
    question = question_from_dict(questions["behavior"])
    labels = json.loads((args.output / "labels.frozen.json").read_text(encoding="utf-8"))
    inputs = read_jsonl(args.output / "inputs.jsonl")
    choices = "\n".join(f"{chr(65+i)}. {key}: {description}" for i, (key, description) in enumerate(question.options))
    marker = "__HANSER_SOURCE_TEXT_7A16__"
    user_template = (question.instructions + "\nOptions:\n" + choices +
                     "\n\n<input_text>\n" + marker + "\n</input_text>\nReturn only the letter of the best option.")
    prompt_template = engine.tokenizer.apply_chat_template(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_template}],
        tokenize=False, add_generation_prompt=True, enable_thinking=False,
    )
    if prompt_template.count(marker) != 1:
        raise ValueError("The source insertion marker must occur exactly once")
    if (args.output / "prompt_template.frozen.txt").exists():
        if (args.output / "prompt_template.frozen.txt").read_text(encoding="utf-8") != prompt_template:
            raise ValueError("Prompt template changed across the same run")
    else:
        (args.output / "prompt_template.frozen.txt").write_text(prompt_template, encoding="utf-8")

    def encode(states: list[str]) -> list[list[int]]:
        rendered = [prompt_template.replace(marker, json.dumps(text, ensure_ascii=False)) for text in states]
        encoded = engine.tokenizer(rendered, add_special_tokens=False)["input_ids"]
        if any(len(row) > engine.max_input_tokens for row in encoded):
            raise ValueError("An input exceeds the token budget; no truncation is performed")
        return encoded

    # Establish the shared task prefix against all actual texts and the development fixtures.
    cases = json.loads((args.output / "calibration_cases.json").read_text(encoding="utf-8"))
    encoded_inputs = encode([row["text"] for row in inputs])
    encoded_cases = encode([row["text"] for row in cases])
    prefix_length = common_prefix_length(encoded_inputs + encoded_cases)
    with torch.inference_mode():
        _, prefix_cache = engine._hidden([encoded_inputs[0][:prefix_length]], use_cache=True)
    write_json(args.output / "gpu_runtime.json", {
        "model_id": MODEL_ID, "model_revision": REVISION,
        "runtime_source": str(args.runtime_source.resolve()), "model_path": str(args.model_dir.resolve()),
        "gpu": torch.cuda.get_device_name(0), "torch": torch.__version__, "cuda": torch.version.cuda,
        "device": str(engine.device), "dtype": str(engine.head.weight.dtype),
        "batch_size": args.batch_size, "shared_fixed_prefix_tokens": prefix_length,
        "max_input_tokens": max(map(len, encoded_inputs)), "generated_tokens": 0,
        "backend": "Jevify eager CUDA backbone and selected answer-row projection; batched states",
        "custom_linux_kernels": False, "external_model_calls": 0,
        "load_and_prepare_seconds": round(time.monotonic()-loaded_at, 3),
    })
    print(f"CUDA ready; fixed prefix {prefix_length} tokens; {args.batch_size} states per batch", flush=True)

    def predict(sequences: list[list[int]]) -> tuple[list[dict], float]:
        if any(row[:prefix_length] != encoded_inputs[0][:prefix_length] for row in sequences):
            raise ValueError("A source does not match the frozen prefix")
        torch.cuda.synchronize()
        begin = time.monotonic()
        with torch.inference_mode():
            cache = engine._fork_cache(prefix_cache)
            cache.batch_repeat_interleave(len(sequences))
            hidden, _ = engine._hidden([row[prefix_length:] for row in sequences],
                                      cache=cache, prefix_length=prefix_length, use_cache=True)
            scores = engine._project(hidden, [15]*len(sequences)).cpu().tolist()
        torch.cuda.synchronize()
        elapsed = time.monotonic()-begin
        results = [{"model": MODEL_ID, "answers": {"behavior": question.answer(vector)},
                    "usage": {"input_tokens": len(row), "output_tokens": 0},
                    "metadata": {"device": "cuda", "shared_prefix_tokens": prefix_length,
                                 "generated_tokens": 0}} for row, vector in zip(sequences, scores, strict=True)]
        return results, elapsed

    if args.action == "calibrate":
        rows, elapsed = [], 0.0
        for start in range(0, len(cases), args.batch_size):
            results, seconds = predict(encoded_cases[start:start+args.batch_size])
            elapsed += seconds
            rows.extend({**case, **parse_label(result, labels), "raw_response": result}
                        for case, result in zip(cases[start:start+args.batch_size], results, strict=True))
        for row in rows:
            row["matches_designed_expectation"] = row["top1_label"] in row["expected_labels"]
        parity = []
        for case, sequence, cached in zip(cases[:4], encoded_cases[:4], rows[:4], strict=True):
            with torch.inference_mode():
                hidden, _ = engine._hidden([sequence], use_cache=False)
                scores = engine._project(hidden, [15]).cpu().tolist()[0]
            reference = question.answer(scores)
            difference = max(abs(reference["probabilities"][key]-cached["label_scores"][key]) for key in labels)
            parity.append({"case_id": case["case_id"], "cached_label": cached["top1_label"],
                           "reference_label": reference["choice"], "max_abs_score_difference": difference,
                           "same_top1": reference["choice"] == cached["top1_label"]})
        write_json(args.output / "prefix_cache_parity.json", parity)
        if any(not row["same_top1"] for row in parity):
            raise RuntimeError("Fixed-prefix batching changed the highest label in the reference checks")
        write_json(args.output / "calibration_results.json", rows)
        summary = {"cases": len(rows), "matches_designed_expectation": sum(row["matches_designed_expectation"] for row in rows),
                   "elapsed_seconds": elapsed, "external_model_calls": 0,
                   "scope": "same Chinese development fixtures; not corpus accuracy or human gold"}
        write_json(args.output / "calibration_summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return

    completed = {row["unit_id"] for row in read_jsonl(args.output / "labels.jsonl")}
    pending = [(item, sequence) for item, sequence in zip(inputs, encoded_inputs, strict=True)
               if item["unit_id"] not in completed]
    if args.limit:
        pending = pending[:args.limit]
    processed, started = 0, time.monotonic()
    with (args.output / "labels.jsonl").open("a", encoding="utf-8") as stream:
        for start in range(0, len(pending), args.batch_size):
            batch = pending[start:start+args.batch_size]
            results, elapsed = predict([row[1] for row in batch])
            for (item, _), response in zip(batch, results, strict=True):
                stream.write(json.dumps({"unit_id": item["unit_id"], **parse_label(response, labels),
                    "elapsed_seconds": elapsed/len(batch), "raw_response": response,
                    "device": "cuda"}, ensure_ascii=False)+'\n')
            stream.flush()
            processed += len(batch)
            if processed % 128 == 0 or processed == len(pending):
                print(f"Labeled {len(completed)+processed}/{len(inputs)}; "
                      f"{time.monotonic()-started:.1f}s", flush=True)
    write_json(args.output / "gpu_batch_summary.json", {
        "processed_this_call": processed, "elapsed_seconds": time.monotonic()-started,
        "gpu_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
        "cpu_fallback_count": 0, "external_model_calls": 0, "generated_tokens": 0})
    summarize(args.output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "calibrate", "classify"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cpu-run", type=Path)
    parser.add_argument("--runtime-source", type=Path, default=PROJECT/'.runtime/jevify-gpu/source/src')
    parser.add_argument("--model-dir", type=Path, default=PROJECT/'.runtime/jevify-gpu/model')
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
