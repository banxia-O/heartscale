"""Scoring functions shared by extract.py and limbic.py.

Architecture spec (section 7 + section 3):

  final_score = 0.4 × ai_score
              + 0.2 × mention_frequency
              + 0.3 × recall_count   (normalised refs)
              + 0.1 × time_decay

  compression_score = final_score
                    + 2.0  if direction in [negative, mixed]
                    + 1.0  if "conflict" or "rupture" in flavor
                    + 0.5 × refs
"""

from __future__ import annotations

import math
from datetime import date as _date


def compute_time_decay(memory_date: str, today: str) -> float:
    """Exponential decay with 90-day half-life (L1 spec). Returns 0–1."""
    d = _date.fromisoformat(memory_date)
    t = _date.fromisoformat(today)
    days_old = max(0, (t - d).days)
    return math.exp(-days_old * math.log(2) / 90)


def compute_final_score(
    ai_score: float,
    mention_frequency: float,
    recall_count: float,
    time_decay: float,
) -> float:
    """Weighted combination per architecture section 7."""
    return (
        0.4 * _clamp(ai_score)
        + 0.2 * _clamp(mention_frequency)
        + 0.3 * _clamp(recall_count)
        + 0.1 * _clamp(time_decay)
    )


def compute_compression_score(
    final_score: float,
    direction: str,
    flavor: list[str],
    refs: int,
) -> float:
    """compression_score used for cascade filtering (section 3)."""
    score = final_score
    if direction in ("negative", "mixed"):
        score += 2.0
    if "conflict" in flavor or "rupture" in flavor:
        score += 1.0
    score += 0.5 * refs
    return score


def initial_scores(
    intensity: int,
    direction: str,
    flavor: list[str],
    memory_date: str,
    today: str,
) -> tuple[float, float]:
    """Compute (final_score, compression_score) for a freshly extracted memory.

    At extraction time: mention_frequency and recall_count are both 0.
    """
    ai_score = intensity / 5.0
    decay = compute_time_decay(memory_date, today)
    fs = compute_final_score(ai_score, 0.0, 0.0, decay)
    cs = compute_compression_score(fs, direction, flavor, refs=0)
    return fs, cs


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))
