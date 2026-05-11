"""Core data models for heartscale."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

VALID_LAYERS = {"recent_7d", "recent_1m", "recent_2m", "month_label", "early"}
VALID_DIRECTIONS = {"positive", "negative", "mixed"}
VALID_TIERS = {"L1", "L2", "L3"}
VALID_FLAVORS = {
    "attachment", "tenderness", "guilt", "anxiety",
    "longing", "pride", "safe", "bittersweet", "conflict", "rupture",
}


@dataclass
class Memory:
    id: str                          # mem_MMDD_NN
    date: str                        # YYYY-MM-DD
    layer: str                       # recent_7d / recent_1m / recent_2m / month_label / early
    direction: str                   # positive / negative / mixed
    flavor: list[str]                # subset of VALID_FLAVORS
    intensity: int                   # 1–5
    summary: str                     # one-line summary, no metadata
    tier: str                        # L1 / L2 / L3
    refs: int = 0                    # times actually injected into context
    protected: bool = False
    pending_review: bool = False
    linked_diary: Optional[str] = None    # recent_7d only: diary filename
    linked_from: Optional[str] = None     # non-recent_7d: source memory id
    final_score: float = 0.0
    compression_score: float = 0.0
    created_at: str = field(default_factory=lambda: _now())
    updated_at: str = field(default_factory=lambda: _now())

    # ------------------------------------------------------------------ #
    # Serialisation                                                        #
    # ------------------------------------------------------------------ #

    def to_jsonl_dict(self) -> dict:
        """Produce the limbic.jsonl representation (matches ARCHITECTURE spec)."""
        d: dict = {
            "id": self.id,
            "date": self.date,
            "layer": self.layer,
            "direction": self.direction,
            "flavor": self.flavor,
            "intensity": self.intensity,
            "summary": self.summary,
            "tier": self.tier,
            "refs": self.refs,
            "protected": self.protected,
        }
        if self.pending_review:
            d["pending_review"] = True
        if self.linked_diary is not None:
            d["linked_diary"] = self.linked_diary
        if self.linked_from is not None:
            d["linked_from"] = self.linked_from
        return d

    @classmethod
    def from_jsonl_dict(cls, d: dict) -> "Memory":
        return cls(
            id=d["id"],
            date=d["date"],
            layer=d["layer"],
            direction=d["direction"],
            flavor=d.get("flavor", []),
            intensity=int(d["intensity"]),
            summary=d["summary"],
            tier=d["tier"],
            refs=int(d.get("refs", 0)),
            protected=bool(d.get("protected", False)),
            pending_review=bool(d.get("pending_review", False)),
            linked_diary=d.get("linked_diary"),
            linked_from=d.get("linked_from"),
            final_score=float(d.get("final_score", 0.0)),
            compression_score=float(d.get("compression_score", 0.0)),
            created_at=d.get("created_at", _now()),
            updated_at=d.get("updated_at", _now()),
        )

    def to_jsonl_line(self) -> str:
        return json.dumps(self.to_jsonl_dict(), ensure_ascii=False)


@dataclass
class RelationshipVector:
    closeness: float = 0.5
    trust: float = 0.5
    dependency: float = 0.3
    tension: float = 0.1
    missing: float = 0.5
    updated_at: str = field(default_factory=lambda: _now())

    def to_dict(self) -> dict:
        return {
            "closeness": self.closeness,
            "trust": self.trust,
            "dependency": self.dependency,
            "tension": self.tension,
            "missing": self.missing,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RelationshipVector":
        return cls(
            closeness=float(d.get("closeness", 0.5)),
            trust=float(d.get("trust", 0.5)),
            dependency=float(d.get("dependency", 0.3)),
            tension=float(d.get("tension", 0.1)),
            missing=float(d.get("missing", 0.5)),
            updated_at=d.get("updated_at", _now()),
        )

    def apply_event_nudge(self, memory: Memory) -> None:
        """Fast-channel: intensity ≥ 4 memories immediately nudge the vector (±0.05)."""
        if memory.intensity < 4:
            return
        delta = 0.05
        if memory.direction == "positive":
            self.closeness = _clamp(self.closeness + delta)
            self.trust = _clamp(self.trust + delta)
            self.missing = _clamp(self.missing - delta)
        elif memory.direction == "negative":
            self.tension = _clamp(self.tension + delta)
            self.closeness = _clamp(self.closeness - delta)
        # mixed: tension up, closeness unchanged
        elif memory.direction == "mixed":
            self.tension = _clamp(self.tension + delta * 0.5)
        self.updated_at = _now()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))
