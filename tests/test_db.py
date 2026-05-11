"""Tests for the database layer."""

from pathlib import Path

import pytest

from heartscale.db import Database
from heartscale.models import Memory, RelationshipVector


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    with Database(tmp_path / "test.db") as database:
        yield database


def _mem(id="mem_0501_01", layer="recent_7d", direction="positive",
         intensity=3, protected=False, refs=0, tier="L1"):
    return Memory(
        id=id,
        date="2026-05-01",
        layer=layer,
        direction=direction,
        flavor=["tenderness", "attachment"],
        intensity=intensity,
        summary="测试记忆条目",
        tier=tier,
        refs=refs,
        protected=protected,
        linked_diary="2026-05-01.md",
    )


# ---------------------------------------------------------------------------
# Schema / init
# ---------------------------------------------------------------------------

def test_db_creates_schema(db):
    # All expected tables should exist
    rows = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    tables = {r["name"] for r in rows}
    assert "memories" in tables
    assert "relationship_vector" in tables
    assert "triggers" in tables


# ---------------------------------------------------------------------------
# Memory CRUD
# ---------------------------------------------------------------------------

def test_upsert_and_get(db):
    m = _mem()
    db.upsert_memory(m)
    fetched = db.get_memory(m.id)
    assert fetched is not None
    assert fetched.id == m.id
    assert fetched.summary == m.summary
    assert fetched.flavor == ["tenderness", "attachment"]


def test_upsert_updates_existing(db):
    m = _mem()
    db.upsert_memory(m)
    m.summary = "更新后的摘要"
    m.refs = 3
    db.upsert_memory(m)
    fetched = db.get_memory(m.id)
    assert fetched.summary == "更新后的摘要"
    assert fetched.refs == 3


def test_get_memories_by_layer(db):
    db.upsert_memory(_mem("mem_01", layer="recent_7d"))
    db.upsert_memory(_mem("mem_02", layer="recent_7d"))
    db.upsert_memory(_mem("mem_03", layer="recent_1m"))
    result = db.get_memories_by_layer("recent_7d")
    assert len(result) == 2
    assert all(m.layer == "recent_7d" for m in result)


def test_delete_memory(db):
    m = _mem()
    db.upsert_memory(m)
    db.delete_memory(m.id)
    assert db.get_memory(m.id) is None


def test_delete_memories_bulk(db):
    ids = ["mem_01", "mem_02", "mem_03"]
    for mid in ids:
        db.upsert_memory(_mem(mid))
    db.delete_memories(["mem_01", "mem_02"])
    assert db.get_memory("mem_01") is None
    assert db.get_memory("mem_02") is None
    assert db.get_memory("mem_03") is not None


def test_increment_refs(db):
    db.upsert_memory(_mem())
    db.increment_refs("mem_0501_01", by=2)
    assert db.get_memory("mem_0501_01").refs == 2


def test_mark_pending_review(db):
    db.upsert_memory(_mem())
    db.mark_pending_review("mem_0501_01")
    assert db.get_memory("mem_0501_01").pending_review is True


def test_protected_memories(db):
    db.upsert_memory(_mem("mem_p", protected=True))
    db.upsert_memory(_mem("mem_u", protected=False))
    protected = db.get_protected_memories()
    assert len(protected) == 1
    assert protected[0].id == "mem_p"


# ---------------------------------------------------------------------------
# Relationship vector
# ---------------------------------------------------------------------------

def test_relationship_vector_default(db):
    rv = db.get_relationship_vector()
    assert rv.closeness == 0.5
    assert rv.trust == 0.5
    assert rv.dependency == 0.3
    assert rv.tension == 0.1
    assert rv.missing == 0.5


def test_upsert_relationship_vector(db):
    rv = RelationshipVector(closeness=0.8, trust=0.9, dependency=0.4,
                            tension=0.05, missing=0.7)
    db.upsert_relationship_vector(rv)
    fetched = db.get_relationship_vector()
    assert fetched.closeness == pytest.approx(0.8)
    assert fetched.trust == pytest.approx(0.9)


def test_event_nudge_positive(db):
    rv = RelationshipVector()
    m = _mem(intensity=4, direction="positive")
    rv.apply_event_nudge(m)
    assert rv.closeness > 0.5
    assert rv.trust > 0.5
    assert rv.missing < 0.5


def test_event_nudge_ignored_below_threshold(db):
    rv = RelationshipVector()
    m = _mem(intensity=3, direction="positive")
    rv.apply_event_nudge(m)
    assert rv.closeness == 0.5  # unchanged


def test_event_nudge_negative(db):
    rv = RelationshipVector()
    m = _mem(intensity=4, direction="negative")
    rv.apply_event_nudge(m)
    assert rv.tension > 0.1
    assert rv.closeness < 0.5


# ---------------------------------------------------------------------------
# Trigger index
# ---------------------------------------------------------------------------

def test_add_and_recall_trigger(db):
    db.upsert_memory(_mem("mem_t1"))
    db.add_trigger("熬夜", "mem_t1")
    results = db.get_memories_by_keyword("熬夜")
    assert len(results) == 1
    assert results[0].id == "mem_t1"


def test_trigger_case_normalized(db):
    db.upsert_memory(_mem("mem_t2"))
    db.add_trigger("  熬夜  ", "mem_t2")
    results = db.get_memories_by_keyword("熬夜")
    assert any(m.id == "mem_t2" for m in results)


def test_trigger_no_match(db):
    results = db.get_memories_by_keyword("不存在的关键词")
    assert results == []


def test_delete_triggers_for_memory(db):
    db.upsert_memory(_mem("mem_t3"))
    db.add_trigger("关键词", "mem_t3")
    db.delete_triggers_for_memory("mem_t3")
    assert db.get_memories_by_keyword("关键词") == []


# ---------------------------------------------------------------------------
# JSONL import / export
# ---------------------------------------------------------------------------

def test_import_seed_file(tmp_path):
    seed = Path(__file__).parent.parent / "seeds" / "example.jsonl"
    with Database(tmp_path / "seed_test.db") as db:
        count = db.import_limbic_jsonl(seed)
        assert count == 13
        all_mem = db.get_all_memories()
        assert len(all_mem) == 13


def test_export_and_reimport_roundtrip(tmp_path):
    seed = Path(__file__).parent.parent / "seeds" / "example.jsonl"
    export_path = tmp_path / "exported.jsonl"

    with Database(tmp_path / "db1.db") as db1:
        db1.import_limbic_jsonl(seed)
        db1.export_limbic_jsonl(export_path)

    with Database(tmp_path / "db2.db") as db2:
        count = db2.import_limbic_jsonl(export_path)
        assert count == 13
        m = db2.get_memory("mem_L3_001")
        assert m is not None
        assert m.protected is True
        assert m.tier == "L3"


def test_export_jsonl_no_metadata_leak(tmp_path):
    """limbic.jsonl must not expose internal score fields."""
    with Database(tmp_path / "db.db") as db:
        db.upsert_memory(_mem())
        out = tmp_path / "out.jsonl"
        db.export_limbic_jsonl(out)
    content = out.read_text(encoding="utf-8")
    assert "final_score" not in content
    assert "compression_score" not in content
    assert "created_at" not in content


def test_import_missing_file_raises(tmp_path):
    with Database(tmp_path / "db.db") as db:
        with pytest.raises(FileNotFoundError):
            db.import_limbic_jsonl(tmp_path / "nonexistent.jsonl")
