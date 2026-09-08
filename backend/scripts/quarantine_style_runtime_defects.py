"""Preview/apply deterministic quarantine for defects found by runtime replay."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DEFECTS = {
    2287: "comfort reaction is off-topic and assumes gender/avatar; unsafe runtime exemplar",
    2774: "synthetic greeting invents current busyness/state",
    2785: "synthetic comfort claims fan group is currently present",
    2786: "synthetic comfort opens with a potentially dismissive reaction",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    database = args.database.resolve()
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute(
            f"SELECT id,prompt,response,review_status FROM style_examples WHERE id IN ({','.join('?' for _ in DEFECTS)}) ORDER BY id",
            list(DEFECTS),
        )]
    if {row["id"] for row in rows} != set(DEFECTS):
        raise SystemExit("one or more defect ids are missing; no changes applied")
    backup = None
    before = sha(database)
    if args.apply:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = database.with_name(f"{database.stem}.pre_runtime_quarantine.{stamp}{database.suffix}")
        shutil.copy2(database, backup_path)
        backup = str(backup_path)
        with sqlite3.connect(database) as conn:
            for item_id, reason in DEFECTS.items():
                conn.execute(
                    """UPDATE style_examples SET review_status='quarantined',
                       reviewer_id='runtime_regression_guard:2026-09-06',review_notes=?,
                       reviewed_at=datetime('now'),embedding_ref=NULL WHERE id=?""",
                    (reason, item_id),
                )
            conn.commit()
    report = {
        "mode": "apply" if args.apply else "preview", "database": str(database),
        "sha256_before": before, "sha256_after": sha(database), "backup": backup,
        "defects": [{**row, "reason": DEFECTS[row["id"]]} for row in rows],
        "production_switched": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"mode": report["mode"], "count": len(rows), "backup": backup}))


if __name__ == "__main__":
    main()
