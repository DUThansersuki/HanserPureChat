import sqlite3

import pytest

from hanser_qq.locking import service_lock
from hanser_qq.prepare import export_clean_seed


def test_seed_removes_runtime_continuity_and_voice_links_without_changing_source(tmp_path):
    source, dest = tmp_path / "seed.db", tmp_path / "qq.db"
    with sqlite3.connect(source) as conn:
        conn.executescript("""
            CREATE TABLE active_index_generations(collection TEXT,generation TEXT);
            INSERT INTO active_index_generations VALUES('style_examples','g1');
            CREATE TABLE documents(id INTEGER,text TEXT);
            INSERT INTO documents VALUES(1,'来源');
            CREATE TABLE continuity_turns(id TEXT);
            INSERT INTO continuity_turns VALUES('private-history');
            CREATE TABLE voice_job_links(id TEXT);
            INSERT INTO voice_job_links VALUES('private-voice');
        """)
    export_clean_seed(source, dest, "g1")
    with sqlite3.connect(dest) as conn:
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM continuity_turns").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM voice_job_links").fetchone()[0] == 0
    with sqlite3.connect(source) as conn:
        assert conn.execute("SELECT COUNT(*) FROM continuity_turns").fetchone()[0] == 1
    with pytest.raises(ValueError, match="generation"):
        export_clean_seed(source, tmp_path / "mismatch.db", "g2")


def test_single_service_lock_is_released_after_exit(tmp_path):
    path = tmp_path / "gateway.lock"
    with service_lock(path):
        with pytest.raises((RuntimeError, OSError)):
            with service_lock(path):
                pass
    with service_lock(path):
        pass
