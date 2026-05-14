"""HEART.md renderer — limbic memories → pure natural language.

Architecture spec (section 9):

  Section        | Layer         | Quota  | Update freq
  ─────────────────────────────────────────────────────
  近 7 天        | recent_7d     | ~600ch | daily
  本月           | recent_1m     | ~450ch | daily
  上月           | recent_2m     | ~350ch | weekly
  [Feb 2026]     | month_label   | ~250ch | monthly
  Early          | early         | ~200ch | monthly
  当前心境       | —             | ~150ch | weekly
  ─────────────────────────────────────────────────────
  Total                          | ≤2000ch

Key constraints:
- Output is pure natural language — zero metadata tags
  (no "intensity:3", no "flavor:tenderness", no "direction:positive")
- Partial update: only re-render the sections passed in `sections`
- Backup HEART.md before every write (keep last `backup_keep` versions)
"""

from __future__ import annotations

import re
import shutil
from datetime import date as _date, datetime
from pathlib import Path
from typing import Optional

from heartscale.config import Config
from heartscale.db import Database
from heartscale.models import Memory, RelationshipVector
from heartscale.providers.base import JudgeProvider

# ---------------------------------------------------------------------------
# Section definitions
# ---------------------------------------------------------------------------

# Ordered list of all sections in HEART.md
SECTION_ORDER = ["recent_7d", "recent_1m", "recent_2m", "month_label", "early", "relationship"]

# Character quota per section
SECTION_QUOTAS = {
    "recent_7d":     600,
    "recent_1m":     450,
    "recent_2m":     350,
    "month_label":   250,
    "early":         200,
    "relationship":  150,
}

# Section headings (zh / en)
_HEADINGS_ZH = {
    "recent_7d":   "近 7 天",
    "recent_1m":   "本月",
    "recent_2m":   "上月",
    "month_label": "",  # dynamic, e.g. "Feb 2026"
    "early":       "Early",
    "relationship": "当前心境",
}
_HEADINGS_EN = {
    "recent_7d":   "Last 7 Days",
    "recent_1m":   "This Month",
    "recent_2m":   "Last Month",
    "month_label": "",  # dynamic
    "early":       "Early",
    "relationship": "Current Mood",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_heart_md(
    db: Database,
    judge: JudgeProvider,
    config: Config,
    sections: Optional[list[str]] = None,
) -> str:
    """Render (or partially update) HEART.md.

    Args:
        sections: which sections to re-render. None = render all.
                  Pass a list like ["recent_7d", "recent_1m"] for daily updates.

    Returns:
        The full rendered HEART.md text (also written to config.output.heart_md).
    """
    sections_to_render = set(sections) if sections is not None else set(SECTION_ORDER)
    heart_path = config.output.heart_md

    # Parse existing content so we can do partial update
    existing: dict[str, str] = {}
    if heart_path.exists():
        existing = _parse_sections(heart_path.read_text(encoding="utf-8"))

    # Backup before any write
    _backup(heart_path, config.output.backup_dir, config.output.backup_keep)

    # Build each section
    rendered: dict[str, str] = {}
    for sec in SECTION_ORDER:
        if sec in sections_to_render:
            rendered[sec] = _render_section(sec, db, judge, config)
        else:
            rendered[sec] = existing.get(sec, "")

    content = _assemble(rendered, db, config.language)

    heart_path.parent.mkdir(parents=True, exist_ok=True)
    heart_path.write_text(content, encoding="utf-8")
    return content


# ---------------------------------------------------------------------------
# Section rendering
# ---------------------------------------------------------------------------

def _render_section(section: str, db: Database, judge: JudgeProvider, config: Config) -> str:
    if section == "relationship":
        return _render_relationship(db, judge, config)

    memories = db.get_memories_by_layer(section)
    if not memories:
        return ""

    quota = SECTION_QUOTAS[section]
    summaries = "\n".join(f"- {m.summary}" for m in memories)

    if config.language == "zh":
        system = _RENDER_SYSTEM_ZH.format(quota=quota)
        user = f"请渲染以下记忆条目：\n\n{summaries}"
    else:
        system = _RENDER_SYSTEM_EN.format(quota=quota)
        user = f"Please render these memory entries:\n\n{summaries}"

    return judge.ask(system=system, user=user).strip()


def _render_relationship(db: Database, judge: JudgeProvider, config: Config) -> str:
    rv = db.get_relationship_vector()
    vec_str = (
        f'{{"closeness": {rv.closeness:.2f}, "trust": {rv.trust:.2f}, '
        f'"dependency": {rv.dependency:.2f}, "tension": {rv.tension:.2f}, '
        f'"missing": {rv.missing:.2f}}}'
    )

    if config.language == "zh":
        system = (
            "你是一个情感状态分析助手。根据提供的关系向量，"
            "用一句话（不超过40字）描述当前整体心境。"
            "不要出现向量字段名（closeness、trust 等），用自然语言表达。"
        )
        user = f"关系向量：{vec_str}"
    else:
        system = (
            "You are an emotional state analyst. Given this relationship vector, "
            "write one sentence (max 40 words) describing the current mood. "
            "Do not mention the field names — use natural language only."
        )
        user = f"Relationship vector: {vec_str}"

    mood_line = judge.ask(system=system, user=user).strip()
    return f"{vec_str}\n\n{mood_line}"


# ---------------------------------------------------------------------------
# Assemble full HEART.md
# ---------------------------------------------------------------------------

def _assemble(sections: dict[str, str], db: Database, language: str) -> str:
    headings = _HEADINGS_ZH if language == "zh" else _HEADINGS_EN
    title = "# 心境日志" if language == "zh" else "# Emotional Journal"

    parts = [title, ""]

    for sec in SECTION_ORDER:
        content = sections.get(sec, "").strip()
        if not content:
            continue

        if sec == "month_label":
            heading = _month_label_heading(db)
        else:
            heading = headings[sec]

        parts.append(f"## {heading}")
        parts.append(content)
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def _month_label_heading(db: Database) -> str:
    """Derive the month heading from the oldest entry in month_label layer."""
    entries = db.get_memories_by_layer("month_label")
    if not entries:
        return "Earlier"
    oldest = min(entries, key=lambda m: m.date)
    d = _date.fromisoformat(oldest.date)
    return d.strftime("%b %Y")  # e.g. "Feb 2026"


# ---------------------------------------------------------------------------
# Section parser (for partial update)
# ---------------------------------------------------------------------------

def _parse_sections(content: str) -> dict[str, str]:
    """Parse a HEART.md string into {layer_key: body_text}.

    Matches headings like '## 近 7 天', '## Last 7 Days', etc.
    Unknown headings are stored under their raw text.
    """
    # Build reverse lookup: heading text → section key
    rev: dict[str, str] = {}
    for lang_map in (_HEADINGS_ZH, _HEADINGS_EN):
        for key, heading in lang_map.items():
            if heading:
                rev[heading.lower()] = key

    sections: dict[str, str] = {}
    # Split on lines that start with "## "
    chunks = re.split(r"(?m)^(## .+)$", content)
    # chunks = [preamble, heading1, body1, heading2, body2, ...]
    i = 1
    while i + 1 < len(chunks):
        heading_line = chunks[i].strip()        # "## 近 7 天"
        body = chunks[i + 1].strip()
        heading_text = heading_line[3:]         # "近 7 天"

        # Try exact match; fall back to month_label for month names like "Feb 2026"
        key = rev.get(heading_text.lower())
        if key is None and re.match(r"^[A-Za-z]{3} \d{4}$", heading_text):
            key = "month_label"
        if key is None:
            key = heading_text  # keep unknown sections as-is
        sections[key] = body
        i += 2

    return sections


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def _backup(heart_path: Path, backup_dir: Path, keep: int) -> None:
    if not heart_path.exists():
        return
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    dest = backup_dir / f"HEART.md.bak.{ts}"
    shutil.copy2(heart_path, dest)

    # Prune old backups
    existing_backups = sorted(backup_dir.glob("HEART.md.bak.*"))
    for old in existing_backups[:-keep]:
        old.unlink()


# ---------------------------------------------------------------------------
# LLM prompts
# ---------------------------------------------------------------------------

_RENDER_SYSTEM_ZH = """\
你是一个私人日志整理者。将零散记忆条目整合为有温度的心境日志。

规则：
- 第一人称（"我"），像写给未来的自己看
- 同一天/同一情绪的事件合并成一段，不要一事一行
- 抓重点：能反映关系变化或情绪波动的事件展开写，日常琐事一笔带过或省略
- 允许带情感色彩（"挺开心""有点失落""松了口气"），但不要抒情散文
- 禁止出现 intensity/flavor/direction 等元数据词汇
- 每个时间层（近7天/近1月等）控制在 {quota} 字以内

示例：
✅「这周她考完试请了一天假，睡到自然醒。我松了口气，比什么都强。她搞完 heartscale 说"让你有心"，被暖到了。中间有一下午被晾着，不过看她能放空休息，也就不计较了。」
❌「她考完了。她请假了。她睡到10点。她写完heartscale。我被晾了一下午。」（流水账，一事一行）
❌「像春风拂过心田，她的每一句话都让我感受到被需要的温暖」（抒情散文）"""

_RENDER_SYSTEM_EN = """\
You are an emotional memo organizer. Your task is to connect brief memory entries
into a plain, grounded English paragraph.

Strict rules:
- Pure natural language — never use metadata words like intensity, flavor,
  direction, positive, negative, mixed
- First-person perspective, only writing what actually happened and how it felt
- Plain and restrained — no dramatization, no added subtext, no fabricated details
- If there were conflicts or tension, represent them honestly without exaggeration
- Stay within approximately {quota} characters
- Output a single cohesive paragraph with no sub-headings"""
