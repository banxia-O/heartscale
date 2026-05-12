"""Configuration loading and validation for heartscale."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class DiaryConfig:
    dir: Path
    extensions: list[str] = field(default_factory=lambda: [".md", ".txt"])


@dataclass
class OutputConfig:
    heart_md: Path
    limbic_jsonl: Path
    backup_dir: Path
    backup_keep: int = 4


@dataclass
class DatabaseConfig:
    path: Path


@dataclass
class ProviderConfig:
    provider: str
    model: str
    api_key_env: str
    base_url: str = ""
    dimension: Optional[int] = None  # only used for embedding

    def api_key(self) -> str:
        """Read API key from environment variable."""
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise EnvironmentError(
                f"API key not found. Set the environment variable: {self.api_key_env}\n"
                f"Example: export {self.api_key_env}=your_key_here"
            )
        return key

    def resolved_base_url(self) -> Optional[str]:
        return self.base_url if self.base_url else None


@dataclass
class ScheduleConfig:
    daily_time: str = "00:05"
    weekly_day: str = "sunday"
    monthly_day: int = 1


@dataclass
class Config:
    diary: DiaryConfig
    output: OutputConfig
    database: DatabaseConfig
    embedding: ProviderConfig
    judge: ProviderConfig
    schedule: ScheduleConfig
    language: str = "zh"

    @classmethod
    def load(cls, path: str | Path = "config.yaml") -> "Config":
        config_path = Path(path).expanduser().resolve()
        if not config_path.exists():
            raise FileNotFoundError(
                f"Config file not found: {config_path}\n"
                f"Copy config.yaml.example to config.yaml and fill in your settings."
            )

        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        return cls._from_dict(raw)

    @classmethod
    def _from_dict(cls, raw: dict) -> "Config":
        diary_raw = raw.get("diary", {})
        diary = DiaryConfig(
            dir=_expand(diary_raw.get("dir", "~/Documents/diary")),
            extensions=diary_raw.get("extensions", [".md", ".txt"]),
        )

        out_raw = raw.get("output", {})
        output = OutputConfig(
            heart_md=_expand(out_raw.get("heart_md", "~/.hermes/HEART.md")),
            limbic_jsonl=_expand(out_raw.get("limbic_jsonl", "~/.hermes/limbic.jsonl")),
            backup_dir=_expand(out_raw.get("backup_dir", "~/.hermes/backups")),
            backup_keep=int(out_raw.get("backup_keep", 4)),
        )

        db_raw = raw.get("database", {})
        database = DatabaseConfig(
            path=_expand(db_raw.get("path", "~/.hermes/heartscale.db")),
        )

        emb_raw = raw.get("embedding", {})
        embedding = ProviderConfig(
            provider=emb_raw.get("provider", "openai"),
            model=emb_raw.get("model", "text-embedding-3-small"),
            api_key_env=emb_raw.get("api_key_env", "HEARTSCALE_EMBEDDING_KEY"),
            base_url=emb_raw.get("base_url", ""),
            dimension=int(emb_raw.get("dimension", 1024)),
        )

        judge_raw = raw.get("judge", {})
        judge = ProviderConfig(
            provider=judge_raw.get("provider", "openai"),
            model=judge_raw.get("model", "gpt-4o-mini"),
            api_key_env=judge_raw.get("api_key_env", "HEARTSCALE_JUDGE_KEY"),
            base_url=judge_raw.get("base_url", ""),
        )

        sched_raw = raw.get("schedule", {})
        schedule = ScheduleConfig(
            daily_time=sched_raw.get("daily_time", "00:05"),
            weekly_day=sched_raw.get("weekly_day", "sunday"),
            monthly_day=int(sched_raw.get("monthly_day", 1)),
        )

        return cls(
            diary=diary,
            output=output,
            database=database,
            embedding=embedding,
            judge=judge,
            schedule=schedule,
            language=str(raw.get("language", "zh")),
        )


def _expand(path_str: str) -> Path:
    return Path(path_str).expanduser().resolve()
