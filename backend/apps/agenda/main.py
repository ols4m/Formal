"""
The Agenda — CLI
Reads open tasks and events from the items pool, scores them with the Priority Engine,
and prints a ranked list.

Run: python apps/agenda/main.py
     python apps/agenda/main.py --all        (include done/archived)
     python apps/agenda/main.py --done ID    (mark an item done)
     python apps/agenda/main.py --dismiss ID (dismiss an item)
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


def _load_open_items(repo, show_all=False, conn=None):
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
        result = score_item(item, conn=conn)
        scored.append((item, result))

    scored.sort(key=lambda x: -x[1]["score"])
    return scored


def print_agenda(show_all=False):
    init_db()
    conn = get_db()
    repo = ItemRepository(connection=conn)
    archived = repo.archive_past_items()
    if archived:
        print(f"  Archived {archived} past item(s).")
    scored = _load_open_items(repo, show_all, conn=conn)

    # Default view: S/A/B tier only (actionable items)
    if not show_all:
        scored = [(item, res) for item, res in scored if res["tier"] in ("S", "A", "B")]

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
    tier_legend = "S=Critical  A=High  B=Medium  C=Low  D=Minimal"
    if not show_all:
        print(f"  {len(scored)} item(s)   {tier_legend}   (use --all for C/D)")
    else:
        print(f"  {len(scored)} item(s)   {tier_legend}")
    print("=" * W)
    print()


def _resolve_item_id(item_id, conn):
    """Resolve an item_id (UUID or row number) to (real_id, title). Returns (None, None) if not found."""
    # Try direct UUID match
    row = conn.execute("SELECT id, title, raw_payload FROM items WHERE id = ?", (item_id,)).fetchone()
    if row:
        return row["id"], row["title"], json.loads(row["raw_payload"])

    # Try row number match
    if item_id.isdigit():
        idx = int(item_id) - 1
        repo = ItemRepository(connection=conn)
        scored = _load_open_items(repo, show_all=False, conn=conn)
        if 0 <= idx < len(scored):
            item = scored[idx][0]
            return item.id, item.title, item.raw_payload
    return None, None, None


def _update_sender_reputation(conn, payload, column):
    """Increment done_count or dismiss_count for the sender domain."""
    domain = (payload or {}).get("sender_domain")
    if not domain:
        return
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(f"""
        INSERT INTO sender_reputation (domain, {column}, updated_at)
        VALUES (?, 1, ?)
        ON CONFLICT(domain) DO UPDATE SET
            {column} = {column} + 1,
            updated_at = ?
    """, (domain, now, now))


def mark_done(item_id):
    init_db()
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    real_id, title, payload = _resolve_item_id(item_id, conn)
    if real_id is None:
        print(f"  Item {item_id} not found.")
        conn.close()
        return
    with conn:
        conn.execute(
            "UPDATE items SET status = 'done', updated_at = ? WHERE id = ?",
            (now, real_id),
        )
        _update_sender_reputation(conn, payload, "done_count")
    print(f"  Done: {title[:60]}")
    conn.close()


def mark_dismissed(item_id):
    init_db()
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    real_id, title, payload = _resolve_item_id(item_id, conn)
    if real_id is None:
        print(f"  Item {item_id} not found.")
        conn.close()
        return
    with conn:
        conn.execute(
            "UPDATE items SET status = 'archived', updated_at = ? WHERE id = ?",
            (now, real_id),
        )
        _update_sender_reputation(conn, payload, "dismiss_count")
    print(f"  Dismissed: {title[:60]}")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="The Agenda — ranked task list from the items pool")
    parser.add_argument("--all", action="store_true", help="Include done/archived items and all tiers")
    parser.add_argument("--done", metavar="ID", help="Mark an item done (by ID or row number)")
    parser.add_argument("--dismiss", metavar="ID", help="Dismiss an item (archive + record sender reputation)")
    args = parser.parse_args()

    if args.done:
        mark_done(args.done)
    elif args.dismiss:
        mark_dismissed(args.dismiss)
    else:
        print_agenda(show_all=args.all)
