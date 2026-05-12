"""Tests for extract.py and scoring.py — all LLM calls are mocked."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from heartscale.db import Database
from heartscale.extract import (
    _build_prompt,
    _call_judge,
    _generate_id,
    _parse_date_from_filename,
    _validate_direction,
    _validate_flavor,
    extract_diary,
)
from heartscale.scoring import (
    compute_compression_score,
    compute_final_score,
    compute_time_decay,
    initial_scores,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    with Database(tmp_path / "test.db") as database:
        yield database


@pytest.fixture
def cfg(tmp_path):
    """Minimal config-like object with the fields extract_diary needs."""
    c = MagicMock()
    c.language = "zh"
    c.output.limbic_jsonl = tmp_path / "limbic.jsonl"
    return c


def _make_judge(events: list[dict]) -> MagicMock:
    """Build a mock JudgeProvider that returns the given events JSON."""
    judge = MagicMock()
    judge.ask.return_value = json.dumps({"events": events})
    return judge


def _sample_diary(tmp_path: Path, name="2026-05-08.md", content="今天和她聊了很久。") -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# scoring.py
# ---------------------------------------------------------------------------

class TestScoring:
    def test_time_decay_today_is_one(self):
        assert compute_time_decay("2026-05-08", "2026-05-08") == pytest.approx(1.0)

    def test_time_decay_90_days_is_half(self):
        decay = compute_time_decay("2026-02-07", "2026-05-08")
        assert decay == pytest.approx(0.5, abs=0.02)

    def test_time_decay_future_clamped_to_one(self):
        # future date should not go above 1
        assert compute_time_decay("2026-05-09", "2026-05-08") == pytest.approx(1.0)

    def test_final_score_weights(self):
        # all components = 1 → score = 1.0
        assert compute_final_score(1.0, 1.0, 1.0, 1.0) == pytest.approx(1.0)
        # only ai_score = 1 → 0.4
        assert compute_final_score(1.0, 0.0, 0.0, 0.0) == pytest.approx(0.4)

    def test_compression_score_negative_bonus(self):
        fs = compute_final_score(0.6, 0.0, 0.0, 1.0)
        cs_pos = compute_compression_score(fs, "positive", [], 0)
        cs_neg = compute_compression_score(fs, "negative", [], 0)
        assert cs_neg - cs_pos == pytest.approx(2.0)

    def test_compression_score_conflict_bonus(self):
        fs = 0.5
        without = compute_compression_score(fs, "positive", ["tenderness"], 0)
        with_conflict = compute_compression_score(fs, "positive", ["conflict"], 0)
        assert with_conflict - without == pytest.approx(1.0)

    def test_compression_score_refs_bonus(self):
        fs = 0.5
        cs0 = compute_compression_score(fs, "positive", [], 0)
        cs4 = compute_compression_score(fs, "positive", [], 4)
        assert cs4 - cs0 == pytest.approx(2.0)  # 0.5 × 4

    def test_initial_scores_intensity_five_is_higher(self):
        fs5, cs5 = initial_scores(5, "positive", [], "2026-05-08", "2026-05-08")
        fs1, cs1 = initial_scores(1, "positive", [], "2026-05-08", "2026-05-08")
        assert fs5 > fs1
        assert cs5 > cs1


# ---------------------------------------------------------------------------
# Helper functions in extract.py
# ---------------------------------------------------------------------------

class TestExtractHelpers:
    def test_parse_date_from_filename(self):
        assert _parse_date_from_filename("2026-05-08.md") == "2026-05-08"
        assert _parse_date_from_filename("diary_2026-03-15.txt") == "2026-03-15"
        assert _parse_date_from_filename("notes.md") is None

    def test_generate_id_format(self):
        mid = _generate_id("2026-05-08", 1, set())
        assert mid == "mem_0508_01"

    def test_generate_id_avoids_collision(self):
        existing = {"mem_0508_01", "mem_0508_02"}
        mid = _generate_id("2026-05-08", 1, existing)
        assert mid == "mem_0508_03"

    def test_validate_direction_valid(self):
        for d in ("positive", "negative", "mixed"):
            assert _validate_direction(d) == d

    def test_validate_direction_invalid_defaults(self):
        assert _validate_direction("happy") == "positive"
        assert _validate_direction("") == "positive"

    def test_validate_flavor_filters_unknown(self):
        result = _validate_flavor(["tenderness", "unknown_tag", "attachment"])
        assert result == ["tenderness", "attachment"]

    def test_validate_flavor_non_list_returns_empty(self):
        assert _validate_flavor("tenderness") == []
        assert _validate_flavor(None) == []

    def test_build_prompt_zh(self):
        system, user = _build_prompt("2026-05-08", "日记内容", "zh")
        assert "日记" in system
        assert "2026-05-08" in user
        assert "日记内容" in user

    def test_build_prompt_en(self):
        system, user = _build_prompt("2026-05-08", "diary text", "en")
        assert "diary" in system.lower()
        assert "2026-05-08" in user


class TestCallJudge:
    def test_parses_events(self):
        judge = MagicMock()
        judge.ask.return_value = json.dumps({"events": [
            {"summary": "test", "direction": "positive",
             "flavor": ["tenderness"], "intensity": 3,
             "trigger_keywords": ["test"]}
        ]})
        result = _call_judge("diary text", "2026-05-08", judge, "zh")
        assert len(result) == 1
        assert result[0]["summary"] == "test"

    def test_returns_empty_on_bad_json(self):
        judge = MagicMock()
        judge.ask.return_value = "not json"
        result = _call_judge("text", "2026-05-08", judge, "zh")
        assert result == []

    def test_returns_empty_on_missing_events_key(self):
        judge = MagicMock()
        judge.ask.return_value = json.dumps({"result": []})
        result = _call_judge("text", "2026-05-08", judge, "zh")
        assert result == []

    def test_filters_non_dict_events(self):
        judge = MagicMock()
        judge.ask.return_value = json.dumps({"events": [
            {"summary": "good event", "direction": "positive",
             "flavor": ["safe"], "intensity": 2, "trigger_keywords": []},
            "not a dict",
            42,
        ]})
        result = _call_judge("text", "2026-05-08", judge, "zh")
        assert len(result) == 1


# ---------------------------------------------------------------------------
# extract_diary() integration
# ---------------------------------------------------------------------------

class TestExtractDiary:
    def test_basic_extraction(self, tmp_path, db, cfg):
        diary = _sample_diary(tmp_path)
        judge = _make_judge([
            {"summary": "聊到很晚，很温暖", "direction": "positive",
             "flavor": ["tenderness", "safe"], "intensity": 3,
             "trigger_keywords": ["聊天", "温暖"]},
        ])
        memories = extract_diary(diary, db, judge, cfg)
        assert len(memories) == 1
        m = memories[0]
        assert m.layer == "recent_7d"
        assert m.tier == "L1"
        assert m.direction == "positive"
        assert m.linked_diary == diary.name
        assert m.final_score > 0

    def test_date_from_filename(self, tmp_path, db, cfg):
        diary = _sample_diary(tmp_path, name="2026-05-08.md")
        judge = _make_judge([
            {"summary": "test", "direction": "positive",
             "flavor": ["attachment"], "intensity": 2,
             "trigger_keywords": []},
        ])
        memories = extract_diary(diary, db, judge, cfg)
        assert memories[0].date == "2026-05-08"

    def test_explicit_date_overrides_filename(self, tmp_path, db, cfg):
        diary = _sample_diary(tmp_path, name="notes.md")
        judge = _make_judge([
            {"summary": "test", "direction": "positive",
             "flavor": ["attachment"], "intensity": 2,
             "trigger_keywords": []},
        ])
        memories = extract_diary(diary, db, judge, cfg, diary_date="2026-04-01")
        assert memories[0].date == "2026-04-01"

    def test_cap_at_seven_events(self, tmp_path, db, cfg):
        events = [
            {"summary": f"event {i}", "direction": "positive",
             "flavor": ["tenderness"], "intensity": 2, "trigger_keywords": []}
            for i in range(10)
        ]
        diary = _sample_diary(tmp_path)
        judge = _make_judge(events)
        memories = extract_diary(diary, db, judge, cfg)
        assert len(memories) == 7

    def test_empty_diary_returns_empty(self, tmp_path, db, cfg):
        diary = _sample_diary(tmp_path, content="   \n  ")
        judge = _make_judge([])
        memories = extract_diary(diary, db, judge, cfg)
        assert memories == []
        judge.ask.assert_not_called()  # should not call LLM for empty text

    def test_keywords_registered_as_triggers(self, tmp_path, db, cfg):
        diary = _sample_diary(tmp_path)
        judge = _make_judge([
            {"summary": "熬夜改代码", "direction": "positive",
             "flavor": ["tenderness"], "intensity": 2,
             "trigger_keywords": ["熬夜", "代码"]},
        ])
        memories = extract_diary(diary, db, judge, cfg)
        recalled = db.get_memories_by_keyword("熬夜")
        assert any(m.id == memories[0].id for m in recalled)

    def test_high_intensity_nudges_relationship_vector(self, tmp_path, db, cfg):
        diary = _sample_diary(tmp_path)
        judge = _make_judge([
            {"summary": "非常重要的一天", "direction": "positive",
             "flavor": ["attachment"], "intensity": 5,
             "trigger_keywords": []},
        ])
        rv_before = db.get_relationship_vector()
        extract_diary(diary, db, judge, cfg)
        rv_after = db.get_relationship_vector()
        assert rv_after.closeness > rv_before.closeness  # nudged upward

    def test_low_intensity_does_not_nudge_vector(self, tmp_path, db, cfg):
        diary = _sample_diary(tmp_path)
        judge = _make_judge([
            {"summary": "普通的一天", "direction": "positive",
             "flavor": ["tenderness"], "intensity": 2,
             "trigger_keywords": []},
        ])
        rv_before = db.get_relationship_vector()
        extract_diary(diary, db, judge, cfg)
        rv_after = db.get_relationship_vector()
        assert rv_after.closeness == rv_before.closeness  # unchanged

    def test_limbic_jsonl_written(self, tmp_path, db, cfg):
        diary = _sample_diary(tmp_path)
        judge = _make_judge([
            {"summary": "test event", "direction": "positive",
             "flavor": ["safe"], "intensity": 2, "trigger_keywords": []},
        ])
        extract_diary(diary, db, judge, cfg)
        assert cfg.output.limbic_jsonl.exists()
        lines = cfg.output.limbic_jsonl.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1

    def test_missing_diary_raises(self, tmp_path, db, cfg):
        from heartscale.extract import extract_diary
        judge = _make_judge([])
        with pytest.raises(FileNotFoundError):
            extract_diary(tmp_path / "nonexistent.md", db, judge, cfg)

    def test_persisted_to_db(self, tmp_path, db, cfg):
        diary = _sample_diary(tmp_path)
        judge = _make_judge([
            {"summary": "should be in db", "direction": "mixed",
             "flavor": ["anxiety", "attachment"], "intensity": 4,
             "trigger_keywords": []},
        ])
        memories = extract_diary(diary, db, judge, cfg)
        fetched = db.get_memory(memories[0].id)
        assert fetched is not None
        assert fetched.summary == "should be in db"
