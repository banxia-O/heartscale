"""Tests for render.py — all LLM calls mocked."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from heartscale.db import Database
from heartscale.models import Memory, RelationshipVector
from heartscale.render import (
    SECTION_ORDER,
    _assemble,
    _backup,
    _month_label_heading,
    _parse_sections,
    _render_section,
    render_heart_md,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TODAY = "2026-05-11"


@pytest.fixture
def db(tmp_path):
    with Database(tmp_path / "test.db") as database:
        yield database


@pytest.fixture
def cfg(tmp_path):
    c = MagicMock()
    c.language = "zh"
    c.output.heart_md = tmp_path / "HEART.md"
    c.output.backup_dir = tmp_path / "backups"
    c.output.backup_keep = 4
    return c


def _make_judge(reply: str = "渲染后的自然语言段落") -> MagicMock:
    j = MagicMock()
    j.ask.return_value = reply
    return j


def _mem(mid: str, layer: str, date_str: str = TODAY) -> Memory:
    return Memory(
        id=mid, date=date_str, layer=layer,
        direction="positive", flavor=["tenderness"],
        intensity=3, summary=f"summary of {mid}",
        tier="L1",
    )


# ---------------------------------------------------------------------------
# _parse_sections
# ---------------------------------------------------------------------------

class TestParseSections:
    def test_parses_zh_headings(self):
        content = (
            "# 心境日志\n\n"
            "## 近 7 天\n七天内容\n\n"
            "## 本月\n本月内容\n\n"
            "## 上月\n上月内容\n"
        )
        sections = _parse_sections(content)
        assert sections["recent_7d"] == "七天内容"
        assert sections["recent_1m"] == "本月内容"
        assert sections["recent_2m"] == "上月内容"

    def test_parses_en_headings(self):
        content = (
            "# Emotional Journal\n\n"
            "## Last 7 Days\nweek content\n\n"
            "## This Month\nmonth content\n"
        )
        sections = _parse_sections(content)
        assert sections["recent_7d"] == "week content"
        assert sections["recent_1m"] == "month content"

    def test_parses_month_label(self):
        content = "## Feb 2026\nsome old memories\n"
        sections = _parse_sections(content)
        assert sections["month_label"] == "some old memories"

    def test_parses_relationship_section(self):
        content = "## 当前心境\n心境内容\n"
        sections = _parse_sections(content)
        assert sections["relationship"] == "心境内容"

    def test_empty_content_returns_empty_dict(self):
        assert _parse_sections("") == {}

    def test_no_sections_returns_empty(self):
        assert _parse_sections("# 心境日志\n\n只有标题") == {}


# ---------------------------------------------------------------------------
# _backup
# ---------------------------------------------------------------------------

class TestBackup:
    def test_creates_backup_file(self, tmp_path):
        heart = tmp_path / "HEART.md"
        heart.write_text("original content", encoding="utf-8")
        backup_dir = tmp_path / "backups"

        _backup(heart, backup_dir, keep=4)

        backups = list(backup_dir.glob("HEART.md.bak.*"))
        assert len(backups) == 1

    def test_keeps_only_n_backups(self, tmp_path):
        heart = tmp_path / "HEART.md"
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Create 5 existing backups
        for i in range(5):
            (backup_dir / f"HEART.md.bak.2026010{i}000000").write_text(f"v{i}")

        heart.write_text("new content", encoding="utf-8")
        _backup(heart, backup_dir, keep=4)

        backups = list(backup_dir.glob("HEART.md.bak.*"))
        assert len(backups) == 4

    def test_no_backup_when_file_missing(self, tmp_path):
        heart = tmp_path / "HEART.md"  # does not exist
        backup_dir = tmp_path / "backups"

        _backup(heart, backup_dir, keep=4)  # should not raise
        assert not backup_dir.exists()


# ---------------------------------------------------------------------------
# _render_section
# ---------------------------------------------------------------------------

class TestRenderSection:
    def test_calls_judge_and_returns_content(self, db, cfg):
        db.upsert_memory(_mem("m1", "recent_7d"))
        judge = _make_judge("七天的记忆段落")

        result = _render_section("recent_7d", db, judge, cfg)
        assert result == "七天的记忆段落"
        judge.ask.assert_called_once()

    def test_returns_empty_when_no_memories(self, db, cfg):
        judge = _make_judge("不应该被调用")
        result = _render_section("recent_7d", db, judge, cfg)
        assert result == ""
        judge.ask.assert_not_called()

    def test_zh_prompt_contains_no_metadata_instructions(self, db, cfg):
        db.upsert_memory(_mem("m1", "recent_1m"))
        judge = _make_judge("内容")
        _render_section("recent_1m", db, judge, cfg)

        system_prompt = judge.ask.call_args.kwargs["system"]
        # Prompt must explicitly forbid metadata words
        assert "intensity" in system_prompt
        assert "flavor" in system_prompt

    def test_en_prompt_used_when_language_en(self, db, cfg):
        cfg.language = "en"
        db.upsert_memory(_mem("m1", "recent_7d"))
        judge = _make_judge("week memories")
        _render_section("recent_7d", db, judge, cfg)

        system_prompt = judge.ask.call_args.kwargs["system"]
        assert "English" in system_prompt or "natural language" in system_prompt.lower()

    def test_relationship_section_renders_vector(self, db, cfg):
        rv = RelationshipVector(closeness=0.8, trust=0.9,
                                dependency=0.5, tension=0.1, missing=0.6)
        db.upsert_relationship_vector(rv)
        judge = _make_judge("最近感觉很亲密")

        result = _render_section("relationship", db, judge, cfg)

        assert "0.80" in result   # closeness
        assert "0.90" in result   # trust
        assert "最近感觉很亲密" in result


# ---------------------------------------------------------------------------
# _assemble
# ---------------------------------------------------------------------------

class TestAssemble:
    def test_title_is_present(self, db):
        sections = {k: f"{k} content" for k in SECTION_ORDER}
        result = _assemble(sections, db, "zh")
        assert "# 心境日志" in result

    def test_en_title_in_english_mode(self, db):
        sections = {k: f"{k} content" for k in SECTION_ORDER}
        result = _assemble(sections, db, "en")
        assert "# Emotional Journal" in result

    def test_empty_sections_omitted(self, db):
        sections = {"recent_7d": "七天内容", "recent_1m": ""}
        result = _assemble(sections, db, "zh")
        assert "近 7 天" in result
        assert "本月" not in result

    def test_month_label_heading_uses_dynamic_month(self, db):
        # Add a month_label entry dated Feb 2026
        m = _mem("ml1", "month_label", date_str="2026-02-14")
        db.upsert_memory(m)
        sections = {"month_label": "old memories"}
        result = _assemble(sections, db, "zh")
        assert "Feb 2026" in result

    def test_no_metadata_in_headings(self, db):
        sections = {k: f"content {k}" for k in SECTION_ORDER}
        result = _assemble(sections, db, "zh")
        for bad in ("intensity", "flavor", "direction", "positive", "negative"):
            assert bad not in result


# ---------------------------------------------------------------------------
# render_heart_md integration
# ---------------------------------------------------------------------------

class TestRenderHeartMd:
    def test_creates_heart_md_file(self, db, cfg, tmp_path):
        db.upsert_memory(_mem("m1", "recent_7d"))
        judge = _make_judge("渲染内容")

        render_heart_md(db, judge, cfg)

        assert cfg.output.heart_md.exists()

    def test_renders_only_specified_sections(self, db, cfg):
        db.upsert_memory(_mem("m7d", "recent_7d"))
        db.upsert_memory(_mem("m1m", "recent_1m"))

        # Pre-populate existing HEART.md with a recent_1m section
        cfg.output.heart_md.parent.mkdir(parents=True, exist_ok=True)
        cfg.output.heart_md.write_text(
            "# 心境日志\n\n## 本月\n原有的本月内容\n",
            encoding="utf-8",
        )

        judge = _make_judge("新的七天内容")
        render_heart_md(db, judge, cfg, sections=["recent_7d"])

        result = cfg.output.heart_md.read_text(encoding="utf-8")
        assert "新的七天内容" in result
        assert "原有的本月内容" in result   # unchanged section preserved

    def test_full_render_when_no_existing_file(self, db, cfg):
        db.upsert_memory(_mem("m1", "recent_7d"))
        call_count = 0

        def counting_ask(**kwargs):
            nonlocal call_count
            call_count += 1
            return "渲染内容"

        judge = MagicMock()
        judge.ask.side_effect = counting_ask

        render_heart_md(db, judge, cfg)
        # Should have called LLM for recent_7d + relationship (at minimum)
        assert call_count >= 1

    def test_backup_created_on_render(self, db, cfg):
        cfg.output.heart_md.parent.mkdir(parents=True, exist_ok=True)
        cfg.output.heart_md.write_text("original", encoding="utf-8")

        db.upsert_memory(_mem("m1", "recent_7d"))
        judge = _make_judge("new content")

        render_heart_md(db, judge, cfg)

        backups = list(cfg.output.backup_dir.glob("HEART.md.bak.*"))
        assert len(backups) == 1

    def test_output_contains_no_metadata_tags(self, db, cfg):
        db.upsert_memory(_mem("m1", "recent_7d"))
        judge = _make_judge("一段干净的自然语言，没有任何元数据")

        content = render_heart_md(db, judge, cfg)

        for forbidden in ("intensity:", "flavor:", "direction:", "tier:", "refs:"):
            assert forbidden not in content

    def test_returns_rendered_string(self, db, cfg):
        db.upsert_memory(_mem("m1", "recent_7d"))
        judge = _make_judge("渲染结果")

        result = render_heart_md(db, judge, cfg)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_partial_update_preserves_unchanged_sections(self, db, cfg):
        # Write an existing HEART.md with multiple sections
        existing = (
            "# 心境日志\n\n"
            "## 近 7 天\n旧的七天内容\n\n"
            "## 本月\n旧的本月内容\n\n"
            "## 上月\n旧的上月内容\n"
        )
        cfg.output.heart_md.parent.mkdir(parents=True, exist_ok=True)
        cfg.output.heart_md.write_text(existing, encoding="utf-8")
        db.upsert_memory(_mem("m7d", "recent_7d"))

        judge = _make_judge("新的七天内容")
        render_heart_md(db, judge, cfg, sections=["recent_7d"])

        result = cfg.output.heart_md.read_text(encoding="utf-8")
        assert "新的七天内容" in result
        assert "旧的本月内容" in result
        assert "旧的上月内容" in result

    def test_backup_respects_keep_limit(self, db, cfg):
        cfg.output.backup_keep = 2
        cfg.output.heart_md.parent.mkdir(parents=True, exist_ok=True)
        cfg.output.backup_dir.mkdir(parents=True, exist_ok=True)

        # Create existing backups
        for i in range(4):
            (cfg.output.backup_dir / f"HEART.md.bak.2026010{i}000000").write_text(f"v{i}")

        cfg.output.heart_md.write_text("old", encoding="utf-8")
        db.upsert_memory(_mem("m1", "recent_7d"))
        judge = _make_judge("new")
        render_heart_md(db, judge, cfg)

        backups = list(cfg.output.backup_dir.glob("HEART.md.bak.*"))
        assert len(backups) == 2
