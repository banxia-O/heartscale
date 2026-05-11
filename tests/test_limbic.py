"""Tests for limbic cascade compression."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from heartscale.db import Database
from heartscale.limbic import (
    LAYER_CAPS,
    LimbicCompressor,
    _days_ago,
    _opposite_directions,
)
from heartscale.models import Memory, _now
from heartscale.scoring import compute_compression_score, compute_final_score


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TODAY = "2026-05-11"


@pytest.fixture
def db(tmp_path):
    with Database(tmp_path / "test.db") as database:
        yield database


@pytest.fixture
def comp(db):
    return LimbicCompressor(db)


def _mem(
    mid: str,
    layer: str = "recent_7d",
    direction: str = "positive",
    intensity: int = 3,
    refs: int = 0,
    protected: bool = False,
    tier: str = "L1",
    date_str: str = TODAY,
    flavor: list[str] | None = None,
    linked_from: str | None = None,
    final_score: float = 0.5,
    compression_score: float = 0.5,
) -> Memory:
    return Memory(
        id=mid,
        date=date_str,
        layer=layer,
        direction=direction,
        flavor=flavor or ["tenderness"],
        intensity=intensity,
        summary=f"test memory {mid}",
        tier=tier,
        refs=refs,
        protected=protected,
        linked_from=linked_from,
        final_score=final_score,
        compression_score=compression_score,
    )


def _dated(days_ago: int) -> str:
    return str(date.fromisoformat(TODAY) - timedelta(days=days_ago))


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

def test_days_ago():
    result = _days_ago("2026-05-11", 7)
    assert result == "2026-05-04"


def test_opposite_directions():
    assert _opposite_directions("positive", "negative") is True
    assert _opposite_directions("negative", "positive") is True
    assert _opposite_directions("positive", "positive") is False
    assert _opposite_directions("mixed", "positive") is False


# ---------------------------------------------------------------------------
# _slide()
# ---------------------------------------------------------------------------

class TestSlide:
    def test_slides_aged_out_entries(self, db, comp):
        old = _mem("old", layer="recent_7d", date_str=_dated(10))
        fresh = _mem("fresh", layer="recent_7d", date_str=_dated(3))
        db.upsert_memory(old)
        db.upsert_memory(fresh)

        n = comp._slide("recent_7d", "recent_1m", TODAY)

        assert n == 1
        assert db.get_memory("old").layer == "recent_1m"
        assert db.get_memory("fresh").layer == "recent_7d"

    def test_protected_entries_do_not_slide(self, db, comp):
        protected = _mem("prot", layer="recent_7d",
                         date_str=_dated(10), protected=True)
        db.upsert_memory(protected)

        n = comp._slide("recent_7d", "recent_1m", TODAY)

        assert n == 0
        assert db.get_memory("prot").layer == "recent_7d"

    def test_returns_zero_when_nothing_to_slide(self, db, comp):
        fresh = _mem("fresh", layer="recent_7d", date_str=TODAY)
        db.upsert_memory(fresh)
        assert comp._slide("recent_7d", "recent_1m", TODAY) == 0


# ---------------------------------------------------------------------------
# _compress()
# ---------------------------------------------------------------------------

class TestCompress:
    def test_skips_when_no_new_arrivals(self, db, comp):
        for i in range(20):
            db.upsert_memory(_mem(f"m{i}", layer="recent_1m"))
        result = comp._compress("recent_1m", TODAY, new_arrivals=0)
        assert result.skipped is True
        # All entries survive — empty-window compensation
        assert len(db.get_memories_by_layer("recent_1m")) == 20

    def test_no_compression_when_under_cap(self, db, comp):
        for i in range(5):
            db.upsert_memory(_mem(f"m{i}", layer="recent_1m"))
        result = comp._compress("recent_1m", TODAY, new_arrivals=3)
        assert result.deleted == 0
        assert result.kept == 5

    def test_eliminates_lowest_score_entries(self, db, comp):
        cap = LAYER_CAPS["recent_1m"]
        # Fill to cap+3, with varying scores
        for i in range(cap + 3):
            score = float(i)  # higher index = higher score
            db.upsert_memory(_mem(
                f"m{i:02d}", layer="recent_1m",
                compression_score=score, final_score=score / 10,
            ))
        result = comp._compress("recent_1m", TODAY, new_arrivals=5)
        remaining = db.get_memories_by_layer("recent_1m")
        assert len(remaining) == cap
        # The 3 lowest-scored entries (m00, m01, m02) should be gone
        ids = {m.id for m in remaining}
        assert "m00" not in ids
        assert "m01" not in ids
        assert "m02" not in ids

    def test_protected_entries_always_kept(self, db, comp):
        cap = LAYER_CAPS["recent_1m"]
        for i in range(cap + 2):
            db.upsert_memory(_mem(f"m{i:02d}", layer="recent_1m",
                                  compression_score=float(i)))
        # Add 3 protected entries (low score, would normally be eliminated)
        for j in range(3):
            db.upsert_memory(_mem(f"prot{j}", layer="recent_1m",
                                  protected=True, compression_score=0.0))

        result = comp._compress("recent_1m", TODAY, new_arrivals=5)
        # All protected must survive
        for j in range(3):
            assert db.get_memory(f"prot{j}") is not None

    def test_pending_review_for_high_refs_eliminated(self, db, comp):
        cap = LAYER_CAPS["recent_1m"]
        for i in range(cap):
            db.upsert_memory(_mem(f"keep{i:02d}", layer="recent_1m",
                                  compression_score=10.0))
        # This entry would be eliminated but has refs≥3 and intensity≥3
        db.upsert_memory(_mem("borderline", layer="recent_1m",
                              refs=5, intensity=4, compression_score=0.0))

        comp._compress("recent_1m", TODAY, new_arrivals=3)

        m = db.get_memory("borderline")
        assert m is not None
        assert m.pending_review is True

    def test_low_refs_entry_deleted_when_eliminated(self, db, comp):
        cap = LAYER_CAPS["recent_1m"]
        for i in range(cap):
            db.upsert_memory(_mem(f"keep{i:02d}", layer="recent_1m",
                                  compression_score=10.0))
        db.upsert_memory(_mem("goner", layer="recent_1m",
                              refs=0, intensity=1, compression_score=0.0))

        comp._compress("recent_1m", TODAY, new_arrivals=3)

        assert db.get_memory("goner") is None


# ---------------------------------------------------------------------------
# Same-source merge
# ---------------------------------------------------------------------------

class TestMergeSameSource:
    def test_merges_entries_with_same_linked_from(self, db, comp):
        db.upsert_memory(_mem("source", layer="recent_7d"))
        db.upsert_memory(_mem("dup1", layer="recent_1m",
                              linked_from="source", intensity=3,
                              flavor=["tenderness"]))
        db.upsert_memory(_mem("dup2", layer="recent_1m",
                              linked_from="source", intensity=2,
                              flavor=["safe"]))

        removed = comp._merge_same_source("recent_1m")

        assert removed == 1
        remaining = db.get_memories_by_layer("recent_1m")
        assert len(remaining) == 1
        # Winner should have merged flavors
        winner = remaining[0]
        assert "tenderness" in winner.flavor
        assert "safe" in winner.flavor

    def test_winner_has_highest_intensity(self, db, comp):
        db.upsert_memory(_mem("src"))
        db.upsert_memory(_mem("low", layer="recent_1m",
                              linked_from="src", intensity=2))
        db.upsert_memory(_mem("high", layer="recent_1m",
                              linked_from="src", intensity=5))

        comp._merge_same_source("recent_1m")

        remaining = db.get_memories_by_layer("recent_1m")
        assert len(remaining) == 1
        assert remaining[0].id == "high"

    def test_no_merge_when_different_sources(self, db, comp):
        db.upsert_memory(_mem("a", layer="recent_1m", linked_from="src1"))
        db.upsert_memory(_mem("b", layer="recent_1m", linked_from="src2"))

        removed = comp._merge_same_source("recent_1m")

        assert removed == 0
        assert len(db.get_memories_by_layer("recent_1m")) == 2

    def test_no_merge_when_no_linked_from(self, db, comp):
        db.upsert_memory(_mem("a", layer="recent_1m"))
        db.upsert_memory(_mem("b", layer="recent_1m"))

        removed = comp._merge_same_source("recent_1m")
        assert removed == 0


# ---------------------------------------------------------------------------
# Direction-flip bonus
# ---------------------------------------------------------------------------

class TestDirectionFlipBonus:
    def test_flip_adds_three_to_score(self, db, comp):
        # source = positive, child = negative → flip → +3
        source = _mem("src", layer="recent_7d", direction="positive")
        child = _mem("child", layer="recent_1m",
                     direction="negative", linked_from="src",
                     compression_score=1.0)
        db.upsert_memory(source)
        db.upsert_memory(child)

        comp._apply_direction_flip_bonus([child])

        updated = db.get_memory("child")
        assert updated.compression_score == pytest.approx(4.0)

    def test_same_direction_no_bonus(self, db, comp):
        source = _mem("src", layer="recent_7d", direction="positive")
        child = _mem("child", layer="recent_1m",
                     direction="positive", linked_from="src",
                     compression_score=1.0)
        db.upsert_memory(source)
        db.upsert_memory(child)

        comp._apply_direction_flip_bonus([child])

        updated = db.get_memory("child")
        assert updated.compression_score == pytest.approx(1.0)

    def test_no_linked_from_no_bonus(self, db, comp):
        child = _mem("child", layer="recent_1m", compression_score=1.0)
        db.upsert_memory(child)
        comp._apply_direction_flip_bonus([child])
        assert db.get_memory("child").compression_score == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Decay
# ---------------------------------------------------------------------------

class TestDecay:
    def test_decay_lowers_score_for_old_entry(self, db, comp):
        old_date = _dated(60)
        m = _mem("old", date_str=old_date, final_score=0.9,
                 compression_score=0.9)
        db.upsert_memory(m)

        comp._apply_decay(TODAY)

        updated = db.get_memory("old")
        # Score should have decreased due to time decay
        assert updated.final_score < 0.9

    def test_decay_skips_protected(self, db, comp):
        m = _mem("prot", date_str=_dated(60),
                 protected=True, final_score=0.9, compression_score=0.9)
        db.upsert_memory(m)

        comp._apply_decay(TODAY)

        updated = db.get_memory("prot")
        assert updated.final_score == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# L2 promotion
# ---------------------------------------------------------------------------

class TestL2Promotion:
    def test_promotes_eligible_l1(self, db, comp):
        old_date = _dated(35)
        m = _mem("eligible", tier="L1", refs=5, intensity=4,
                 date_str=old_date)
        db.upsert_memory(m)

        count = comp._check_l2_promotion(TODAY)

        assert count == 1
        updated = db.get_memory("eligible")
        assert updated.tier == "L2"
        assert updated.protected is True

    def test_does_not_promote_too_young(self, db, comp):
        m = _mem("young", tier="L1", refs=10, intensity=5,
                 date_str=_dated(10))
        db.upsert_memory(m)

        count = comp._check_l2_promotion(TODAY)
        assert count == 0
        assert db.get_memory("young").tier == "L1"

    def test_does_not_promote_low_refs(self, db, comp):
        m = _mem("lowrefs", tier="L1", refs=2, intensity=5,
                 date_str=_dated(40))
        db.upsert_memory(m)
        assert comp._check_l2_promotion(TODAY) == 0

    def test_does_not_promote_l2_entries(self, db, comp):
        m = _mem("already_l2", tier="L2", refs=10, intensity=5,
                 date_str=_dated(40))
        db.upsert_memory(m)
        assert comp._check_l2_promotion(TODAY) == 0


# ---------------------------------------------------------------------------
# Expired entries (> 6 months)
# ---------------------------------------------------------------------------

class TestHandleExpired:
    def test_promotes_protected_expired(self, db, comp):
        m = _mem("prot_old", layer="early", protected=True,
                 date_str=_dated(200))
        db.upsert_memory(m)

        promoted, deleted = comp._handle_expired(TODAY)

        assert promoted == 1
        assert deleted == 0
        updated = db.get_memory("prot_old")
        assert updated.layer == "permanent"

    def test_promotes_high_quality_expired(self, db, comp):
        m = _mem("quality", layer="early", refs=6, intensity=4,
                 date_str=_dated(200))
        db.upsert_memory(m)

        promoted, deleted = comp._handle_expired(TODAY)

        assert promoted == 1
        assert db.get_memory("quality").layer == "permanent"

    def test_deletes_low_quality_expired(self, db, comp):
        m = _mem("junk", layer="early", refs=0, intensity=1,
                 date_str=_dated(200))
        db.upsert_memory(m)

        promoted, deleted = comp._handle_expired(TODAY)

        assert deleted == 1
        assert db.get_memory("junk") is None

    def test_does_not_touch_recent_early_entries(self, db, comp):
        m = _mem("recent_early", layer="early", date_str=_dated(100))
        db.upsert_memory(m)

        promoted, deleted = comp._handle_expired(TODAY)

        assert promoted == 0
        assert deleted == 0
        assert db.get_memory("recent_early") is not None


# ---------------------------------------------------------------------------
# Full schedule integration
# ---------------------------------------------------------------------------

class TestRunDaily:
    def test_daily_slides_and_compresses(self, db, comp):
        # One entry old enough to slide, one fresh
        db.upsert_memory(_mem("old", layer="recent_7d", date_str=_dated(10)))
        db.upsert_memory(_mem("fresh", layer="recent_7d", date_str=TODAY))

        results = comp.run_daily(TODAY)

        assert results[0].slid == 1
        assert db.get_memory("old").layer == "recent_1m"
        assert db.get_memory("fresh").layer == "recent_7d"

    def test_daily_skips_when_nothing_slides(self, db, comp):
        db.upsert_memory(_mem("fresh", layer="recent_7d", date_str=TODAY))

        results = comp.run_daily(TODAY)
        assert results[0].skipped is True


class TestRunWeekly:
    def test_weekly_slides_1m_to_2m(self, db, comp):
        db.upsert_memory(_mem("old_1m", layer="recent_1m", date_str=_dated(35)))
        db.upsert_memory(_mem("fresh_1m", layer="recent_1m", date_str=_dated(10)))

        comp.run_weekly(TODAY)

        assert db.get_memory("old_1m").layer == "recent_2m"
        assert db.get_memory("fresh_1m").layer == "recent_1m"


class TestRunMonthly:
    def test_monthly_slides_through_layers(self, db, comp):
        db.upsert_memory(_mem("old_2m", layer="recent_2m", date_str=_dated(65)))
        db.upsert_memory(_mem("old_ml", layer="month_label", date_str=_dated(95)))

        comp.run_monthly(TODAY)

        assert db.get_memory("old_2m").layer == "month_label"
        assert db.get_memory("old_ml").layer == "early"
