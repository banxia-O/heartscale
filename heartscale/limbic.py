"""Cascade compression for the limbic memory layer.

Architecture spec (section 3, v2.1):

  Obsidian diary ──→ recent_7d (daily, extract.py writes here)
                         ↓  slide after 7 days
                      recent_1m  (cap 15, updated daily)
                         ↓  slide after 30 days
                      recent_2m  (cap 15, updated weekly)
                         ↓  slide after 60 days
                      month_label (cap 15, updated monthly)
                         ↓  slide after 90 days
                      early       (cap 20, updated monthly)
                         ↓  >180 days
                      promote to L2 (protected) or delete

Schedule:
  daily   → run_daily():   slide recent_7d→1m, compress recent_1m
  weekly  → run_weekly():  decay + L2 promotion + slide 1m→2m, compress 2m
  monthly → run_monthly(): slide 2m→month_label→early, handle expired
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date as _date, timedelta
from typing import Optional

from heartscale.db import Database
from heartscale.models import Memory, _now
from heartscale.scoring import (
    compute_compression_score,
    compute_final_score,
    compute_time_decay,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LAYER_CAPS: dict[str, int] = {
    "recent_7d": 49,      # 7/day × 7 days; per-day cap enforced in extract.py
    "recent_1m": 15,
    "recent_2m": 15,
    "month_label": 15,
    "early": 20,
}

# Entries older than this many days slide OUT of the layer
LAYER_WINDOW_DAYS: dict[str, int] = {
    "recent_7d": 7,
    "recent_1m": 30,
    "recent_2m": 60,
    "month_label": 90,
    "early": 180,
}

NEXT_LAYER: dict[str, str] = {
    "recent_7d": "recent_1m",
    "recent_1m": "recent_2m",
    "recent_2m": "month_label",
    "month_label": "early",
}

# L1→L2 promotion thresholds (architecture section 6)
L2_PROMOTION_REFS = 5
L2_PROMOTION_INTENSITY = 3       # ≥ 0.6 × 5
L2_PROMOTION_AGE_DAYS = 30


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class CompressResult:
    layer: str
    slid: int = 0       # entries moved into this layer this run
    kept: int = 0       # entries surviving after compression
    deleted: int = 0    # entries removed
    pending: int = 0    # entries marked pending_review
    merged: int = 0     # entries merged (same-source dedup)
    promoted: int = 0   # entries promoted to L2 (weekly / expired handling)
    skipped: bool = False  # empty-window compensation triggered — no compression run


# ---------------------------------------------------------------------------
# Main compressor class
# ---------------------------------------------------------------------------

class LimbicCompressor:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------ #
    # Public schedule entry points                                         #
    # ------------------------------------------------------------------ #

    def run_daily(self, today: str) -> list[CompressResult]:
        """After extraction: slide recent_7d → recent_1m, compress recent_1m."""
        n_slid = self._slide("recent_7d", "recent_1m", today)
        if n_slid > 0:
            self._merge_same_source("recent_1m")
        result = self._compress("recent_1m", today, new_arrivals=n_slid)
        return [result]

    def run_weekly(self, today: str) -> list[CompressResult]:
        """Decay + L2 promotion + slide recent_1m → recent_2m + compress."""
        results: list[CompressResult] = []

        self._apply_decay(today)
        promoted = self._check_l2_promotion(today)

        n_slid = self._slide("recent_1m", "recent_2m", today)
        if n_slid > 0:
            self._merge_same_source("recent_2m")
        r = self._compress("recent_2m", today, new_arrivals=n_slid)
        r.promoted = promoted
        results.append(r)
        return results

    def run_monthly(self, today: str) -> list[CompressResult]:
        """Slide 2m → month_label → early, handle expired (>180 days)."""
        results: list[CompressResult] = []

        n_slid_2m = self._slide("recent_2m", "month_label", today)
        if n_slid_2m > 0:
            self._merge_same_source("month_label")
        results.append(self._compress("month_label", today, new_arrivals=n_slid_2m))

        n_slid_ml = self._slide("month_label", "early", today)
        if n_slid_ml > 0:
            self._merge_same_source("early")
        results.append(self._compress("early", today, new_arrivals=n_slid_ml))

        promoted, deleted = self._handle_expired(today)
        results.append(CompressResult(
            layer="expired", promoted=promoted, deleted=deleted
        ))
        return results

    # ------------------------------------------------------------------ #
    # Slide                                                                #
    # ------------------------------------------------------------------ #

    def _slide(self, from_layer: str, to_layer: str, today: str) -> int:
        """Move aged-out entries from from_layer to to_layer.

        Only non-protected entries slide; protected ones stay put until
        manually cleared or promoted.
        Returns count of entries moved.
        """
        cutoff = _days_ago(today, LAYER_WINDOW_DAYS[from_layer])
        entries = self.db.get_memories_by_layer(from_layer)
        moved = 0
        for m in entries:
            if m.date < cutoff and not m.protected:
                m.layer = to_layer
                m.updated_at = _now()
                self.db.upsert_memory(m)
                moved += 1
        return moved

    # ------------------------------------------------------------------ #
    # Compress                                                             #
    # ------------------------------------------------------------------ #

    def _compress(self, layer: str, today: str, new_arrivals: int) -> CompressResult:
        """Enforce layer cap, applying all filtering rules from spec."""
        result = CompressResult(layer=layer, slid=new_arrivals)

        # Empty-window compensation: no new entries → skip
        if new_arrivals == 0:
            result.skipped = True
            result.kept = len(self.db.get_memories_by_layer(layer))
            return result

        entries = self.db.get_memories_by_layer(layer)
        cap = LAYER_CAPS[layer]

        if len(entries) <= cap:
            result.kept = len(entries)
            return result

        # Apply direction-flip bonus before sorting
        self._apply_direction_flip_bonus(entries)

        protected = [m for m in entries if m.protected]
        candidates = [m for m in entries if not m.protected]
        candidates.sort(key=lambda m: m.compression_score, reverse=True)

        slots = max(0, cap - len(protected))
        survivors = candidates[:slots]
        eliminated = candidates[slots:]

        result.kept = len(protected) + len(survivors)

        for m in eliminated:
            if m.refs >= 3 and m.intensity >= 3:
                self.db.mark_pending_review(m.id)
                result.pending += 1
            else:
                self.db.delete_memory(m.id)
                result.deleted += 1

        return result

    # ------------------------------------------------------------------ #
    # Same-source merge                                                    #
    # ------------------------------------------------------------------ #

    def _merge_same_source(self, layer: str) -> int:
        """Merge entries in a layer that share the same linked_from.

        Phase 1: merges entries with identical linked_from (exact match).
        TODO Phase 2: extend with cosine similarity threshold 0.85 using
                      sqlite-vec embeddings.

        Returns count of entries removed by merging.
        """
        entries = self.db.get_memories_by_layer(layer)
        groups: dict[str, list[Memory]] = defaultdict(list)
        no_source: list[Memory] = []

        for m in entries:
            if m.linked_from:
                groups[m.linked_from].append(m)
            else:
                no_source.append(m)

        removed = 0
        for source_id, group in groups.items():
            if len(group) < 2:
                continue
            # Winner = highest intensity; tie-break by compression_score
            group.sort(key=lambda m: (m.intensity, m.compression_score), reverse=True)
            winner = group[0]
            # Merge: union of flavors, keep highest intensity
            merged_flavors = list({f for m in group for f in m.flavor})
            winner.flavor = merged_flavors
            winner.updated_at = _now()
            self.db.upsert_memory(winner)
            for loser in group[1:]:
                self.db.delete_memory(loser.id)
                removed += 1

        return removed

    # ------------------------------------------------------------------ #
    # Direction-flip bonus                                                 #
    # ------------------------------------------------------------------ #

    def _apply_direction_flip_bonus(self, entries: list[Memory]) -> None:
        """Add +3 compression_score to entries whose direction opposes their source.

        Architecture: "如果某条记忆的 direction 与同来源的前一条记忆 direction 相反，
        额外 +3 分——关系波动比平稳更值得记住"
        """
        for m in entries:
            if not m.linked_from:
                continue
            source = self.db.get_memory(m.linked_from)
            if source is None:
                continue
            if _opposite_directions(m.direction, source.direction):
                m.compression_score += 3.0
                self.db.update_scores(m.id, m.final_score, m.compression_score)

    # ------------------------------------------------------------------ #
    # Decay                                                                #
    # ------------------------------------------------------------------ #

    def _apply_decay(self, today: str) -> None:
        """Recalculate final_score and compression_score for all non-protected entries.

        Uses: ai_score = intensity/5, mention_frequency = 0 (Phase 1),
              recall_count = min(refs/10, 1.0), time_decay = f(age).
        """
        all_memories = self.db.get_all_memories()
        for m in all_memories:
            if m.protected:
                continue
            ai_score = m.intensity / 5.0
            recall_count = min(m.refs / 10.0, 1.0)
            decay = compute_time_decay(m.date, today)
            fs = compute_final_score(ai_score, 0.0, recall_count, decay)
            cs = compute_compression_score(fs, m.direction, m.flavor, m.refs)
            self.db.update_scores(m.id, fs, cs)

    # ------------------------------------------------------------------ #
    # L1 → L2 promotion                                                   #
    # ------------------------------------------------------------------ #

    def _check_l2_promotion(self, today: str) -> int:
        """Promote L1 entries to L2 if they meet all thresholds.

        Conditions (architecture section 6):
          refs ≥ 5  AND  intensity ≥ 3  AND  age ≥ 30 days
        Returns count promoted.
        """
        cutoff = _days_ago(today, L2_PROMOTION_AGE_DAYS)
        all_memories = self.db.get_all_memories()
        promoted = 0
        for m in all_memories:
            if m.tier != "L1":
                continue
            if m.date > cutoff:
                continue  # too young
            if m.refs >= L2_PROMOTION_REFS and m.intensity >= L2_PROMOTION_INTENSITY:
                m.tier = "L2"
                m.protected = True
                m.updated_at = _now()
                self.db.upsert_memory(m)
                promoted += 1
        return promoted

    # ------------------------------------------------------------------ #
    # Expired entries (> 180 days)                                         #
    # ------------------------------------------------------------------ #

    def _handle_expired(self, today: str) -> tuple[int, int]:
        """Promote or delete early-layer entries older than 180 days.

        Promotion condition: protected=True OR (refs≥5 AND intensity≥3).
        Promoted entries move to layer='permanent' (kept in SQLite, excluded
        from active limbic export).
        Returns (promoted, deleted).
        """
        cutoff = _days_ago(today, LAYER_WINDOW_DAYS["early"])
        early_entries = self.db.get_memories_by_layer("early")
        promoted = 0
        deleted = 0
        for m in early_entries:
            if m.date >= cutoff:
                continue  # not expired yet
            if m.protected or (m.refs >= 5 and m.intensity >= 3):
                m.layer = "permanent"
                m.tier = "L2"
                m.protected = True
                m.updated_at = _now()
                self.db.upsert_memory(m)
                promoted += 1
            else:
                self.db.delete_memory(m.id)
                deleted += 1
        return promoted, deleted


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _days_ago(today: str, n: int) -> str:
    d = _date.fromisoformat(today) - timedelta(days=n)
    return str(d)


def _opposite_directions(a: str, b: str) -> bool:
    return (a == "positive" and b == "negative") or (a == "negative" and b == "positive")
