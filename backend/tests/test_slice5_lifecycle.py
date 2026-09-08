from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hanser_agent import db
from hanser_agent.agent.conversation import (
    ConversationOwnershipError,
    ConversationStore,
)
from hanser_agent.retrieval.indexer import delete_document_chunks
from hanser_agent.retrieval.vector_store import SQLiteVectorStore


def migrated_database(path: Path) -> None:
    with db.connect(path) as conn:
        db.init_db(conn)
        db.apply_slice5_ownership_index_migration(conn)


class Slice5OwnershipTests(unittest.TestCase):
    def test_two_users_cannot_share_a_conversation_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.db"
            migrated_database(path)
            store = ConversationStore(path)
            store.append_turn(
                conversation_id="same", user_id="alice", user_text="secret",
                assistant_text="answer", model_name="m", trace_id="t",
                persona_version="p",
            )
            with self.assertRaises(ConversationOwnershipError):
                store.get_recent("same", user_id="bob")
            with self.assertRaises(ConversationOwnershipError):
                store.append_turn(
                    conversation_id="same", user_id="bob", user_text="steal",
                    assistant_text="no", model_name="m", trace_id="t2",
                    persona_version="p",
                )
            self.assertEqual(
                [m.content for m in store.get_recent("same", user_id="alice")],
                ["secret", "answer"],
            )
            store.append_turn(
                conversation_id="same", user_id="alice", user_text="again",
                assistant_text="continued", model_name="m", trace_id="t3",
                persona_version="p",
            )
            with db.connect(path) as conn:
                row = conn.execute("SELECT user_id FROM conversations WHERE id='same'").fetchone()
                self.assertEqual(str(row["user_id"]), "alice")
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 4)


class Slice5IndexLifecycleTests(unittest.TestCase):
    def test_failed_build_keeps_old_generation_and_cross_instance_cache_refreshes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.db"
            with db.connect(path) as conn:
                db.init_db(conn)
            legacy = SQLiteVectorStore(path)
            legacy.upsert("facts", "m", [("old", [1.0, 0.0])])
            with db.connect(path) as conn:
                db.apply_slice5_ownership_index_migration(conn)

            reader = SQLiteVectorStore(path)
            writer = SQLiteVectorStore(path)
            self.assertEqual(reader.search("facts", "m", [1.0, 0.0], top_k=1)[0].item_id, "old")

            failed = writer.begin_generation("facts", "m", 2, source_revision="r2", config_hash="c1")
            writer.stage_upsert("facts", failed, "m", [("partial", [1.0, 0.0])])
            writer.fail_generation("facts", failed)
            self.assertEqual(reader.search("facts", "m", [1.0, 0.0], top_k=1)[0].item_id, "old")

            current = writer.begin_generation("facts", "m", 2, source_revision="r3", config_hash="c2")
            writer.stage_upsert("facts", current, "m", [("new", [1.0, 0.0])])
            writer.publish_generation("facts", current, expected_count=1)
            self.assertEqual(reader.search("facts", "m", [1.0, 0.0], top_k=1)[0].item_id, "new")
            with db.connect(path) as conn:
                meta = conn.execute("SELECT source_revision,config_hash,item_count FROM index_generations WHERE collection='facts' AND generation=?", (current,)).fetchone()
                self.assertEqual(tuple(meta), ("r3", "c2", 1))

    def test_model_dimension_change_requires_new_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.db"
            migrated_database(path)
            store = SQLiteVectorStore(path)
            generation = store.begin_generation("facts", "m2", 3, source_revision="r", config_hash="c")
            with self.assertRaises(ValueError):
                store.stage_upsert("facts", generation, "m2", [("bad", [1.0, 0.0])])
            store.stage_upsert("facts", generation, "m2", [("ok", [1.0, 0.0, 0.0])])
            store.publish_generation("facts", generation, expected_count=1)
            self.assertEqual(store.search("facts", "m2", [1.0, 0.0, 0.0], top_k=1)[0].item_id, "ok")

    def test_new_modified_and_deleted_items_follow_only_published_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.db"
            migrated_database(path)
            store = SQLiteVectorStore(path)
            first = store.begin_generation("docs", "m", 2, source_revision="docs-r1", config_hash="c")
            store.stage_upsert("docs", first, "m", [("1", [1.0, 0.0]), ("2", [0.0, 1.0])])
            store.publish_generation("docs", first, expected_count=2)
            self.assertEqual(store.search("docs", "m", [0.0, 1.0], top_k=1)[0].item_id, "2")

            second = store.begin_generation("docs", "m", 2, source_revision="docs-r2", config_hash="c")
            store.stage_upsert("docs", second, "m", [("1", [1.0, 0.0]), ("2", [-1.0, 0.0])])
            store.publish_generation("docs", second, expected_count=2)
            self.assertEqual(store.search("docs", "m", [1.0, 0.0], top_k=1)[0].item_id, "1")

            third = store.begin_generation("docs", "m", 2, source_revision="docs-r3", config_hash="c")
            store.stage_upsert("docs", third, "m", [("1", [1.0, 0.0])])
            store.publish_generation("docs", third, expected_count=1)
            self.assertEqual(store.count("docs"), 1)
            self.assertNotIn("2", [hit.item_id for hit in store.search("docs", "m", [0.0, 1.0], top_k=5)])

    def test_cache_refreshes_after_publish_from_another_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.db"
            migrated_database(path)
            reader = SQLiteVectorStore(path)
            reader.upsert("cross", "m", [("old", [1.0, 0.0])])
            self.assertEqual(reader.search("cross", "m", [1.0, 0.0], top_k=1)[0].item_id, "old")
            code = (
                f"import sys;sys.path.insert(0,r'{Path.cwd() / 'backend'}');"
                "from hanser_agent.retrieval.vector_store import SQLiteVectorStore;"
                f"s=SQLiteVectorStore(r'{path}');"
                "g=s.begin_generation('cross','m',2,source_revision='subprocess',config_hash='c');"
                "s.stage_upsert('cross',g,'m',[('new',[1.0,0.0])]);"
                "s.publish_generation('cross',g,expected_count=1)"
            )
            subprocess.check_call([sys.executable, "-c", code])
            self.assertEqual(reader.search("cross", "m", [1.0, 0.0], top_k=1)[0].item_id, "new")

    def test_document_delete_removes_associated_chunk_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.db"
            migrated_database(path)
            with db.connect(path) as conn:
                cursor = conn.execute("INSERT INTO documents(filename,filepath,content,mtime,size,indexed_at) VALUES ('a','a','x',0,1,'now')")
                document_id = int(cursor.lastrowid)
                chunk = conn.execute("INSERT INTO document_chunks(document_id,chunk_index,text,start_char,end_char,token_count,metadata_json) VALUES (?,0,'x',0,1,1,'{}')", (document_id,))
                conn.execute("INSERT INTO chunk_tokens(chunk_id,token,tf) VALUES (?,'x',1)", (int(chunk.lastrowid),))
                conn.commit()
            self.assertEqual(delete_document_chunks(db_path=path, document_id=document_id), 1)
            with db.connect(path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM chunk_tokens").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
