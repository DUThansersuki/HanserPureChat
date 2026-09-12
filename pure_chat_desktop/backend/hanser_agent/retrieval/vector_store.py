from __future__ import annotations

from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from .. import db


@dataclass(frozen=True, slots=True)
class VectorHit:
    item_id: str
    score: float


class VectorStore(Protocol):
    def clear(self, collection: str) -> None: ...
    def upsert(self, collection: str, model: str, items: list[tuple[str, list[float]]]) -> None: ...
    def search(self, collection: str, model: str, query_vector: list[float], *, top_k: int) -> list[VectorHit]: ...
    def search_filtered(self, collection: str, model: str, query_vector: list[float], *, item_ids: list[str], top_k: int) -> list[VectorHit]: ...
    def begin_generation(self, collection: str, model: str, dimensions: int, *, source_revision: str, config_hash: str) -> str: ...
    def stage_upsert(self, collection: str, generation: str, model: str, items: list[tuple[str, list[float]]]) -> None: ...
    def publish_generation(self, collection: str, generation: str, *, expected_count: int) -> None: ...
    def fail_generation(self, collection: str, generation: str) -> None: ...
    def delete_items(self, collection: str, item_ids: list[str]) -> None: ...


class SQLiteVectorStore:
    """SQLite exact-cosine store with revision-aware, atomic generations."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._cache: dict[tuple[str, str, int, str], tuple[list[str], object]] = {}

    @staticmethod
    def _migrated(conn) -> bool:
        return conn.execute("SELECT 1 FROM schema_migrations WHERE version=4").fetchone() is not None

    def begin_generation(self, collection: str, model: str, dimensions: int, *, source_revision: str, config_hash: str) -> str:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        if not source_revision or not config_hash:
            raise ValueError("source_revision and config_hash are required")
        generation = uuid4().hex
        with db.connect(self.db_path) as conn:
            db.init_db(conn)
            if not self._migrated(conn):
                raise RuntimeError("Slice 5 migration 4 is required")
            conn.execute(
                "INSERT INTO index_generations (collection,generation,status,model,dimensions,source_revision,config_hash,item_count,created_at) VALUES (?,?,'building',?,?,?,?,0,datetime('now'))",
                (collection, generation, model, dimensions, source_revision, config_hash),
            )
            conn.commit()
        return generation

    def stage_upsert(self, collection: str, generation: str, model: str, items: list[tuple[str, list[float]]]) -> None:
        if not items:
            return
        with db.connect(self.db_path) as conn:
            meta = conn.execute("SELECT status,model,dimensions FROM index_generations WHERE collection=? AND generation=?", (collection, generation)).fetchone()
            if meta is None or str(meta["status"]) != "building":
                raise ValueError("generation is not buildable")
            dimensions = int(meta["dimensions"])
            if str(meta["model"]) != model or any(len(v) != dimensions for _, v in items):
                raise ValueError("generation model/dimensions mismatch")
            conn.executemany(
                "INSERT OR REPLACE INTO vector_embeddings_v2 (collection,generation,item_id,model,dimensions,vector) VALUES (?,?,?,?,?,?)",
                [(collection, generation, item, model, dimensions, array("f", vector).tobytes()) for item, vector in items],
            )
            conn.execute("UPDATE index_generations SET item_count=(SELECT COUNT(*) FROM vector_embeddings_v2 WHERE collection=? AND generation=?) WHERE collection=? AND generation=?", (collection, generation, collection, generation))
            conn.commit()

    def publish_generation(self, collection: str, generation: str, *, expected_count: int) -> None:
        with db.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            meta = conn.execute("SELECT status,item_count,model,dimensions FROM index_generations WHERE collection=? AND generation=?", (collection, generation)).fetchone()
            if meta is None or str(meta["status"]) != "building":
                raise ValueError("generation is not publishable")
            if int(meta["item_count"]) != expected_count:
                raise ValueError("generation item count mismatch")
            invalid = conn.execute(
                "SELECT COUNT(*) FROM vector_embeddings_v2 WHERE collection=? AND generation=? AND (model!=? OR dimensions!=?)",
                (collection, generation, str(meta["model"]), int(meta["dimensions"])),
            ).fetchone()[0]
            actual = conn.execute(
                "SELECT COUNT(*) FROM vector_embeddings_v2 WHERE collection=? AND generation=?",
                (collection, generation),
            ).fetchone()[0]
            if invalid or int(actual) != expected_count:
                raise ValueError("generation contents failed publication validation")
            pointer = conn.execute("SELECT revision FROM active_index_generations WHERE collection=?", (collection,)).fetchone()
            revision = (int(pointer["revision"]) if pointer else 0) + 1
            conn.execute("UPDATE index_generations SET status='retired' WHERE collection=? AND status='active'", (collection,))
            conn.execute("UPDATE index_generations SET status='active',published_at=datetime('now') WHERE collection=? AND generation=?", (collection, generation))
            conn.execute("INSERT INTO active_index_generations(collection,generation,revision,updated_at) VALUES (?,?,?,datetime('now')) ON CONFLICT(collection) DO UPDATE SET generation=excluded.generation,revision=excluded.revision,updated_at=excluded.updated_at", (collection, generation, revision))
            conn.commit()
        self._drop_collection_cache(collection)

    def fail_generation(self, collection: str, generation: str) -> None:
        with db.connect(self.db_path) as conn:
            conn.execute("UPDATE index_generations SET status='failed' WHERE collection=? AND generation=? AND status='building'", (collection, generation))
            conn.commit()

    def clear(self, collection: str) -> None:
        with db.connect(self.db_path) as conn:
            db.init_db(conn)
            if self._migrated(conn):
                pointer = conn.execute("SELECT generation FROM active_index_generations WHERE collection=?", (collection,)).fetchone()
                if pointer:
                    generation = str(pointer["generation"])
                    conn.execute("DELETE FROM vector_embeddings_v2 WHERE collection=? AND generation=?", (collection, generation))
                    conn.execute("UPDATE index_generations SET item_count=0 WHERE collection=? AND generation=?", (collection, generation))
                    conn.execute("UPDATE active_index_generations SET revision=revision+1,updated_at=datetime('now') WHERE collection=?", (collection,))
            else:
                conn.execute("DELETE FROM vector_embeddings WHERE collection=?", (collection,))
            conn.commit()
        self._drop_collection_cache(collection)

    def delete_items(self, collection: str, item_ids: list[str]) -> None:
        if not item_ids:
            return
        placeholders = ",".join("?" for _ in item_ids)
        with db.connect(self.db_path) as conn:
            db.init_db(conn)
            if self._migrated(conn):
                pointer = conn.execute(
                    "SELECT generation FROM active_index_generations WHERE collection=?",
                    (collection,),
                ).fetchone()
                if pointer:
                    generation = str(pointer["generation"])
                    conn.execute(
                        f"DELETE FROM vector_embeddings_v2 WHERE collection=? AND generation=? AND item_id IN ({placeholders})",
                        [collection, generation, *item_ids],
                    )
                    conn.execute("UPDATE index_generations SET item_count=(SELECT COUNT(*) FROM vector_embeddings_v2 WHERE collection=? AND generation=?) WHERE collection=? AND generation=?", (collection,generation,collection,generation))
                    conn.execute("UPDATE active_index_generations SET revision=revision+1,updated_at=datetime('now') WHERE collection=?", (collection,))
            else:
                conn.execute(
                    f"DELETE FROM vector_embeddings WHERE collection=? AND item_id IN ({placeholders})",
                    [collection, *item_ids],
                )
            conn.commit()
        self._drop_collection_cache(collection)

    def upsert(self, collection: str, model: str, items: list[tuple[str, list[float]]]) -> None:
        if not items:
            return
        dimensions = len(items[0][1])
        if any(len(vector) != dimensions for _, vector in items):
            raise ValueError("mixed vector dimensions")
        create_new = False
        with db.connect(self.db_path) as conn:
            db.init_db(conn)
            if self._migrated(conn):
                meta = conn.execute("SELECT p.generation,g.model,g.dimensions FROM active_index_generations p JOIN index_generations g ON g.collection=p.collection AND g.generation=p.generation WHERE p.collection=?", (collection,)).fetchone()
                if meta is None:
                    create_new = True
                else:
                    if str(meta["model"]) != model or int(meta["dimensions"]) != dimensions:
                        raise ValueError("active generation model/dimensions mismatch")
                    generation = str(meta["generation"])
                    conn.executemany("INSERT OR REPLACE INTO vector_embeddings_v2 VALUES (?,?,?,?,?,?)", [(collection, generation, item, model, dimensions, array("f", vector).tobytes()) for item, vector in items])
                    conn.execute("UPDATE index_generations SET item_count=(SELECT COUNT(*) FROM vector_embeddings_v2 WHERE collection=? AND generation=?) WHERE collection=? AND generation=?", (collection,generation,collection,generation))
                    conn.execute("UPDATE active_index_generations SET revision=revision+1,updated_at=datetime('now') WHERE collection=?", (collection,))
            else:
                conn.executemany("INSERT OR REPLACE INTO vector_embeddings(collection,item_id,model,dimensions,vector) VALUES (?,?,?,?,?)", [(collection, item, model, dimensions, array("f", vector).tobytes()) for item, vector in items])
            conn.commit()
        if create_new:
            generation = self.begin_generation(collection, model, dimensions, source_revision="incremental", config_hash="incremental")
            self.stage_upsert(collection, generation, model, items)
            self.publish_generation(collection, generation, expected_count=len(items))
        self._drop_collection_cache(collection)

    def _active(self, conn, collection: str, model: str):
        if not self._migrated(conn):
            return None
        return conn.execute("SELECT p.generation,p.revision,g.dimensions FROM active_index_generations p JOIN index_generations g ON g.collection=p.collection AND g.generation=p.generation WHERE p.collection=? AND g.model=? AND g.status='active'", (collection, model)).fetchone()

    def _rows(self, collection: str, model: str, item_ids: list[str] | None = None):
        with db.connect(self.db_path) as conn:
            active = self._active(conn, collection, model)
            if active is not None:
                sql = "SELECT item_id,vector FROM vector_embeddings_v2 WHERE collection=? AND generation=? AND model=?"
                params: list[object] = [collection, str(active["generation"]), model]
            elif self._migrated(conn):
                return [], None
            else:
                sql = "SELECT item_id,vector FROM vector_embeddings WHERE collection=? AND model=?"
                params = [collection, model]
            if item_ids is not None:
                sql += " AND item_id IN (" + ",".join("?" for _ in item_ids) + ")"
                params.extend(item_ids)
            return conn.execute(sql + " ORDER BY item_id", params).fetchall(), active

    def search(self, collection: str, model: str, query_vector: list[float], *, top_k: int) -> list[VectorHit]:
        item_ids, matrix = self._matrix(collection, model)
        return self._rank(item_ids, matrix, query_vector, top_k)

    def search_filtered(self, collection: str, model: str, query_vector: list[float], *, item_ids: list[str], top_k: int) -> list[VectorHit]:
        if not item_ids:
            return []
        ids, matrix = self._matrix(collection, model)
        allowed = set(item_ids)
        indices = [index for index, item_id in enumerate(ids) if item_id in allowed]
        if not indices:
            return []
        filtered_ids = [ids[index] for index in indices]
        return self._rank(filtered_ids, matrix[indices], query_vector, top_k)

    def count(self, collection: str) -> int:
        with db.connect(self.db_path) as conn:
            db.init_db(conn)
            if self._migrated(conn):
                row = conn.execute("SELECT g.item_count FROM active_index_generations p JOIN index_generations g ON g.collection=p.collection AND g.generation=p.generation WHERE p.collection=?", (collection,)).fetchone()
                return int(row[0]) if row else 0
            return int(conn.execute("SELECT COUNT(*) FROM vector_embeddings WHERE collection=?", (collection,)).fetchone()[0])

    def _matrix(self, collection: str, model: str):
        with db.connect(self.db_path) as conn:
            active = self._active(conn, collection, model)
        revision = int(active["revision"]) if active is not None else 0
        generation = str(active["generation"]) if active is not None else "legacy"
        key = (collection, generation, revision, model)
        if key not in self._cache:
            rows, _ = self._rows(collection, model)
            self._cache[key] = self._to_matrix(rows)
        return self._cache[key]

    @staticmethod
    def _to_matrix(rows):
        import torch
        ids = [str(row["item_id"]) for row in rows]
        vectors = []
        for row in rows:
            values = array("f")
            values.frombytes(bytes(row["vector"]))
            vectors.append(values.tolist())
        return ids, torch.tensor(vectors, dtype=torch.float32) if vectors else torch.empty((0, 0), dtype=torch.float32)

    @staticmethod
    def _rank(item_ids, matrix, query_vector, top_k):
        if not item_ids:
            return []
        import torch
        query = torch.tensor(query_vector, dtype=torch.float32)
        if matrix.shape[1] != query.shape[0]:
            raise ValueError("query vector dimensions do not match active generation")
        values, indices = torch.topk(matrix @ query, k=min(top_k, len(item_ids)))
        return [VectorHit(item_ids[int(i)], float(v)) for v, i in zip(values.tolist(), indices.tolist(), strict=True)]

    def _drop_collection_cache(self, collection: str) -> None:
        for key in [key for key in self._cache if key[0] == collection]:
            self._cache.pop(key)
