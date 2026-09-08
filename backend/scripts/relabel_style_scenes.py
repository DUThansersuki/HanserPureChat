"""Preview or apply deterministic style-label corrections without rebuilding rows."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from hanser_agent.persona.data_pipeline import label_style  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    database = args.database.resolve()
    before_hash = _sha256(database)
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id,prompt,response,scene,speech_act,response_mode FROM style_examples ORDER BY id"
        ).fetchall()
        changes: list[dict[str, object]] = []
        for row in rows:
            labels = label_style(str(row["prompt"]), str(row["response"]))
            before = {
                "scene": row["scene"],
                "speech_act": row["speech_act"],
                "response_mode": row["response_mode"],
            }
            after = {key: labels[key] for key in before}
            if before != after:
                changes.append({
                    "id": int(row["id"]), "prompt": row["prompt"],
                    "before": before, "after": after,
                })

    backup: str | None = None
    if args.apply and changes:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = database.with_name(f"{database.stem}.pre_relabel.{stamp}{database.suffix}")
        shutil.copy2(database, backup_path)
        backup = str(backup_path)
        with sqlite3.connect(database) as conn:
            conn.executemany(
                "UPDATE style_examples SET scene=?,speech_act=?,response_mode=? WHERE id=?",
                [
                    (
                        item["after"]["scene"], item["after"]["speech_act"],
                        item["after"]["response_mode"], item["id"],
                    )
                    for item in changes
                ],
            )
            conn.commit()
    report = {
        "mode": "apply" if args.apply else "preview",
        "database": str(database), "database_sha256_before": before_hash,
        "database_sha256_after": _sha256(database), "backup": backup,
        "row_count": len(rows), "change_count": len(changes), "changes": changes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
