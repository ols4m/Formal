"""Coach v0 — daily focus question.

Fetches the top-ranked open action items and recent data records, then asks
Groq: "What should {owner} focus on today, and why?"

Run from backend/:
    python apps/coach/main.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv
load_dotenv(BACKEND / ".env")

from core.db import init_db
from core.items import Item, ItemRepository
from core.priority import score_item

# ── Config ────────────────────────────────────────────────────────────────────

TOP_N_ACTIONS = 10   # ranked open items to feed the Coach
TOP_N_RECORDS = 20   # recent archived data records for life context
GROQ_MODEL = os.getenv("GROQ_COACH_MODEL", "llama-3.3-70b-versatile")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_owner() -> str:
    profile_path = BACKEND / "profile.json"
    if profile_path.exists():
        try:
            with profile_path.open() as f:
                p = json.load(f)
            return p.get("owner_name") or p.get("name") or "the user"
        except Exception:
            pass
    return "the user"


def _ranked_actions(repo: ItemRepository) -> list[tuple[float, str, Item]]:
    rows = repo.list_items(status="open", limit=50)
    now  = datetime.now(timezone.utc)
    scored = []
    for row in rows:
        item = Item(
            id=row["id"],
            source=row["source"],
            source_item_id=row.get("source_item_id"),
            item_type=row["item_type"],
            title=row["title"],
            description=row.get("description"),
            due_at=row.get("due_at"),
            importance=row["importance"],
            consequence=row["consequence"],
            effort_minutes=row.get("effort_minutes"),
            status=row["status"],
            raw_payload=row.get("raw_payload") or {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        result = score_item(item, now=now)
        scored.append((result["score"], result["tier"], item))
    scored.sort(reverse=True, key=lambda x: x[0])
    return scored[:TOP_N_ACTIONS]


def _recent_records(repo: ItemRepository) -> list[dict]:
    rows = repo.list_items(status="archived", limit=TOP_N_RECORDS)
    return rows


def _format_actions(actions: list[tuple[float, str, Item]]) -> str:
    if not actions:
        return "  (none)"
    lines = []
    for score, tier, item in actions:
        due = item.due_at[:10] if item.due_at else "no deadline"
        lines.append(
            f"  [{tier}] {item.title[:80]}\n"
            f"       score={score:.2f}  due={due}  "
            f"importance={item.importance}  consequence={item.consequence}"
        )
    return "\n".join(lines)


def _format_records(records: list[dict]) -> str:
    if not records:
        return "  (none)"
    lines = []
    for r in records:
        payload = r.get("raw_payload") or {}
        rtype   = payload.get("record_type", r.get("item_type", "record"))
        lines.append(f"  [{rtype}] {r['title'][:80]}")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        print("Error: GROQ_API_KEY not set in backend/.env")
        sys.exit(1)

    init_db()
    repo  = ItemRepository()
    owner = _load_owner()
    today = datetime.now(timezone.utc).strftime("%A, %B %-d, %Y")

    try:
        actions = _ranked_actions(repo)
        records = _recent_records(repo)
    finally:
        repo.close()

    action_context = _format_actions(actions)
    record_context = _format_records(records)

    system_prompt = (
        f"You are Formal, {owner}'s personal AI life strategist. "
        "Your role is advisory only — recommend, never act. "
        f"You see {owner}'s current action queue and recent life data records. "
        "Be direct, concise, and strategic. Surface non-obvious leverage and risks. "
        "Ground every recommendation in the specific items shown — no generic advice."
    )

    user_prompt = f"""Today is {today}.

=== TOP ACTION ITEMS (ranked by Priority Engine) ===
{action_context}

=== RECENT LIFE DATA (financial, academic, logistics, identity) ===
{record_context}

What should {owner} focus on today, and why? Be specific and strategic. \
Keep the response under 300 words."""

    from groq import Groq
    client = Groq(api_key=api_key)

    print(f"  Asking Coach ({GROQ_MODEL})...\n")
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.4,
        max_tokens=512,
    )

    advice = response.choices[0].message.content.strip()
    print("=" * 70)
    print(f"  FORMAL COACH — {today}")
    print("=" * 70)
    print()
    print(advice)
    print()
    print("=" * 70)


if __name__ == "__main__":
    main()
