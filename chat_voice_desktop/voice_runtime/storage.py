from __future__ import annotations

import json
import shutil
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class JobCatalog:
    """Small durable job index; full snapshots and events stay on disk."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.path = self.root / "job_catalog.sqlite3"
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, identity TEXT UNIQUE, owner TEXT,
                    status TEXT, updated REAL, accessed REAL);
                CREATE TABLE IF NOT EXISTS artifacts (
                    job_id TEXT, path TEXT, PRIMARY KEY(job_id,path));
                CREATE INDEX IF NOT EXISTS artifact_paths ON artifacts(path);
            """)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def touch_artifact(self, path: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE jobs SET accessed=? WHERE id IN (SELECT job_id FROM artifacts WHERE path=?)", (time.time(), path))

    def record(self, job):
        now = time.time()
        with self.connect() as conn:
            conn.execute("""INSERT INTO jobs VALUES (?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET identity=excluded.identity,
                status=excluded.status,updated=excluded.updated""",
                (job.snapshot.job_id, job.identity, job.snapshot.owner,
                 job.snapshot.status.value, now, now))
            for segment in job.snapshot.ready_segments:
                conn.executemany("INSERT OR IGNORE INTO artifacts VALUES (?,?)", [
                    (job.snapshot.job_id, "artifacts/" + segment.audio.relative_path),
                    (job.snapshot.job_id, "timelines/" + segment.timeline.artifact_id + ".json"),
                ])

    def identity(self, identity: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM jobs WHERE identity=?", (identity,)).fetchone()
        return row["id"] if row else None

    def owner(self, job_id: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT owner FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row:
                conn.execute("UPDATE jobs SET accessed=? WHERE id=?", (time.time(), job_id))
        return row["owner"] if row else None

    def owns_timeline(self, owner: str, artifact_id: str) -> bool:
        with self.connect() as conn:
            return conn.execute("""SELECT 1 FROM artifacts a JOIN jobs j ON j.id=a.job_id
                WHERE j.owner=? AND a.path=? LIMIT 1""",
                (owner, f"timelines/{artifact_id}.json")).fetchone() is not None

    def interrupted(self) -> list[str]:
        with self.connect() as conn:
            return [row[0] for row in conn.execute("SELECT id FROM jobs WHERE status NOT IN ('completed','failed','cancelled')")]

    def maintenance(self, *, retention_days: int, max_bytes: int, max_jobs: int) -> dict:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM jobs ORDER BY accessed,updated").fetchall()
        if any(row["status"] not in {"completed", "failed", "cancelled"} for row in rows):
            return {"deferred": "active_jobs", "deleted_jobs": []}
        files = [path for directory in ("jobs", "packages", "artifacts", "timelines")
                 for path in (self.root / directory).rglob("*") if path.is_file()]
        size = sum(path.stat().st_size for path in files)
        now = time.time()
        removed = []
        for row in rows:
            expired = row["accessed"] < now - retention_days * 86400
            over_budget = size > max_bytes or len(rows) - len(removed) > max_jobs
            # Protect recently played history, even when the quota is full.
            if not expired and (not over_budget or row["accessed"] > now - 3600):
                continue
            job_root = (self.root / "jobs" / row["id"]).resolve()
            if job_root.parent != self.root / "jobs":
                raise ValueError("job cleanup path escapes jobs directory")
            package_path = (self.root / "packages" / (row["id"] + ".json")).resolve()
            if package_path.parent != self.root / "packages":
                raise ValueError("package cleanup path escapes packages directory")
            reclaimed = sum(p.stat().st_size for p in job_root.rglob("*") if p.is_file())
            if job_root.is_dir():
                shutil.rmtree(job_root)
            if package_path.is_file():
                reclaimed += package_path.stat().st_size
                package_path.unlink()
            with self.connect() as conn:
                refs = [r[0] for r in conn.execute("SELECT path FROM artifacts WHERE job_id=?", (row["id"],))]
                conn.execute("DELETE FROM artifacts WHERE job_id=?", (row["id"],))
                conn.execute("DELETE FROM jobs WHERE id=?", (row["id"],))
                for ref in refs:
                    if conn.execute("SELECT 1 FROM artifacts WHERE path=?", (ref,)).fetchone():
                        continue
                    path = (self.root / ref).resolve()
                    if not path.is_relative_to(self.root):
                        raise ValueError("artifact cleanup path escapes runtime directory")
                    if path.is_file():
                        reclaimed += path.stat().st_size
                        path.unlink()
            size -= reclaimed
            removed.append(row["id"])
        # Remove stale cache pointers and unpublished leftovers only while idle.
        with self.connect() as conn:
            retained = {row[0] for row in conn.execute("SELECT DISTINCT path FROM artifacts")}
        for directory in ("artifacts/owners", "timelines"):
            for path in (self.root / directory).rglob("*"):
                if not path.is_file():
                    continue
                if path.parent.name == "keys" and path.suffix == ".json":
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    if not (self.root / "artifacts" / raw["relative_path"]).is_file():
                        size -= path.stat().st_size
                        path.unlink()
                elif path.relative_to(self.root).as_posix() not in retained and path.stat().st_mtime < now - 3600:
                    size -= path.stat().st_size
                    path.unlink()
        return {"deleted_jobs": removed, "bytes": size, "max_bytes": max_bytes,
                "jobs": len(rows) - len(removed), "retention_days": retention_days,
                "over_budget": size > max_bytes}
