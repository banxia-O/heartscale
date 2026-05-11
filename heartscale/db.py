"""SQLite database layer for heartscale.

Schema:
  memories            — three-tier memory store (L1 / L2 / L3)
  relationship_vector — five-dimensional relationship state
  triggers            — keyword → memory_id index (Phase 1 recall)
  vec_memories        — sqlite-vec virtual table for embeddings (Phase 2, optional)
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

try:
    import sqlite_vec  # type: ignore
    _HAS_VEC = True
except ImportError:
    _HAS_VEC = False

from heartscale.models import Memory, RelationshipVector, _now

# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS memories (
    id                  TEXT PRIMARY KEY,
    date                TEXT NOT NULL,
    layer               TEXT NOT NULL,
    direction           TEXT NOT NULL,
    flavor              TEXT NOT NULL DEFAULT '[]',   -- JSON array
    intensity           INTEGER NOT NULL DEFAULT 3,
    summary             TEXT NOT NULL DEFAULT '',
    linked_diary        TEXT,
    linked_from         TEXT,
    tier                TEXT NOT NULL DEFAULT 'L1',
    refs                INTEGER NOT NULL DEFAULT 0,
    protected           INTEGER NOT NULL DEFAULT 0,   -- 0/1 bool
    pending_review      INTEGER NOT NULL DEFAULT 0,
    final_score         REAL NOT NULL DEFAULT 0.0,
    compression_score   REAL NOT NULL DEFAULT 0.0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memories_layer ON memories(layer);
CREATE INDEX IF NOT EXISTS idx_memories_tier  ON memories(tier);
CREATE INDEX IF NOT EXISTS idx_memories_date  ON memories(date);

CREATE TABLE IF NOT EXISTS relationship_vector (
    id          INTEGER PRIMARY KEY DEFAULT 1,   -- singleton row
    closeness   REAL NOT NULL DEFAULT 0.5,
    trust       REAL NOT NULL DEFAULT 0.5,
    dependency  REAL NOT NULL DEFAULT 0.3,
    tension     REAL NOT NULL DEFAULT 0.1,
    missing     REAL NOT NULL DEFAULT 0.5,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS triggers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword     TEXT NOT NULL,
    memory_id   TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    UNIQUE(keyword, memory_id)
);

CREATE INDEX IF NOT EXISTS idx_triggers_keyword ON triggers(keyword);
"""

_VEC_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0(
    memory_id TEXT PRIMARY KEY,
    embedding float[{dim}]
);
"""


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------

class Database:
    def __init__(self, path: Path, embedding_dim: int = 1024):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema(embedding_dim)

    def _init_schema(self, embedding_dim: int) -> None:
        self._conn.executescript(_DDL)
        if _HAS_VEC:
            sqlite_vec.load(self._conn)
            self._conn.executescript(_VEC_DDL.format(dim=embedding_dim))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Memory CRUD                                                          #
    # ------------------------------------------------------------------ #

    def upsert_memory(self, m: Memory) -> None:
        """Insert or replace a memory record."""
        self._conn.execute(
            """
            INSERT INTO memories
                (id, date, layer, direction, flavor, intensity, summary,
                 linked_diary, linked_from, tier, refs, protected,
                 pending_review, final_score, compression_score,
                 created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                date=excluded.date,
                layer=excluded.layer,
                direction=excluded.direction,
                flavor=excluded.flavor,
                intensity=excluded.intensity,
                summary=excluded.summary,
                linked_diary=excluded.linked_diary,
                linked_from=excluded.linked_from,
                tier=excluded.tier,
                refs=excluded.refs,
                protected=excluded.protected,
                pending_review=excluded.pending_review,
                final_score=excluded.final_score,
                compression_score=excluded.compression_score,
                updated_at=excluded.updated_at
            """,
            (
                m.id, m.date, m.layer, m.direction,
                json.dumps(m.flavor, ensure_ascii=False),
                m.intensity, m.summary,
                m.linked_diary, m.linked_from, m.tier,
                m.refs, int(m.protected), int(m.pending_review),
                m.final_score, m.compression_score,
                m.created_at, m.updated_at,
            ),
        )
        self._conn.commit()

    def get_memory(self, memory_id: str) -> Optional[Memory]:
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        return _row_to_memory(row) if row else None

    def get_memories_by_layer(self, layer: str) -> list[Memory]:
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE layer = ? ORDER BY date DESC",
            (layer,),
        ).fetchall()
        return [_row_to_memory(r) for r in rows]

    def get_all_memories(self) -> list[Memory]:
        rows = self._conn.execute(
            "SELECT * FROM memories ORDER BY date DESC"
        ).fetchall()
        return [_row_to_memory(r) for r in rows]

    def get_protected_memories(self) -> list[Memory]:
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE protected = 1"
        ).fetchall()
        return [_row_to_memory(r) for r in rows]

    def delete_memory(self, memory_id: str) -> None:
        self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self._conn.commit()

    def delete_memories(self, memory_ids: list[str]) -> None:
        self._conn.executemany(
            "DELETE FROM memories WHERE id = ?", [(mid,) for mid in memory_ids]
        )
        self._conn.commit()

    def increment_refs(self, memory_id: str, by: int = 1) -> None:
        """Record that a memory was injected into context (used for scoring)."""
        self._conn.execute(
            "UPDATE memories SET refs = refs + ?, updated_at = ? WHERE id = ?",
            (by, _now(), memory_id),
        )
        self._conn.commit()

    def mark_pending_review(self, memory_id: str) -> None:
        self._conn.execute(
            "UPDATE memories SET pending_review = 1, updated_at = ? WHERE id = ?",
            (_now(), memory_id),
        )
        self._conn.commit()

    def update_scores(self, memory_id: str, final_score: float, compression_score: float) -> None:
        self._conn.execute(
            "UPDATE memories SET final_score=?, compression_score=?, updated_at=? WHERE id=?",
            (final_score, compression_score, _now(), memory_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------ #
    # Relationship vector                                                  #
    # ------------------------------------------------------------------ #

    def get_relationship_vector(self) -> RelationshipVector:
        row = self._conn.execute("SELECT * FROM relationship_vector WHERE id = 1").fetchone()
        if row is None:
            return RelationshipVector()
        return RelationshipVector(
            closeness=row["closeness"],
            trust=row["trust"],
            dependency=row["dependency"],
            tension=row["tension"],
            missing=row["missing"],
            updated_at=row["updated_at"],
        )

    def upsert_relationship_vector(self, rv: RelationshipVector) -> None:
        self._conn.execute(
            """
            INSERT INTO relationship_vector (id, closeness, trust, dependency, tension, missing, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                closeness=excluded.closeness,
                trust=excluded.trust,
                dependency=excluded.dependency,
                tension=excluded.tension,
                missing=excluded.missing,
                updated_at=excluded.updated_at
            """,
            (rv.closeness, rv.trust, rv.dependency, rv.tension, rv.missing, rv.updated_at),
        )
        self._conn.commit()

    # ------------------------------------------------------------------ #
    # Trigger index                                                        #
    # ------------------------------------------------------------------ #

    def add_trigger(self, keyword: str, memory_id: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO triggers (keyword, memory_id) VALUES (?, ?)",
            (keyword.lower().strip(), memory_id),
        )
        self._conn.commit()

    def get_memories_by_keyword(self, keyword: str) -> list[Memory]:
        rows = self._conn.execute(
            """
            SELECT m.* FROM memories m
            JOIN triggers t ON t.memory_id = m.id
            WHERE t.keyword = ?
            """,
            (keyword.lower().strip(),),
        ).fetchall()
        return [_row_to_memory(r) for r in rows]

    def delete_triggers_for_memory(self, memory_id: str) -> None:
        self._conn.execute("DELETE FROM triggers WHERE memory_id = ?", (memory_id,))
        self._conn.commit()

    # ------------------------------------------------------------------ #
    # Vector store (Phase 2, no-op if sqlite-vec not available)           #
    # ------------------------------------------------------------------ #

    def upsert_embedding(self, memory_id: str, embedding: list[float]) -> None:
        if not _HAS_VEC:
            return
        import struct
        blob = struct.pack(f"{len(embedding)}f", *embedding)
        self._conn.execute(
            "INSERT OR REPLACE INTO vec_memories(memory_id, embedding) VALUES (?, ?)",
            (memory_id, blob),
        )
        self._conn.commit()

    def search_similar(self, embedding: list[float], limit: int = 10) -> list[str]:
        """Return memory_ids sorted by cosine similarity. Returns [] if vec unavailable."""
        if not _HAS_VEC:
            return []
        import struct
        blob = struct.pack(f"{len(embedding)}f", *embedding)
        rows = self._conn.execute(
            "SELECT memory_id FROM vec_memories WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
            (blob, limit),
        ).fetchall()
        return [r["memory_id"] for r in rows]

    # ------------------------------------------------------------------ #
    # Limbic JSONL import / export                                         #
    # ------------------------------------------------------------------ #

    def export_limbic_jsonl(self, path: Path) -> int:
        """Write all active memories to limbic.jsonl. Returns count written."""
        memories = self.get_all_memories()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for m in memories:
                f.write(m.to_jsonl_line() + "\n")
        return len(memories)

    def import_limbic_jsonl(self, path: Path) -> int:
        """Load memories from a limbic.jsonl file into the DB. Returns count imported."""
        if not path.exists():
            raise FileNotFoundError(f"Seed file not found: {path}")
        count = 0
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                data = json.loads(line)
                m = Memory.from_jsonl_dict(data)
                self.upsert_memory(m)
                count += 1
        return count


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _row_to_memory(row: sqlite3.Row) -> Memory:
    return Memory(
        id=row["id"],
        date=row["date"],
        layer=row["layer"],
        direction=row["direction"],
        flavor=json.loads(row["flavor"]),
        intensity=row["intensity"],
        summary=row["summary"],
        linked_diary=row["linked_diary"],
        linked_from=row["linked_from"],
        tier=row["tier"],
        refs=row["refs"],
        protected=bool(row["protected"]),
        pending_review=bool(row["pending_review"]),
        final_score=row["final_score"],
        compression_score=row["compression_score"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
