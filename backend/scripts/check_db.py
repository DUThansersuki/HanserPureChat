from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hanser_agent.config import load_settings
from hanser_agent.db import connect, init_db

settings = load_settings()
with connect(settings.db_path) as conn:
    init_db(conn)
    docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    tokens = conn.execute("SELECT COUNT(*) FROM doc_tokens").fetchone()[0]
print(f"db={settings.db_path}")
print(f"documents={docs}")
print(f"doc_tokens={tokens}")
