"""
The Agenda — CLI
Reads open tasks and events from the items pool, scores them with the Priority Engine,
and prints a ranked list.

Run: python apps/agenda/main.py
     python apps/agenda/main.py --all        (include done/archived)
     python apps/agenda/main.py --done ID    (mark an item done)
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import date, datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from core.db import get_db, init_db
from core.items import Item, ItemRepository
from core.priority import score_item

TIER_LABEL = {"S": "S", "A": "A", "B": "B", "C": "C", "D": "D"}

W = 80


def _load_open_items(repo, show_all=False):
    """Load items from the pool and score them."""
    if show_all:
        rows = repo.list_items(status=None, limit=200)
    else:
        rows = repo.list_items(status="open", limit=200)

    # Only tasks and events for the agenda
    rows = [r for r in rows if r["item_type"] in ("task", "event")]

    scored = []
    for r in rows:
        item = Item(
            id=r["id"], source=r["source"], source_item_id=r["source_item_id"],
            item_type=r["item_type"], title=r["title"], description=r["description"],
            due_at=r["due_at"], importance=r["importance"],
            effort_minutes=r["effort_minutes"], consequence=r["consequence"],
            status=r["status"], raw_payload=r["raw_payload"],
            created_at=r["created_at"], updated_at=r["updated_at"],
        )
        result = score_item(item)
        scored.append((item, result))

    scored.sort(key=lambda x: -x[1]["score"])
    return scored


def print_agenda(show_all=False):
    init_db()
    repo = ItemRepository()
    scored = _load_open_items(repo, show_all)
    repo.close()

    if not scored:
        print("  No items. Run the Gmail scraper first.")
        return

    print()
    print("=" * W)
    print(f"  THE AGENDA — {date.today()}")
    print("=" * W)
    print(f"  {'#':>3}  {'TIER':<4}  {'SCORE':>5}  {'DUE':<12}  {'TYPE':<6}  TITLE")
    print("  " + "-" * (W - 2))

    for i, (item, res) in enumerate(scored, 1):
        due = (item.due_at or "")[:10] or "-"
        tier = TIER_LABEL[res["tier"]]
        score = f"{res['score']:.2f}"
        itype = item.item_type[:6]
        title = item.title[:45]
        status_tag = f" [{item.status}]" if item.status != "open" else ""
        print(f"  {i:>3}  [{tier}]  {score:>5}  {due:<12}  {itype:<6}  {title}{status_tag}")

    print("  " + "-" * (W - 2))
    print(f"  {len(scored)} item(s)   S=Critical  A=High  B=Medium  C=Low  D=Minimal")
    print("=" * W)
    print()


def mark_done(item_id):
    init_db()
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        cursor = conn.execute(
            "UPDATE items SET status = 'done', updated_at = ? WHERE id = ?",
            (now, item_id),
        )
    if cursor.rowcount:
        print(f"  Done: {item_id}")
    else:
        # Try matching by row number prefix
        rows = conn.execute(
            "SELECT id, title FROM items WHERE status = 'open' AND item_type IN ('task', 'event')"
        ).fetchall()
        if item_id.isdigit():
            idx = int(item_id) - 1
            repo = ItemRepository(connection=conn)
            scored = _load_open_items(repo, show_all=False)
            if 0 <= idx < len(scored):
                real_id = scored[idx][0].id
                with conn:
                    conn.execute(
                        "UPDATE items SET status = 'done', updated_at = ? WHERE id = ?",
                        (now, real_id),
                    )
                print(f"  Done: {scored[idx][0].title[:60]}")
            else:
                print(f"  Item #{item_id} not found.")
        else:
            print(f"  Item {item_id} not found.")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="The Agenda — ranked task list from the items pool")
    parser.add_argument("--all", action="store_true", help="Include done/archived items")
    parser.add_argument("--done", metavar="ID", help="Mark an item done (by ID or row number)")
    args = parser.parse_args()

    if args.done:
        mark_done(args.done)
    else:
        print_agenda(show_all=args.all)
