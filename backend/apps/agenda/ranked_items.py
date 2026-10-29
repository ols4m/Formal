"""Show the ranked item pool — run from backend/.

    python apps/agenda/ranked_items.py
    python apps/agenda/ranked_items.py --ingest   # also pulls latest emails.json into pool
"""

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND))

from core.db import init_db
from core.items import ItemRepository
from core.priority import score_item, Item


TIER_LABEL = {"S": "🔴 S", "A": "🟠 A", "B": "🟡 B", "C": "🟢 C", "D": "⚪ D"}
CATEGORY_ICON = {
    "transaction": "💳",
    "deadline":    "⏰",
    "opportunity": "🌟",
    "task":        "✅",
}


def ingest_emails(repo: ItemRepository) -> int:
    from apps.gmail.items_adapter import emails_to_items
    emails_path = BACKEND / "backend" / "output" / "emails.json"
    if not emails_path.exists():
        emails_path = BACKEND / "output" / "emails.json"
    with emails_path.open() as f:
        data = json.load(f)
    emails = data.get("emails", []) if isinstance(data, dict) else data
    items = emails_to_items(emails)
    # Clear stale gmail items before re-ingesting so old IDs don't linger
    repo._connection.execute("DELETE FROM items WHERE source = 'gmail_personal'")
    count = repo.upsert(items)
    print(f"  Ingested {count} item(s) from emails.json ({len(items)} unique actions)\n")
    return count


def show_ranked(repo: ItemRepository) -> None:
    rows = repo.list_items(status="open", limit=50)
    if not rows:
        print("  No open items in the pool.")
        return

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
        result = score_item(item)
        scored.append((result["score"], result["tier"], item, result["reasons"]))

    scored.sort(reverse=True, key=lambda x: x[0])

    print("=" * 70)
    print("  FORMAL — RANKED ACTIONS")
    print("=" * 70)

    for score, tier, item, reasons in scored:
        payload  = item.raw_payload or {}
        category = payload.get("category", item.item_type)
        icon     = CATEGORY_ICON.get(category, "•")
        tier_str = TIER_LABEL.get(tier, tier)

        print(f"\n  {tier_str}  {icon}  {item.title[:70]}")
        print(f"      score={score:.2f}  importance={item.importance}  "
              f"consequence={item.consequence}  "
              f"effort={item.effort_minutes or '?'}min  "
              f"urgency={reasons['urgency_factor']:.2f}")
        if item.description:
            print(f"      {item.description[:100]}")

    print("\n" + "=" * 70)


def main() -> None:
    init_db()
    repo = ItemRepository()
    try:
        if "--ingest" in sys.argv:
            ingest_emails(repo)
        show_ranked(repo)
    finally:
        repo.close()


if __name__ == "__main__":
    main()
