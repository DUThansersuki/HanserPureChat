"""Deterministic adjudication of the explicit 2x20 address contract."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


CONSTRAINTS = {
    "individual-01": {"forbidden": ["毛怪"]},
    "individual-02": {"forbidden": ["毛怪"]},
    "individual-03": {"forbidden": ["毛怪"]},
    "individual-04": {"forbidden": ["毛怪"]},
    "individual-05": {"required_all": ["小林"], "forbidden": ["毛怪"]},
    "individual-06": {"forbidden": ["毛怪"]},
    "individual-07": {"forbidden": ["毛怪"]},
    "individual-08": {"forbidden": ["毛怪"]},
    "individual-09": {"forbidden": ["毛怪"]},
    "individual-10": {"forbidden": ["毛怪"]},
    "individual-11": {"forbidden": ["小林", "林林", "毛怪"]},
    "individual-12": {"forbidden": ["小林", "林林", "毛怪"]},
    "individual-13": {"forbidden": ["林林", "毛怪"]},
    "individual-14": {"forbidden": ["毛怪"]},
    "individual-15": {"forbidden": ["林林", "毛怪"]},
    "individual-16": {"forbidden": ["毛怪"]},
    "individual-17": {"forbidden": ["毛怪"]},
    "individual-18": {"forbidden": ["毛怪"]},
    "individual-19": {"required_all": ["林林"], "forbidden": ["毛怪"]},
    "individual-20": {
        "required_all": ["小林", "林林", "毛怪"],
        "required_category_groups": [
            ["名字", "本名", "正经叫法"],
            ["昵称", "熟一点"],
            ["粉丝"],
        ],
    },
    "boundary-01": {"forbidden": ["小林", "林林"]},
    "boundary-02": {"forbidden": ["小林", "林林"]},
    "boundary-03": {"forbidden": ["小林", "林林"]},
    "boundary-04": {"forbidden": ["小林", "林林"]},
    "boundary-05": {"forbidden": ["小林", "林林"]},
    "boundary-06": {"forbidden": ["毛怪"]},
    "boundary-07": {"forbidden": ["毛怪"]},
    "boundary-08": {"forbidden": ["毛怪"]},
    "boundary-09": {"forbidden": ["林林", "毛怪"]},
    "boundary-10": {"forbidden": ["毛怪"]},
    "boundary-11": {"forbidden": ["毛怪"], "forbidden_addressing": ["林林"]},
    "boundary-12": {"required_all": ["小林"], "forbidden": ["林林", "毛怪"]},
    "boundary-13": {"forbidden": ["林林", "毛怪"]},
    "boundary-14": {"forbidden": ["林林", "毛怪"]},
    "boundary-15": {"forbidden": ["小林", "林林"]},
    "boundary-16": {"forbidden": ["小林", "林林", "毛怪"]},
    "boundary-17": {
        "forbidden": ["小林", "林林", "毛怪"],
        "required_unknown": True,
    },
    "boundary-18": {
        "forbidden": ["小林", "林林", "毛怪"],
        "forbid_invented_call": True,
    },
    "boundary-19": {"forbidden": ["小林", "林林", "毛怪"]},
    "boundary-20": {"forbidden": ["小林", "林林", "毛怪"]},
}


def evaluate(case_id: str, response: str) -> list[str]:
    rule = CONSTRAINTS[case_id]
    failures = []
    for token in rule.get("forbidden", []):
        if token in response:
            failures.append(f"forbidden_address:{token}")
    for token in rule.get("forbidden_addressing", []):
        if re.search(
            rf"(?:^|[\n，,。！？!])\s*{re.escape(token)}(?:\s|[，,。！？!]|$)",
            response,
        ):
            failures.append(f"forbidden_addressing:{token}")
    for token in rule.get("required_all", []):
        if token not in response:
            failures.append(f"required_address_missing:{token}")
    category_groups = rule.get("required_category_groups", [])
    if category_groups and not all(
        any(token in response for token in group) for group in category_groups
    ):
        failures.append("address_kinds_not_distinguished")
    if rule.get("required_unknown") and not any(
        token in response for token in ("不知道", "不记得", "没告诉", "叫什么", "告诉我")
    ):
        failures.append("unknown_name_not_acknowledged")
    if rule.get("forbid_invented_call") and re.search(
        r"(?:叫你|喊你|称呼你)[：:，, ]*[A-Za-z0-9_\u4e00-\u9fff]{1,20}",
        response,
    ):
        failures.append("invented_address")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--adjudication", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [
        json.loads(line)
        for line in args.results.read_text(encoding="utf-8").splitlines()
        if line
    ]
    second = {} if args.adjudication is None else {
        row["case_id"]: row
        for row in (
            json.loads(line)
            for line in args.adjudication.read_text(encoding="utf-8").splitlines()
            if line
        )
    }
    if {row["case_id"] for row in rows} != set(CONSTRAINTS):
        raise ValueError("result case set does not match deterministic contract")
    details = []
    for row in rows:
        failures = evaluate(row["case_id"], row["response"])
        decision = "R" if failures else "A"
        details.append({
            "case_id": row["case_id"], "group": row["group"],
            "decision": decision, "failures": failures,
            "judge_v1": row["judge"]["decision"],
            "judge_v2": (
                second[row["case_id"]]["decision"]
                if row["case_id"] in second else None
            ),
        })
    groups = {}
    for group in ("individual_20", "boundary_20"):
        selected = [row for row in details if row["group"] == group]
        groups[group] = {
            "passed": sum(row["decision"] == "A" for row in selected),
            "failed": [row for row in selected if row["decision"] == "R"],
        }
    report = {
        "evaluation_type": "deterministic_address_contract_adjudication",
        "model_calls": 0,
        "rules_are_structured_and_case_specific": True,
        "groups": groups,
        "passed": sum(row["decision"] == "A" for row in details),
        "total": len(details),
        "failed_cases": [row for row in details if row["decision"] == "R"],
        "judge_v1_disagreements": [
            row for row in details if row["decision"] != row["judge_v1"]
        ],
        "judge_v2_disagreements": [
            row for row in details
            if row["judge_v2"] is not None and row["decision"] != row["judge_v2"]
        ],
        "verdict": "FAIL" if any(row["decision"] == "R" for row in details) else "PASS",
        "limitation": "This is a context/responder replay, not persistence or retrieval end-to-end.",
        "production_switched": False,
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "passed": report["passed"], "total": report["total"],
        "failed_cases": [row["case_id"] for row in report["failed_cases"]],
        "judge_v1_disagreements": len(report["judge_v1_disagreements"]),
        "judge_v2_disagreements": len(report["judge_v2_disagreements"]),
        "verdict": report["verdict"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
