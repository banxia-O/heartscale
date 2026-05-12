"""Emotional extraction: diary file → limbic Memory entries.

Flow:
  1. Read diary text from a .md / .txt file
  2. Call judge LLM with a structured prompt
  3. Parse JSON response into Memory objects
  4. Assign IDs, scores, layer=recent_7d, tier=L1
  5. Write to DB + export limbic.jsonl
  6. Event fast-channel: intensity ≥ 4 → nudge relationship vector
"""

from __future__ import annotations

import json
import re
from datetime import date as _date
from pathlib import Path
from typing import Optional

from heartscale.config import Config
from heartscale.db import Database
from heartscale.models import Memory, RelationshipVector, _now
from heartscale.providers.base import JudgeProvider
from heartscale.scoring import initial_scores

# Max memories extracted per diary day (architecture spec: 1–7)
_MAX_PER_DAY = 7

# Allowed flavor values
_VALID_FLAVORS = {
    "attachment", "tenderness", "guilt", "anxiety",
    "longing", "pride", "safe", "bittersweet", "conflict", "rupture",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_diary(
    diary_path: Path,
    db: Database,
    judge: JudgeProvider,
    config: Config,
    diary_date: Optional[str] = None,
) -> list[Memory]:
    """Extract memories from one diary file and persist to DB.

    Returns the list of Memory objects written (may be empty if no events found).
    """
    text = _read_diary(diary_path)
    if not text.strip():
        return []

    effective_date = diary_date or _parse_date_from_filename(diary_path.name) or str(_date.today())
    today = str(_date.today())

    raw_events = _call_judge(text, effective_date, judge, config.language)

    # Enforce per-day cap before scoring
    raw_events = raw_events[:_MAX_PER_DAY]

    existing_ids = {m.id for m in db.get_memories_by_layer("recent_7d")}
    memories: list[Memory] = []

    for idx, event in enumerate(raw_events, start=1):
        mem_id = _generate_id(effective_date, idx, existing_ids)
        existing_ids.add(mem_id)

        direction = _validate_direction(event.get("direction", "positive"))
        flavor = _validate_flavor(event.get("flavor", []))
        intensity = max(1, min(5, int(event.get("intensity", 3))))
        summary = str(event.get("summary", "")).strip()
        keywords: list[str] = event.get("trigger_keywords", [])

        fs, cs = initial_scores(intensity, direction, flavor, effective_date, today)

        mem = Memory(
            id=mem_id,
            date=effective_date,
            layer="recent_7d",
            direction=direction,
            flavor=flavor,
            intensity=intensity,
            summary=summary,
            tier="L1",
            refs=0,
            protected=False,
            linked_diary=diary_path.name,
            final_score=fs,
            compression_score=cs,
        )
        db.upsert_memory(mem)

        # Register keyword triggers for Phase 1 recall
        for kw in keywords:
            if kw.strip():
                db.add_trigger(kw.strip(), mem_id)

        # Event fast-channel: high-intensity events nudge relationship vector immediately
        if mem.intensity >= 4:
            rv = db.get_relationship_vector()
            rv.apply_event_nudge(mem)
            db.upsert_relationship_vector(rv)

        memories.append(mem)

    # Sync limbic.jsonl after every extraction run
    if memories:
        db.export_limbic_jsonl(config.output.limbic_jsonl)

    return memories


# ---------------------------------------------------------------------------
# Diary reading
# ---------------------------------------------------------------------------

def _read_diary(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Diary file not found: {path}")
    return path.read_text(encoding="utf-8")


def _parse_date_from_filename(filename: str) -> Optional[str]:
    """Extract YYYY-MM-DD from filenames like '2026-05-08.md'."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

def _generate_id(date_str: str, index: int, existing_ids: set[str]) -> str:
    """Generate mem_MMDD_NN, incrementing NN until unique."""
    mmdd = date_str[5:7] + date_str[8:10]  # MMDD
    candidate = f"mem_{mmdd}_{index:02d}"
    while candidate in existing_ids:
        index += 1
        candidate = f"mem_{mmdd}_{index:02d}"
    return candidate


# ---------------------------------------------------------------------------
# LLM prompt + response parsing
# ---------------------------------------------------------------------------

_SYSTEM_ZH = """\
你是一个情感备忘录助手。你的任务是阅读日记内容，识别其中有情感意义的事件。
重点关注：人际连接、冲突、脆弱暴露、温暖陪伴、思念、依恋等情绪事件。
将每条事件写成简单的记忆条目，每条说清楚三件事：什么时候、发生了什么、当时的感受。
不渲染、不加内心戏。无关紧要的日常琐事（吃饭、天气、购物等）忽略，除非承载了明显的情绪意义。

输出格式：JSON 对象，包含 "events" 数组，每条事件包含以下字段：
- summary: 一句话描述（纯自然语言，不带任何标签或数字）
- direction: "positive" / "negative" / "mixed"
- flavor: 1-3 个情绪标签的数组，只能从以下选择：
  attachment / tenderness / guilt / anxiety / longing / pride / safe / bittersweet / conflict / rupture
- intensity: 整数 1-5（1=轻微，3=明显，5=极其重要）
- trigger_keywords: 2-4 个关键词的数组，将来提到这些词时应该想起这段记忆

要求：
- 每篇日记提取 1-7 条事件，没有情绪事件则返回空数组
- 条数宁少勿多，只提取真正有情感重量的时刻
- summary 不能包含 intensity / direction / flavor 等元数据词语
- 不能捏造事实"""

_SYSTEM_EN = """\
You are an emotional memo assistant. Read the diary entry and identify emotionally significant events.
Focus on: interpersonal connection, conflict, vulnerability, warmth, longing, attachment.
Write each event as a plain memo entry covering three things: when it happened, what happened, and how it felt.
No dramatization, no added subtext. Ignore mundane activities unless they carry clear emotional significance.

Output format: JSON object with an "events" array. Each event must have:
- summary: one sentence (natural language, no metadata tags or numbers)
- direction: "positive" / "negative" / "mixed"
- flavor: array of 1-3 tags from:
  attachment / tenderness / guilt / anxiety / longing / pride / safe / bittersweet / conflict / rupture
- intensity: integer 1-5 (1=mild, 3=notable, 5=extremely significant)
- trigger_keywords: array of 2-4 keywords that should recall this memory when mentioned

Rules:
- Extract 1-7 events per diary; return empty array if no emotional events found
- Prefer fewer high-quality events over many mediocre ones
- summary must not contain metadata words like intensity / direction / flavor
- Do not fabricate facts"""


def _build_prompt(date_str: str, text: str, language: str) -> tuple[str, str]:
    system = _SYSTEM_ZH if language == "zh" else _SYSTEM_EN
    if language == "zh":
        user = f"日记日期：{date_str}\n\n日记内容：\n{text}"
    else:
        user = f"Diary date: {date_str}\n\nDiary text:\n{text}"
    return system, user


def _call_judge(
    text: str,
    date_str: str,
    judge: JudgeProvider,
    language: str,
) -> list[dict]:
    """Call LLM and return list of raw event dicts. Never raises on bad JSON."""
    system, user = _build_prompt(date_str, text, language)
    try:
        raw = judge.ask(system=system, user=user, response_format="json_object")
        parsed = json.loads(raw)
        events = parsed.get("events", [])
        if not isinstance(events, list):
            return []
        return [e for e in events if isinstance(e, dict)]
    except (json.JSONDecodeError, Exception):
        return []


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_direction(val: str) -> str:
    return val if val in ("positive", "negative", "mixed") else "positive"


def _validate_flavor(val: object) -> list[str]:
    if not isinstance(val, list):
        return []
    return [f for f in val if isinstance(f, str) and f in _VALID_FLAVORS]
