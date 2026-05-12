"""Command-line interface for heartscale.

Entry point: heartscale  (registered in pyproject.toml)

Commands:
  heartscale seed      — import seed memories into the database
  heartscale extract   — extract emotions from diary file(s)
  heartscale compress  — run cascade compression (daily / weekly / monthly)
  heartscale render    — render HEART.md from current memories
  heartscale scheduler — start the automatic scheduler (runs forever)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import click

from heartscale.config import Config
from heartscale.extract import extract_diary
from heartscale.render import render_heart_md


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------

@click.group()
@click.option(
    "--config", "config_path",
    default="config.yaml",
    show_default=True,
    help="Path to your config.yaml file.",
)
@click.pass_context
def main(ctx: click.Context, config_path: str) -> None:
    """heartscale — human-like emotional memory for AI agents."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path


# ---------------------------------------------------------------------------
# seed
# ---------------------------------------------------------------------------

@main.command()
@click.option(
    "--limbic",
    "limbic_path",
    default=None,
    help="Path to a limbic JSONL file to import (default: seeds/example.jsonl).",
)
@click.option(
    "--vector",
    "vector_path",
    default=None,
    help="Path to relationship_vector.json to import (default: seeds/relationship_vector.json.example).",
)
@click.pass_context
def seed(ctx: click.Context, limbic_path: Optional[str], vector_path: Optional[str]) -> None:
    """Import seed memories into the database (first-time setup)."""
    config = _load_config(ctx)
    db = _open_db(config)

    # Import limbic JSONL
    src = Path(limbic_path) if limbic_path else Path("seeds/example.jsonl")
    if not src.exists():
        _die(f"Seed file not found: {src}\nRun from the project root, or pass --limbic PATH.")

    count = db.import_limbic_jsonl(src)
    click.echo(f"✓ Imported {count} memories from {src}")

    # Import relationship vector
    vec_src = Path(vector_path) if vector_path else Path("seeds/relationship_vector.json.example")
    if vec_src.exists():
        _import_vector(db, vec_src)
        click.echo(f"✓ Imported relationship vector from {vec_src}")

    # Sync to limbic.jsonl
    db.export_limbic_jsonl(config.output.limbic_jsonl)
    click.echo(f"✓ Exported to {config.output.limbic_jsonl}")
    db.close()


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------

@main.command()
@click.option(
    "--diary", "diary_path",
    default=None,
    help="Path to a specific diary file. Omit to scan diary_dir from config.",
)
@click.option(
    "--date",
    default=None,
    help="Override diary date (YYYY-MM-DD). Only used with --diary.",
)
@click.option(
    "--force", is_flag=True, default=False,
    help="Re-extract even if this diary file was already processed.",
)
@click.pass_context
def extract(
    ctx: click.Context,
    diary_path: Optional[str],
    date: Optional[str],
    force: bool,
) -> None:
    """Extract emotional memories from diary file(s)."""
    config = _load_config(ctx)
    db = _open_db(config)
    judge = _make_judge(config)

    from heartscale.extract import _parse_date_from_filename

    if diary_path:
        path = Path(diary_path)
        if not path.exists():
            _die(f"Diary file not found: {path}")

        # Check if already processed
        if not force:
            already = {m.linked_diary for m in db.get_all_memories() if m.linked_diary}
            if path.name in already:
                click.echo(f"⚠ {path.name} already processed. Use --force to re-extract.")
                db.close()
                return

        memories = extract_diary(path, db, judge, config, diary_date=date)
        click.echo(f"✓ Extracted {len(memories)} memories from {path.name}")
    else:
        # Scan diary_dir
        from heartscale.scheduler import _extract_recent_diaries
        from datetime import date as _date
        today = date or str(_date.today())
        count = _extract_recent_diaries(config, db, judge, today, days_back=7)
        click.echo(f"✓ Extracted {count} memories from {config.diary.dir}")

    db.close()


# ---------------------------------------------------------------------------
# compress
# ---------------------------------------------------------------------------

@main.command()
@click.option("--daily",   "mode", flag_value="daily",   help="Run daily compression.")
@click.option("--weekly",  "mode", flag_value="weekly",  help="Run weekly compression.")
@click.option("--monthly", "mode", flag_value="monthly", help="Run monthly compression.")
@click.option(
    "--date",
    default=None,
    help="Override today's date (YYYY-MM-DD). Useful for testing.",
)
@click.pass_context
def compress(ctx: click.Context, mode: Optional[str], date: Optional[str]) -> None:
    """Run cascade compression on the limbic memory layer."""
    if not mode:
        click.echo("Specify one of: --daily, --weekly, --monthly")
        raise click.UsageError("No compression mode selected.")

    config = _load_config(ctx)
    db = _open_db(config)
    judge = _make_judge(config)

    from heartscale.scheduler import run_daily, run_weekly, run_monthly
    from datetime import date as _date
    today = date or str(_date.today())

    if mode == "daily":
        result = run_daily(config, db, judge, today)
    elif mode == "weekly":
        result = run_weekly(config, db, judge, today)
    else:
        result = run_monthly(config, db, judge, today)

    click.echo(f"✓ {mode.capitalize()} compression done: {result}")
    db.close()


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------

@main.command()
@click.option(
    "--sections",
    default=None,
    help="Comma-separated sections to re-render, e.g. recent_7d,recent_1m. "
         "Omit to render all sections.",
)
@click.pass_context
def render(ctx: click.Context, sections: Optional[str]) -> None:
    """Render (or update) HEART.md from current limbic memories."""
    config = _load_config(ctx)
    db = _open_db(config)
    judge = _make_judge(config)

    section_list = [s.strip() for s in sections.split(",")] if sections else None
    content = render_heart_md(db, judge, config, sections=section_list)

    char_count = len(content)
    click.echo(f"✓ HEART.md rendered ({char_count} chars) → {config.output.heart_md}")
    if char_count > 2000:
        click.echo(f"⚠ Output is {char_count} chars (recommended ≤ 2000). "
                   "Consider reducing diary entries or quota limits.")
    db.close()


# ---------------------------------------------------------------------------
# scheduler
# ---------------------------------------------------------------------------

@main.command("scheduler")
@click.pass_context
def scheduler_cmd(ctx: click.Context) -> None:
    """Start the automatic scheduler (runs indefinitely, Ctrl+C to stop)."""
    config = _load_config(ctx)
    from heartscale.scheduler import start_scheduler
    start_scheduler(config)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _load_config(ctx: click.Context) -> Config:
    config_path = ctx.obj["config_path"]
    try:
        return Config.load(config_path)
    except FileNotFoundError as e:
        _die(str(e))


def _open_db(config: Config):
    from heartscale.db import Database
    return Database(config.database.path, embedding_dim=config.embedding.dimension or 1024)


def _make_judge(config: Config):
    from heartscale.providers.factory import make_judge_provider
    return make_judge_provider(config.judge)


def _import_vector(db, path: Path) -> None:
    from heartscale.models import RelationshipVector
    raw = json.loads(path.read_text(encoding="utf-8"))
    rv = RelationshipVector.from_dict(raw)
    db.upsert_relationship_vector(rv)


def _die(msg: str) -> None:
    click.echo(f"Error: {msg}", err=True)
    sys.exit(1)
