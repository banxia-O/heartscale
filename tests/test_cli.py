"""CLI tests — use Click's CliRunner, mock all heavy work."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import pytest
from click.testing import CliRunner

from heartscale.cli import main


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def config_file(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"""\
diary:
  dir: "{tmp_path / 'diary'}"
  extensions: [".md"]
output:
  heart_md: "{tmp_path / 'HEART.md'}"
  limbic_jsonl: "{tmp_path / 'limbic.jsonl'}"
  backup_dir: "{tmp_path / 'backups'}"
  backup_keep: 4
database:
  path: "{tmp_path / 'heartscale.db'}"
language: "zh"
embedding:
  provider: "openai"
  model: "text-embedding-3-small"
  api_key_env: "TEST_EMBED_KEY"
  base_url: ""
  dimension: 1024
judge:
  provider: "openai"
  model: "gpt-4o-mini"
  api_key_env: "TEST_JUDGE_KEY"
  base_url: ""
schedule:
  daily_time: "23:30"
  weekly_day: "sunday"
  monthly_day: 1
""",
        encoding="utf-8",
    )
    (tmp_path / "diary").mkdir()
    return cfg, tmp_path


def _set_keys(monkeypatch):
    monkeypatch.setenv("TEST_EMBED_KEY", "sk-fake")
    monkeypatch.setenv("TEST_JUDGE_KEY", "sk-fake")


# ---------------------------------------------------------------------------
# seed command
# ---------------------------------------------------------------------------

class TestSeedCommand:
    def test_seed_imports_example_jsonl(self, runner, config_file, monkeypatch):
        _set_keys(monkeypatch)
        cfg_path, tmp = config_file
        seed_src = Path("seeds/example.jsonl")

        result = runner.invoke(main, ["--config", str(cfg_path), "seed"])

        assert result.exit_code == 0, result.output
        assert "Imported" in result.output
        assert "13" in result.output   # 13 seed memories

    def test_seed_missing_file_exits_nonzero(self, runner, config_file, monkeypatch):
        _set_keys(monkeypatch)
        cfg_path, tmp = config_file
        result = runner.invoke(
            main, ["--config", str(cfg_path), "seed",
                   "--limbic", "/nonexistent/path.jsonl"]
        )
        assert result.exit_code != 0

    def test_seed_exports_limbic_jsonl(self, runner, config_file, monkeypatch):
        _set_keys(monkeypatch)
        cfg_path, tmp = config_file
        runner.invoke(main, ["--config", str(cfg_path), "seed"])
        assert (tmp / "limbic.jsonl").exists()


# ---------------------------------------------------------------------------
# extract command
# ---------------------------------------------------------------------------

class TestExtractCommand:
    def test_extract_specific_file(self, runner, config_file, monkeypatch):
        _set_keys(monkeypatch)
        cfg_path, tmp = config_file

        diary = tmp / "diary" / "2026-05-08.md"
        diary.write_text("今天发生了很多事。", encoding="utf-8")

        with patch("heartscale.cli.extract_diary") as mock_extract, \
             patch("heartscale.cli._make_judge") as mock_judge:
            mock_extract.return_value = [MagicMock(), MagicMock()]
            mock_judge.return_value = MagicMock()

            result = runner.invoke(
                main, ["--config", str(cfg_path), "extract",
                       "--diary", str(diary)]
            )

        assert result.exit_code == 0, result.output
        assert "Extracted 2" in result.output

    def test_extract_missing_diary_exits(self, runner, config_file, monkeypatch):
        _set_keys(monkeypatch)
        cfg_path, _ = config_file
        result = runner.invoke(
            main, ["--config", str(cfg_path), "extract",
                   "--diary", "/nonexistent.md"]
        )
        assert result.exit_code != 0

    def test_extract_skips_already_processed(self, runner, config_file, monkeypatch):
        _set_keys(monkeypatch)
        cfg_path, tmp = config_file

        diary = tmp / "diary" / "2026-05-08.md"
        diary.write_text("内容", encoding="utf-8")

        # Simulate: diary already in DB
        from heartscale.db import Database
        from heartscale.config import Config
        cfg = Config.load(cfg_path)
        with Database(cfg.database.path) as db:
            from heartscale.models import Memory
            m = Memory(
                id="mem_0508_01", date="2026-05-08", layer="recent_7d",
                direction="positive", flavor=["tenderness"], intensity=3,
                summary="already done", tier="L1",
                linked_diary="2026-05-08.md",
            )
            db.upsert_memory(m)

        with patch("heartscale.cli._make_judge", return_value=MagicMock()):
            result = runner.invoke(
                main, ["--config", str(cfg_path), "extract",
                       "--diary", str(diary)]
            )

        assert result.exit_code == 0
        assert "already processed" in result.output

    def test_extract_force_reprocesses(self, runner, config_file, monkeypatch):
        _set_keys(monkeypatch)
        cfg_path, tmp = config_file

        diary = tmp / "diary" / "2026-05-08.md"
        diary.write_text("内容", encoding="utf-8")

        with patch("heartscale.cli.extract_diary") as mock_extract, \
             patch("heartscale.cli._make_judge", return_value=MagicMock()):
            mock_extract.return_value = []
            result = runner.invoke(
                main, ["--config", str(cfg_path), "extract",
                       "--diary", str(diary), "--force"]
            )

        assert result.exit_code == 0
        mock_extract.assert_called_once()


# ---------------------------------------------------------------------------
# compress command
# ---------------------------------------------------------------------------

class TestCompressCommand:
    def _run_compress(self, runner, cfg_path, mode, monkeypatch):
        _set_keys(monkeypatch)
        job_fn = f"heartscale.scheduler.run_{mode}"
        with patch(job_fn) as mock_job, \
             patch("heartscale.cli._make_judge", return_value=MagicMock()):
            mock_job.return_value = {"date": "2026-05-11"}
            result = runner.invoke(
                main, ["--config", str(cfg_path), "compress", f"--{mode}"]
            )
        return result, mock_job

    def test_daily_compress(self, runner, config_file, monkeypatch):
        cfg_path, _ = config_file
        result, mock = self._run_compress(runner, cfg_path, "daily", monkeypatch)
        assert result.exit_code == 0, result.output
        mock.assert_called_once()

    def test_weekly_compress(self, runner, config_file, monkeypatch):
        cfg_path, _ = config_file
        result, mock = self._run_compress(runner, cfg_path, "weekly", monkeypatch)
        assert result.exit_code == 0
        mock.assert_called_once()

    def test_monthly_compress(self, runner, config_file, monkeypatch):
        cfg_path, _ = config_file
        result, mock = self._run_compress(runner, cfg_path, "monthly", monkeypatch)
        assert result.exit_code == 0
        mock.assert_called_once()

    def test_no_mode_shows_error(self, runner, config_file, monkeypatch):
        _set_keys(monkeypatch)
        cfg_path, _ = config_file
        with patch("heartscale.cli._make_judge", return_value=MagicMock()):
            result = runner.invoke(main, ["--config", str(cfg_path), "compress"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# render command
# ---------------------------------------------------------------------------

class TestRenderCommand:
    def test_render_all_sections(self, runner, config_file, monkeypatch):
        _set_keys(monkeypatch)
        cfg_path, tmp = config_file
        with patch("heartscale.cli.render_heart_md") as mock_render, \
             patch("heartscale.cli._make_judge", return_value=MagicMock()):
            mock_render.return_value = "# 心境日志\n\n内容"
            result = runner.invoke(main, ["--config", str(cfg_path), "render"])

        assert result.exit_code == 0, result.output
        assert "HEART.md rendered" in result.output
        mock_render.assert_called_once_with(
            ANY, ANY, ANY, sections=None
        )

    def test_render_specific_sections(self, runner, config_file, monkeypatch):
        _set_keys(monkeypatch)
        cfg_path, _ = config_file
        with patch("heartscale.cli.render_heart_md") as mock_render, \
             patch("heartscale.cli._make_judge", return_value=MagicMock()):
            mock_render.return_value = "content"
            runner.invoke(
                main, ["--config", str(cfg_path), "render",
                       "--sections", "recent_7d,recent_1m"]
            )

        call_kwargs = mock_render.call_args
        assert call_kwargs.kwargs["sections"] == ["recent_7d", "recent_1m"]

    def test_render_warns_when_over_2000_chars(self, runner, config_file, monkeypatch):
        _set_keys(monkeypatch)
        cfg_path, _ = config_file
        with patch("heartscale.cli.render_heart_md") as mock_render, \
             patch("heartscale.cli._make_judge", return_value=MagicMock()):
            mock_render.return_value = "x" * 2100
            result = runner.invoke(main, ["--config", str(cfg_path), "render"])

        assert "⚠" in result.output
        assert "2100" in result.output


# ---------------------------------------------------------------------------
# Missing config
# ---------------------------------------------------------------------------

def test_missing_config_exits_with_message(runner):
    result = runner.invoke(
        main, ["--config", "/nonexistent/config.yaml", "render"]
    )
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Scheduler (just test it can be imported and starts cleanly)
# ---------------------------------------------------------------------------

def test_scheduler_job_functions_exist():
    from heartscale.scheduler import run_daily, run_weekly, run_monthly
    assert callable(run_daily)
    assert callable(run_weekly)
    assert callable(run_monthly)
