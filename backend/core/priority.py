"""Generic Priority Engine for Formal.

Scores any Item deterministically and explains every input.

Formula (additive weighted sum):
    P = clamp(W_u * urgency + W_c * consequence_norm + W_i * importance_norm, 0, 1)

    W_u = 0.50  — urgency dominates: due soonest rises
    W_c = 0.30  — consequence distinguishes: high stakes beats low stakes
    W_i = 0.20  — importance nudges: personal relevance as tiebreaker

No factor can zero out another. Each contributes independently.

Urgency factor (days until due_at, relative to UTC now):
    overdue ≤1 day   → 1.00
    overdue 1–3 days → 0.70
    overdue 3–7 days → 0.40  (7+ auto-archived)
    0 < days <= 1    → 0.90
    1 < days <= 7    → linear 0.90 → 0.50
    7 < days <= 30   → linear 0.50 → 0.10
    days > 30        → 0.10
    no due_at        → context-dependent (available_window=0.65, evergreen=0.20, default=0.30)

Tier bands:
    S ≥ 0.80 | A ≥ 0.60 | B ≥ 0.40 | C ≥ 0.20 | D < 0.20
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import TypedDict

from core.items import Item

# Tunable weights — must sum to 1.0
W_URGENCY:     float = 0.50
W_CONSEQUENCE: float = 0.30
W_IMPORTANCE:  float = 0.20


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
        if urgency_context is None:
            return 0.30
        return _URGENCY_CONTEXT_FLOORS.get(urgency_context, 0.30)
    due = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    days = (due - now).total_seconds() / 86400.0
    if days <= 0:
        overdue = -days
        if overdue <= 1:
            return 1.00
        if overdue <= 3:
            return 0.70
        return 0.40  # 3-7 days overdue; 7+ auto-archived elsewhere
    if days <= 1:
        return 0.90
    if days <= 7:
        return 0.90 - (days - 1) / 6 * 0.40
    if days <= 30:
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


def _reputation_boost(sender_domain: str | None, conn: sqlite3.Connection | None) -> float:
    """Compute an importance nudge from user done/dismiss history for this sender domain."""
    if not sender_domain or conn is None:
        return 0.0
    try:
        row = conn.execute(
            "SELECT done_count, dismiss_count FROM sender_reputation WHERE domain = ?",
            (sender_domain,),
        ).fetchone()
    except Exception:
        return 0.0
    if not row:
        return 0.0
    done, dismiss = row["done_count"], row["dismiss_count"]
    total = done + dismiss
    if total < 2:
        return 0.0  # too few signals
    ratio = done / total  # 0.0 = all dismissed, 1.0 = all done
    return (ratio - 0.5) * 0.4  # maps to [-0.2, +0.2]


def score_item(item: Item, now: datetime | None = None,
               conn: sqlite3.Connection | None = None) -> ScoreResult:
    """Return priority score, tier, and explainable reasons for one Item."""
    now = now or datetime.now(timezone.utc)

    importance_norm  = item.importance / 5.0
    consequence_norm = item.consequence / 5.0
    payload          = item.raw_payload or {}
    urgency_context  = payload.get("urgency_context")
    urgency_factor   = _urgency(item.due_at, now, urgency_context=urgency_context)

    # Gmail-Important adds a small nudge to importance
    is_important = bool(payload.get("is_important"))
    if is_important:
        importance_norm = min(importance_norm + 0.10, 1.0)

    # Sender reputation nudge (learned from user done/dismiss actions)
    sender_domain = payload.get("sender_domain")
    rep_boost = _reputation_boost(sender_domain, conn)
    if rep_boost:
        importance_norm = min(max(importance_norm + rep_boost, 0.0), 1.0)

    raw = W_URGENCY * urgency_factor + W_CONSEQUENCE * consequence_norm + W_IMPORTANCE * importance_norm

    score = round(min(max(raw, 0.0), 1.0), 4)

    reasons: dict = {
        "importance": item.importance,
        "importance_norm": round(importance_norm, 4),
        "consequence": item.consequence,
        "consequence_norm": round(consequence_norm, 4),
        "due_at": item.due_at,
        "urgency_context": urgency_context,
        "urgency_factor": round(urgency_factor, 4),
        "is_important_boost": is_important,
        "reputation_boost": round(rep_boost, 4),
        "effort": f"{item.effort_minutes:.0f} min" if item.effort_minutes else "unknown",
        "raw": round(raw, 4),
    }

    return ScoreResult(score=score, tier=_tier(score), reasons=reasons)
