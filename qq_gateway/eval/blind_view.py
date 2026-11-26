"""Render masked pairs with each side's real preceding dialogue."""
import argparse
import json
import random
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("run", type=Path)
parser.add_argument("--start", type=int, default=0)
parser.add_argument("--count", type=int, default=24)
parser.add_argument("--history", type=int, default=0)
parser.add_argument("--cases", type=Path, help="Render already completed pairs while a run is finishing")
args = parser.parse_args()
rows = [json.loads(line) for line in (args.run / "outputs.jsonl").read_text(encoding="utf-8").splitlines()]
by = {(row["case_id"], row["variant"]): row for row in rows}
if (args.run / "blind_pairs.json").exists():
    pairs = json.loads((args.run / "blind_pairs.json").read_text(encoding="utf-8"))
    mapping = json.loads((args.run / "blind_mapping.json").read_text(encoding="utf-8"))
else:
    cases = [json.loads(line) for line in args.cases.read_text(encoding="utf-8").splitlines()]
    rng, pairs, mapping = random.Random(202610180746), [], {}
    for case in cases:
        left, right = ("A", "B") if rng.random() < 0.5 else ("B", "A")
        cid = case["case_id"]
        mapping[cid] = {"X": left, "Y": right}
        pairs.append({"case_id": cid, "input": case["message"],
                      "X": by.get((cid, left), {}).get("text"), "Y": by.get((cid, right), {}).get("text")})
for pair in pairs[args.start:args.start + args.count]:
    if pair["X"] is None or pair["Y"] is None:
        raise ValueError("Requested blind pair is not completed yet")
    item = dict(pair)
    if args.history:
        for side in ("X", "Y"):
            row = by[pair["case_id"], mapping[pair["case_id"]][side]]
            messages = [m for m in row.get("messages", []) if m["role"] in ("user", "assistant")]
            # Last user is the current input. Earlier messages are real history.
            item[side + "_history"] = messages[-(args.history * 2 + 1):-1]
    print(json.dumps(item, ensure_ascii=False))
