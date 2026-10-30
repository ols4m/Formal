"""Show the ranked item pool — run from backend/.

    python apps/agenda/ranked_items.py              # show open ranked items
    python apps/agenda/ranked_items.py --ingest     # re-ingest emails.json first
    python apps/agenda/ranked_items.py all          # include D-tier items
    python apps/agenda/ranked_items.py done   <id>  # mark item done
    python apps/agenda/ranked_items.py defer  <id> <days>  # defer N days
    python apps/agenda/ranked_items.py dismiss <id> # archive (remove from view)
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND))

from core.db import init_db
from core.items import ItemRepository
from core.priority import score_item, Item


TIER_LABEL = {"S": "🔴 S", "A": "🟠 A", "B": "🟡 B", "C": "🟢 C", "D": "⚪ D"}
CATEGORY_ICON = {
    "task":        "✅",
    "event":       "📅",
    "opportunity": "🌟",
    "financial":   "💳",
    "academic":    "🎓",
    "logistics":   "📦",
    "identity":    "🔑",
    "social":      "💬",
}

_URGENCY_CONTEXT_LABEL = {
    "available_window": "[window open]",
    "hard_deadline":    "[hard deadline]",
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
    # Clear stale gmail action items before re-ingesting so old IDs don't linger
    repo._connection.execute(
        "DELETE FROM items WHERE source = 'gmail_personal' AND item_type != 'record'"
    )
    count = repo.upsert(items)
    print(f"  Ingested {count} item(s) from emails.json ({len(items)} unique actions)\n")
    return count


def show_ranked(repo: ItemRepository, show_all: bool = False) -> None:
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

    visible = [(s, t, i, r) for s, t, i, r in scored if show_all or t != "D"]
    hidden  = len(scored) - len(visible)

    now = datetime.now(timezone.utc)
    for score, tier, item, reasons in visible:
        payload  = item.raw_payload or {}
        category = payload.get("category", item.item_type)
        icon     = CATEGORY_ICON.get(category, "•")
        tier_str = TIER_LABEL.get(tier, tier)

        # Due date display
        if item.due_at:
            due = datetime.fromisoformat(item.due_at.replace("Z", "+00:00"))
            due_str = "overdue" if due < now else due.strftime("%Y-%m-%d")
        else:
            due_str = None

        # Urgency context label (skip evergreen — it's the default)
        ctx_label = _URGENCY_CONTEXT_LABEL.get(payload.get("urgency_context", ""), "")

        print(f"\n  {tier_str}  {icon}  {item.title[:72]}")
        meta = f"      id={item.id[:8]}  score={score:.2f}  imp={item.importance}  csq={item.consequence}  effort={item.effort_minutes or '?'}min  urgency={reasons['urgency_factor']:.2f}"
        if due_str:
            meta += f"  due={due_str}"
        if ctx_label:
            meta += f"  {ctx_label}"
        print(meta)
        if item.description:
            desc = item.description[:120]
            suffix = "..." if len(item.description) > 120 else ""
            print(f"      {desc}{suffix}")

    if hidden:
        print(f"\n  ({hidden} D-tier item(s) hidden — run with 'all' to see)")
    print("\n" + "=" * 70)


# ── Status subcommands ────────────────────────────────────────────────────────

def _get_title(repo: ItemRepository, item_id: str) -> str | None:
    row = repo._connection.execute(
        "SELECT title FROM items WHERE id=?", (item_id,)
    ).fetchone()
    return row["title"] if row else None


def cmd_done(repo: ItemRepository, args: list[str]) -> None:
    if not args:
        print("  Usage: ranked_items.py done <item-id>")
        sys.exit(1)
    item_id = args[0]
    title = _get_title(repo, item_id)
    if title is None:
        print(f"  Item not found: {item_id}")
        sys.exit(1)
    repo.update_status(item_id, "done")
    print(f"  [done] {title}")


def cmd_defer(repo: ItemRepository, args: list[str]) -> None:
    if len(args) < 2:
        print("  Usage: ranked_items.py defer <item-id> <days>")
        sys.exit(1)
    item_id, days_str = args[0], args[1]
    try:
        days = int(days_str)
        if days <= 0:
            raise ValueError
    except ValueError:
        print(f"  defer: <days> must be a positive integer, got {days_str!r}")
        sys.exit(1)
    title = _get_title(repo, item_id)
    if title is None:
        print(f"  Item not found: {item_id}")
        sys.exit(1)
    defer_until = (
        datetime.now(timezone.utc) + timedelta(days=days)
    ).replace(hour=23, minute=59, second=0, microsecond=0).isoformat().replace("+00:00", "Z")
    repo.update_status(item_id, "deferred", defer_until=defer_until)
    print(f"  [deferred +{days}d until {defer_until[:10]}] {title}")


def cmd_dismiss(repo: ItemRepository, args: list[str]) -> None:
    if not args:
        print("  Usage: ranked_items.py dismiss <item-id>")
        sys.exit(1)
    item_id = args[0]
    title = _get_title(repo, item_id)
    if title is None:
        print(f"  Item not found: {item_id}")
        sys.exit(1)
    repo.update_status(item_id, "archived")
    print(f"  [dismissed] {title}")


# ── Entry point ───────────────────────────────────────────────────────────────

_STATUS_CMDS = {"done": cmd_done, "defer": cmd_defer, "dismiss": cmd_dismiss}


def main() -> None:
    init_db()
    repo = ItemRepository()
    args = sys.argv[1:]
    try:
        if args and args[0] in _STATUS_CMDS:
            _STATUS_CMDS[args[0]](repo, args[1:])
        else:
            if "--ingest" in args:
                ingest_emails(repo)
            show_ranked(repo, show_all=("all" in args or "--all" in args))
    finally:
        repo.close()


if __name__ == "__main__":
    main()
