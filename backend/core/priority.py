"""Generic Priority Engine for Formal.

Scores any Item deterministically and explains every input.

Formula:
    importance_norm = importance / 5
    effort_norm     = min(effort_minutes / EFFORT_CAP_MINUTES, 1.0), floored at EFFORT_FLOOR
    value           = consequence_norm / effort_norm   (capped at 1.0)
                      — if effort_minutes is None, value = consequence_norm
    P               = clamp(importance_norm * urgency_factor * value, 0.0, 1.0)

Urgency factor (days until due_at, relative to UTC now):
    overdue          → 1.00
    0 < days <= 1    → 0.90
    1 < days <= 7    → linear 0.90 → 0.50
    7 < days <= 30   → linear 0.50 → 0.10
    days > 30        → 0.10
    no due_at        → 0.30

Tier bands:
    S ≥ 0.80 | A ≥ 0.60 | B ≥ 0.40 | C ≥ 0.20 | D < 0.20
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TypedDict

from core.items import Item

EFFORT_CAP_MINUTES: float = 240.0   # 4 hours → effort_norm = 1.0
EFFORT_FLOOR: float = 0.10          # prevents divide-by-zero; minimum effort_norm


class ScoreResult(TypedDict):
    score: float
    tier: str
    reasons: dict


_URGENCY_CONTEXT_FLOORS = {
    "available_window": 0.65,  # window just opened, no hard date but act soon
    "hard_deadline":    0.30,  # has explicit due_at, handled below
    "evergreen":        0.20,  # no time pressure
}


def _urgency(due_at: str | None, now: datetime, urgency_context: str | None = None) -> float:
    if due_at is None:
        # None means no context signal — preserve old default of 0.30
        # Only deviate when Groq explicitly provided a context value
        if urgency_context is None:
            return 0.30
        return _URGENCY_CONTEXT_FLOORS.get(urgency_context, 0.30)
    due = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    days = (due - now).total_seconds() / 86400.0
    if days <= 0:
        return 1.00
    if days <= 1:
        return 0.90
    if days <= 7:
        # linear: 1 day → 0.90, 7 days → 0.50
        return 0.90 - (days - 1) / 6 * 0.40
    if days <= 30:
        # linear: 7 days → 0.50, 30 days → 0.10
        return 0.50 - (days - 7) / 23 * 0.40
    return 0.10


def _tier(score: float) -> str:
    if score >= 0.80:
        return "S"
    if score >= 0.60:
        return "A"
    if score >= 0.40:
        return "B"
    if score >= 0.20:
        return "C"
    return "D"


def score_item(item: Item, now: datetime | None = None) -> ScoreResult:
    """Return priority score, tier, and explainable reasons for one Item."""
    now = now or datetime.now(timezone.utc)

    importance_norm  = item.importance / 5.0
    consequence_norm = item.consequence / 5.0
    payload          = item.raw_payload or {}
    urgency_context  = payload.get("urgency_context")
    urgency_factor   = _urgency(item.due_at, now, urgency_context=urgency_context)

    # Gmail-Important items without a deadline get a higher urgency floor
    # so they always rank above non-important undated items
    is_important = bool(payload.get("is_important"))
    if item.due_at is None and is_important:
        urgency_factor = max(urgency_factor, 0.55)

    if item.effort_minutes is not None:
        effort_norm = min(item.effort_minutes / EFFORT_CAP_MINUTES, 1.0)
        effort_norm = max(effort_norm, EFFORT_FLOOR)
        value = min(consequence_norm / effort_norm, 1.0)
        effort_label = f"{item.effort_minutes:.0f} min"
    else:
        effort_norm = None
        value = consequence_norm
        effort_label = "unknown"

    raw = importance_norm * urgency_factor * value
    score = round(min(max(raw, 0.0), 1.0), 4)

    reasons: dict = {
        "importance": item.importance,
        "importance_norm": round(importance_norm, 4),
        "consequence": item.consequence,
        "consequence_norm": round(consequence_norm, 4),
        "effort": effort_label,
        "effort_norm": round(effort_norm, 4) if effort_norm is not None else None,
        "value": round(value, 4),
        "due_at": item.due_at,
        "urgency_context": urgency_context,
        "urgency_factor": round(urgency_factor, 4),
        "is_important_boost": is_important and item.due_at is None,
        "raw": round(raw, 4),
    }

    return ScoreResult(score=score, tier=_tier(score), reasons=reasons)
