"""Convert classified Gmail emails into deduplicated Items for the pool."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Sequence

from core.items import Item

ACTIONABLE_CATEGORIES = {"task", "deadline", "opportunity", "transaction", "event"}

_INVITE_RE = re.compile(r"^(invitation|invite)\s*:", re.IGNORECASE)

_TICKET_RE = re.compile(r"ticket\s*#?\s*(\d{5,})", re.IGNORECASE)
_ORDER_RE  = re.compile(r"order\s*#?\s*([\w-]{3,})", re.IGNORECASE)

# ── Informational signal patterns ────────────────────────────────────────────

# Order/package states that need no user action
_DELIVERED_RE = re.compile(
    r"\b(your order is here|package (has been )?delivered|order (has )?arrived|"
    r"delivered today|package is arriving|package arriving|"
    r"shipment is on the way|shipment from order|order tracking|"
    r"is on the way|on its way)\b",
    re.IGNORECASE,
)

# Pure confirmations — nothing to do (flexible word order)
_CONFIRMATION_RE = re.compile(
    r"\b(thank you for (registering|your)|"
    r"(order|purchase).{0,20}confirmed|confirmed.{0,20}(order|purchase)|"
    r"registration (is )?complete|order has been received|"
    r"your (account|email) (has been )?(confirmed|verified)|"
    r"welcome to)\b",
    re.IGNORECASE,
)

# ── Action signal patterns ────────────────────────────────────────────────────

_RESPONSE_RE = re.compile(
    r"\b(please respond|reply to this email|confirm.*amount|verify.*charge|"
    r"action required|respond by|you must|please confirm)\b",
    re.IGNORECASE,
)

# ── Date parsing ──────────────────────────────────────────────────────────────

_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DATE_RE = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|"
    r"november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)"
    r"\s+(\d{1,2})(?:st|nd|rd|th)?",
    re.IGNORECASE,
)
_TOMORROW_RE   = re.compile(r"\bdue tomorrow\b|\b\[due tomorrow\]", re.IGNORECASE)
_SLASH_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/\d{2,4})?\b")


def _parse_due_at(emails: list[dict]) -> str | None:
    today = datetime.now(timezone.utc)
    combined = " ".join(
        " ".join([
            e.get("deadline") or "",
            " ".join(e.get("action_items", [])),
            e.get("subject", ""),
            e.get("body", "")[:400],
        ])
        for e in emails
    )

    if _TOMORROW_RE.search(combined):
        due = (today + timedelta(days=1)).replace(hour=23, minute=59, second=0, microsecond=0)
        return due.isoformat().replace("+00:00", "Z")

    m = _DATE_RE.search(combined)
    if m:
        month = _MONTH_MAP.get(m.group(1).lower())
        day   = int(m.group(2))
        if month and 1 <= day <= 31:
            year = today.year
            if month < today.month or (month == today.month and day < today.day):
                year += 1
            try:
                due = datetime(year, month, day, 23, 59, tzinfo=timezone.utc)
                return due.isoformat().replace("+00:00", "Z")
            except ValueError:
                pass

    m = _SLASH_DATE_RE.search(combined)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            year = today.year
            if month < today.month or (month == today.month and day < today.day):
                year += 1
            try:
                due = datetime(year, month, day, 23, 59, tzinfo=timezone.utc)
                return due.isoformat().replace("+00:00", "Z")
            except ValueError:
                pass

    # Implicit urgency: if a response is required and no date found, assume 3 days
    requires_response = any(
        _RESPONSE_RE.search(e.get("subject", "") + " " + e.get("body", "")[:300])
        for e in emails
    )
    if requires_response:
        due = (today + timedelta(days=3)).replace(hour=23, minute=59, second=0, microsecond=0)
        return due.isoformat().replace("+00:00", "Z")

    return None


# ── Title generation ──────────────────────────────────────────────────────────

_STRIP_URL_RE    = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_STRIP_ENCODE_RE = re.compile(r"[A-Za-z0-9%]{10,}=[A-Za-z0-9%+/=]{10,}")
_STRIP_MD_RE     = re.compile(r"\*{1,2}(.+?)\*{1,2}")
_REAL_WORDS_RE   = re.compile(r"[a-zA-Z]{3,}")

_CATEGORY_VERB = {
    "transaction": "Action needed",
    "deadline":    "Complete",
    "opportunity": "Review",
}


def _clean(text: str) -> str:
    text = _STRIP_URL_RE.sub("", text)
    text = _STRIP_ENCODE_RE.sub("", text)
    text = _STRIP_MD_RE.sub(r"\1", text)  # strip **bold** markers
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _has_real_words(text: str, min_words: int = 4) -> bool:
    # Reject URL-encoded strings (heavy % or = characters near start)
    if text.count("%") > 2 or re.match(r"[A-Za-z0-9+/=%]{20,}", text):
        return False
    return len(_REAL_WORDS_RE.findall(text)) >= min_words


def _action_title(emails: list[dict], category: str) -> str:
    for email in reversed(emails):
        for raw in email.get("action_items", []):
            cleaned = _clean(raw)
            if len(cleaned) > 15 and _has_real_words(cleaned):
                return (cleaned[0].upper() + cleaned[1:])[:120]

    subject = _clean(emails[-1].get("subject", ""))
    verb    = _CATEGORY_VERB.get(category, "Review")
    return f"{verb}: {subject[:100]}"


# ── Informational check ───────────────────────────────────────────────────────

def _is_informational(emails: list[dict], category: str) -> bool:
    combined_subject = " ".join(e.get("subject", "") for e in emails)
    requires_response = any(
        _RESPONSE_RE.search(e.get("subject", "") + " " + e.get("body", "")[:300])
        for e in emails
    )

    # Confirmations are always informational (no pending user action)
    if _CONFIRMATION_RE.search(combined_subject):
        return True

    # Shipping/delivery updates with no required response
    if category == "transaction" and _DELIVERED_RE.search(combined_subject) and not requires_response:
        return True

    return False


# ── Scoring ───────────────────────────────────────────────────────────────────

def _derive_consequence(emails: list[dict], category: str) -> int:
    is_important      = any(e.get("is_important") for e in emails)
    has_action_items  = any(e.get("action_items") for e in emails)
    requires_response = any(
        _RESPONSE_RE.search(e.get("subject", "") + " " + e.get("body", "")[:300])
        for e in emails
    )
    has_deadline = any(e.get("deadline") for e in emails)

    score = 2
    if is_important:                                                   score += 1
    if requires_response or (category == "transaction" and has_action_items): score += 1
    if has_deadline:                                                   score += 1
    return min(score, 5)


def _derive_importance(emails: list[dict]) -> int:
    is_important     = any(e.get("is_important") for e in emails)
    has_action_items = any(e.get("action_items") for e in emails)
    score = 3
    if is_important:         score += 1
    if not has_action_items: score -= 1
    return max(1, min(score, 5))


def _derive_effort(category: str, emails: list[dict]) -> float:
    subjects = " ".join(e.get("subject", "") for e in emails)
    if re.search(r"\bapply\b", subjects, re.IGNORECASE):  return 60.0
    if re.search(r"\bsurvey\b", subjects, re.IGNORECASE): return 20.0
    return {"transaction": 5.0, "deadline": 15.0, "opportunity": 30.0}.get(category, 10.0)


# ── Build + group ─────────────────────────────────────────────────────────────

def _extract_action_key(email: dict) -> str | None:
    text = email.get("subject", "") + " " + email.get("body", "")[:500]
    m = _TICKET_RE.search(text)
    if m:
        return f"ticket-{m.group(1)}"
    m = _ORDER_RE.search(text)
    if m:
        return f"order-{m.group(1)}"
    return None


def _build_item(emails: list[dict], source_item_id: str, source: str) -> Item | None:
    category = emails[-1].get("category", "other")

    if _is_informational(emails, category):
        return None

    title = _action_title(emails, category)
    description = " | ".join(
        dict.fromkeys(
            _clean((e.get("snippet") or e.get("body", ""))[:150])
            for e in emails
            if (e.get("snippet") or e.get("body", ""))
        )
    )[:500] or None

    item_type = "event" if category == "event" else "task"

    return Item(
        source=source,
        source_item_id=source_item_id,
        item_type=item_type,
        title=title,
        description=description,
        importance=_derive_importance(emails),
        consequence=_derive_consequence(emails, category),
        effort_minutes=_derive_effort(category, emails),
        due_at=_parse_due_at(emails),
        raw_payload={
            "category":  category,
            "email_ids": [e.get("id") for e in emails],
            "gmail_tab": emails[-1].get("gmail_tab"),
        },
    )


def emails_to_items(emails: list[dict], source: str = "gmail_personal") -> list[Item]:
    """Convert classified email dicts into deduplicated, action-oriented Items."""
    # Promote calendar invites to event items regardless of Groq classification
    for e in emails:
        if _INVITE_RE.match(e.get("subject", "")) and e.get("category") not in ACTIONABLE_CATEGORIES:
            e["category"] = "event"

    actionable = [e for e in emails if e.get("category") in ACTIONABLE_CATEGORIES]

    groups: dict[str, list[dict]] = {}
    singletons: list[dict] = []

    for email in actionable:
        key = _extract_action_key(email)
        if key:
            groups.setdefault(key, []).append(email)
        else:
            singletons.append(email)

    items: list[Item] = []

    for action_key, group in groups.items():
        item = _build_item(group, action_key, source)
        if item:
            items.append(item)

    for email in singletons:
        item = _build_item([email], email["id"], source)
        if item:
            items.append(item)

    # Deduplicate by normalised title (catches duplicate digest emails)
    seen: set[str] = set()
    deduped: list[Item] = []
    for item in items:
        key = re.sub(r"\s+", " ", item.title.lower().strip())[:60]
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    return deduped
