"""Tests for config loading."""

import os
import textwrap
from pathlib import Path

import pytest

from heartscale.config import Config


@pytest.fixture
def config_file(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(textwrap.dedent("""\
        diary:
          dir: "~/Documents/diary"
          extensions: [".md", ".txt"]
        output:
          heart_md: "~/.hermes/HEART.md"
          limbic_jsonl: "~/.hermes/limbic.jsonl"
          backup_dir: "~/.hermes/backups"
          backup_keep: 4
        database:
          path: "~/.hermes/heartscale.db"
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
          daily_time: "00:05"
          weekly_day: "sunday"
          monthly_day: 1
    """), encoding="utf-8")
    return cfg


def test_load_parses_all_sections(config_file):
    cfg = Config.load(config_file)

    assert cfg.language == "zh"
    assert cfg.output.backup_keep == 4
    assert cfg.embedding.model == "text-embedding-3-small"
    assert cfg.embedding.dimension == 1024
    assert cfg.judge.model == "gpt-4o-mini"
    assert cfg.schedule.daily_time == "00:05"
    assert cfg.schedule.weekly_day == "sunday"
    assert cfg.schedule.monthly_day == 1
    assert ".md" in cfg.diary.extensions
    assert ".txt" in cfg.diary.extensions


def test_paths_are_expanded(config_file):
    cfg = Config.load(config_file)
    # ~ should be expanded to the actual home directory
    assert "~" not in str(cfg.output.heart_md)
    assert "~" not in str(cfg.database.path)
    assert "~" not in str(cfg.diary.dir)


def test_missing_config_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="config.yaml"):
        Config.load(tmp_path / "nonexistent.yaml")


def test_api_key_from_env(config_file, monkeypatch):
    monkeypatch.setenv("TEST_EMBED_KEY", "sk-test-123")
    cfg = Config.load(config_file)
    assert cfg.embedding.api_key() == "sk-test-123"


def test_api_key_missing_raises(config_file, monkeypatch):
    monkeypatch.delenv("TEST_EMBED_KEY", raising=False)
    cfg = Config.load(config_file)
    with pytest.raises(EnvironmentError, match="TEST_EMBED_KEY"):
        cfg.embedding.api_key()


def test_example_config_is_parseable():
    """The shipped config.yaml.example must load without errors."""
    example = Path(__file__).parent.parent / "config.yaml.example"
    cfg = Config.load(example)
    assert cfg.language in ("zh", "en")
    assert cfg.embedding.provider == "openai"
    assert cfg.judge.provider == "openai"
