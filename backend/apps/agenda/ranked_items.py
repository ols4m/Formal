"""Show the ranked item pool — run from backend/.

    python apps/agenda/ranked_items.py              # show open ranked items
    python apps/agenda/ranked_items.py all          # include D-tier items
    python apps/agenda/ranked_items.py done   <id>  # mark item done
    python apps/agenda/ranked_items.py defer  <id> <days>  # defer N days
    python apps/agenda/ranked_items.py dismiss <id> # archive (remove from view)
"""

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


def show_ranked(repo: ItemRepository, show_all: bool = False) -> None:
    repo.reopen_deferred_items()
    rows = repo.list_items(status="open", limit=None)
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

def _resolve(repo: ItemRepository, id_or_prefix: str) -> tuple[str | None, str | None]:
    """Resolve a full item ID or the short ID this view prints (a unique prefix)."""
    rows = repo._connection.execute(
        "SELECT id, title FROM items WHERE id = ? OR id LIKE ? LIMIT 2",
        (id_or_prefix, id_or_prefix.replace("%", "").replace("_", "") + "%"),
    ).fetchall()
    exact = [r for r in rows if r["id"] == id_or_prefix]
    if exact:
        return exact[0]["id"], exact[0]["title"]
    if len(rows) == 1:
        return rows[0]["id"], rows[0]["title"]
    if len(rows) > 1:
        print(f"  Ambiguous ID {id_or_prefix!r}: use more characters.")
    return None, None


def cmd_done(repo: ItemRepository, args: list[str]) -> None:
    if not args:
        print("  Usage: ranked_items.py done <item-id>")
        sys.exit(1)
    item_id, title = _resolve(repo, args[0])
    if title is None:
        print(f"  Item not found: {args[0]}")
        sys.exit(1)
    repo.update_status(item_id, "done")
    repo.record_sender_feedback(item_id, "done")
    print(f"  [done] {title}")


def cmd_defer(repo: ItemRepository, args: list[str]) -> None:
    if len(args) < 2:
        print("  Usage: ranked_items.py defer <item-id> <days>")
        sys.exit(1)
    days_str = args[1]
    try:
        days = int(days_str)
        if days <= 0:
            raise ValueError
    except ValueError:
        print(f"  defer: <days> must be a positive integer, got {days_str!r}")
        sys.exit(1)
    item_id, title = _resolve(repo, args[0])
    if title is None:
        print(f"  Item not found: {args[0]}")
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
    item_id, title = _resolve(repo, args[0])
    if title is None:
        print(f"  Item not found: {args[0]}")
        sys.exit(1)
    repo.update_status(item_id, "archived")
    repo.record_sender_feedback(item_id, "dismiss")
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
            show_ranked(repo, show_all=("all" in args or "--all" in args))
    finally:
        repo.close()


if __name__ == "__main__":
    main()
