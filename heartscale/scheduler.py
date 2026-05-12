"""Job orchestration for heartscale scheduled runs.

Three schedule tiers (architecture section 11):

  daily   00:05  → extract new diary → compress daily → render recent sections
  weekly  Sunday → decay + L2 promotion + compress weekly → render mid sections
  monthly 1st    → compress monthly + cleanup → render all sections
"""

from __future__ import annotations

from datetime import date as _date
from pathlib import Path
from typing import Optional

from heartscale.config import Config
from heartscale.db import Database
from heartscale.extract import extract_diary, _parse_date_from_filename
from heartscale.limbic import LimbicCompressor
from heartscale.providers.base import JudgeProvider
from heartscale.providers.factory import make_embedding_provider, make_judge_provider
from heartscale.render import render_heart_md


# ---------------------------------------------------------------------------
# Job functions (called by scheduler or CLI)
# ---------------------------------------------------------------------------

def run_daily(config: Config, db: Database, judge: JudgeProvider, today: Optional[str] = None) -> dict:
    """Nightly job: extract diary → compress daily → render recent sections.

    Returns a summary dict with counts for logging.
    """
    today = today or str(_date.today())
    summary: dict = {"date": today, "extracted": 0, "slid": 0, "rendered": []}

    # 1. Extract today's diary (and any unprocessed recent diaries)
    extracted = _extract_recent_diaries(config, db, judge, today)
    summary["extracted"] = extracted

    # 2. Cascade: slide recent_7d → recent_1m, compress recent_1m
    comp = LimbicCompressor(db)
    results = comp.run_daily(today)
    summary["slid"] = sum(r.slid for r in results)

    # 3. Render only the sections that update daily
    render_heart_md(db, judge, config, sections=["recent_7d", "recent_1m"])
    summary["rendered"] = ["recent_7d", "recent_1m"]

    return summary


def run_weekly(config: Config, db: Database, judge: JudgeProvider, today: Optional[str] = None) -> dict:
    """Weekly job: decay + L2 promotion + compress weekly → render mid sections."""
    today = today or str(_date.today())
    summary: dict = {"date": today, "promoted": 0, "rendered": []}

    comp = LimbicCompressor(db)
    results = comp.run_weekly(today)
    summary["promoted"] = sum(r.promoted for r in results)

    render_heart_md(db, judge, config, sections=["recent_2m", "relationship"])
    summary["rendered"] = ["recent_2m", "relationship"]

    return summary


def run_monthly(config: Config, db: Database, judge: JudgeProvider, today: Optional[str] = None) -> dict:
    """Monthly job: deep compression + cleanup → full render."""
    today = today or str(_date.today())
    summary: dict = {"date": today, "promoted": 0, "deleted": 0, "rendered": []}

    comp = LimbicCompressor(db)
    results = comp.run_monthly(today)
    for r in results:
        summary["promoted"] += r.promoted
        summary["deleted"] += r.deleted

    render_heart_md(db, judge, config, sections=["month_label", "early"])
    summary["rendered"] = ["month_label", "early"]

    return summary


# ---------------------------------------------------------------------------
# Diary discovery
# ---------------------------------------------------------------------------

def _extract_recent_diaries(
    config: Config,
    db: Database,
    judge: JudgeProvider,
    today: str,
    days_back: int = 1,
) -> int:
    """Find and extract diary files not yet processed.

    Looks for files in config.diary.dir whose date is within the last
    `days_back` days and which have no memories linked to them yet.
    Returns count of memories extracted.
    """
    diary_dir = config.diary.dir
    if not diary_dir.exists():
        return 0

    # Collect all existing linked_diary values to detect already-processed files
    processed: set[str] = {
        m.linked_diary for m in db.get_all_memories()
        if m.linked_diary is not None
    }

    total = 0
    for ext in config.diary.extensions:
        for path in sorted(diary_dir.glob(f"*{ext}")):
            if path.name in processed:
                continue
            date_str = _parse_date_from_filename(path.name)
            if date_str is None or date_str > today:
                continue  # future files or undated files — skip
            memories = extract_diary(path, db, judge, config, diary_date=date_str)
            total += len(memories)

    return total


# ---------------------------------------------------------------------------
# Long-running scheduler
# ---------------------------------------------------------------------------

def start_scheduler(config: Config) -> None:
    """Block forever, running jobs on schedule. Ctrl+C to stop."""
    import schedule
    import time

    judge = make_judge_provider(config.judge)
    db = Database(config.database.path, embedding_dim=config.embedding.dimension or 1024)

    def _daily():
        print(f"[scheduler] Running daily job…")
        result = run_daily(config, db, judge)
        print(f"[scheduler] Daily done: {result}")

    def _weekly():
        print(f"[scheduler] Running weekly job…")
        result = run_weekly(config, db, judge)
        print(f"[scheduler] Weekly done: {result}")

    def _monthly():
        print(f"[scheduler] Running monthly job…")
        result = run_monthly(config, db, judge)
        print(f"[scheduler] Monthly done: {result}")

    daily_time = config.schedule.daily_time
    weekly_day = config.schedule.weekly_day.lower()
    monthly_day = config.schedule.monthly_day

    schedule.every().day.at(daily_time).do(_daily)
    getattr(schedule.every(), weekly_day).do(_weekly)

    print(f"[scheduler] Started. Daily at {daily_time}, weekly on {weekly_day}.")
    print("[scheduler] Press Ctrl+C to stop.")

    try:
        while True:
            schedule.run_pending()
            # Monthly: check on first run of each day
            if _date.today().day == monthly_day:
                _monthly()
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n[scheduler] Stopped.")
    finally:
        db.close()
