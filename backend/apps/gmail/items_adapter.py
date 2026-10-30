"""Convert classified Gmail emails into deduplicated Items for the pool."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Sequence

from core.items import Item

ACTION_CATEGORIES = {"task", "event", "opportunity"}
DATA_CATEGORIES   = {"financial", "academic", "logistics", "identity", "social"}

# Maps Groq data category directly to record_type string
_DATA_RECORD_TYPE = {
    "financial": "financial",
    "academic":  "academic",
    "logistics": "logistics",
    "identity":  "identity",
    "social":    "social",
}

# Keep for backward-compat with cached emails.json that have old category names
_LEGACY_CATEGORY_MAP = {
    "transaction": "financial",
    "deadline":    "task",
    "other":       None,  # drop
}

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
    r"\b(thank you for (registering|your|signing|completing|submitting|participating)|"
    r"(order|purchase).{0,20}confirmed|confirmed.{0,20}(order|purchase)|"
    r"registration (is )?complete|order has been received|"
    r"your (account|email|information) (has been )?(confirmed|verified|updated)|"
    r"your (data|export|download|report) (is |are )?(ready|available|complete)|"
    r"successfully (updated|changed|modified|signed|submitted|completed)|"
    r"we received a request to (add|change|update|remove)|"
    r"welcome to)\b",
    re.IGNORECASE,
)

# Notifications that money is arriving or financial info is ready — no action required
_DEPOSIT_RE = re.compile(
    r"\b(will be deposited|you('ll| will) get paid|"
    r"sent you money with zelle|someone sent you money|"
    r"payment.{0,20}(deposited|on its way|is coming)|"
    r"statement(s)? (is|are) ready|monthly statement|"
    r"your (september|october|november|december|january|february|march|april|may|june|july|august) statements?|"
    r"disbursement was assigned|new loan disbursement|"
    r"your .{0,20}statement(s)? for)\b",
    re.IGNORECASE,
)

# ── Action signal patterns ────────────────────────────────────────────────────

_RESPONSE_RE = re.compile(
    r"\b(please respond|reply to this email|confirm.*amount|verify.*charge|"
    r"action required|respond by|you must|please confirm)\b",
    re.IGNORECASE,
)

# High-stakes actions that just became available — no explicit deadline yet
_HIGH_STAKES_AVAILABLE_RE = re.compile(
    r"\b(is now (available|open|live|accepting)|"
    r"form is (now )?(available|open)|"
    r"application(s)? (are |is )?(now )?(open|accepting|live)|"
    r"now accepting (applications?|submissions?)|"
    r"submit your (application|taxes|financial aid)|"
    r"(fafsa|financial aid|scholarship|grant|benefit|enrollment).{0,40}"
    r"(available|open|live|now))\b",
    re.IGNORECASE,
)

# OTPs and account-verification codes — never actionable tasks
_OTP_SUBJECT_RE = re.compile(
    r"\b(verification code|one.time (code|password|pin|passcode)|"
    r"\bOTP\b|security code|account verification|"
    r"confirm your (phone|email|account|identity)|"
    r"enter (this |your )?(code|pin))\b",
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
    is_event = any(e.get("category") == "event" for e in emails)
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
            if not is_event and (month < today.month or (month == today.month and day < today.day)):
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
            if not is_event and (month < today.month or (month == today.month and day < today.day)):
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


# ── Title generation ─────────────────────────────────────────────────────────

_STRIP_URL_RE    = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_STRIP_ENCODE_RE = re.compile(r"[A-Za-z0-9%]{10,}=[A-Za-z0-9%+/=]{10,}")
_STRIP_MD_RE     = re.compile(r"\*{1,2}(.+?)\*{1,2}")
_REAL_WORDS_RE   = re.compile(r"[a-zA-Z]{3,}")

_CATEGORY_VERB = {
    "opportunity": "Review",
    "task":        "Action",
    "event":       "Attend",
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


def _record_title(emails: list[dict], record_type: str) -> str:
    subject = _clean(emails[-1].get("subject", "")).strip()
    amounts = emails[-1].get("amounts", [])
    if record_type == "financial" and amounts:
        return f"{subject} — {' / '.join(amounts[:2])}"
    return subject[:120] or f"[{record_type}] record"


def _action_title(emails: list[dict], category: str) -> str:
    for email in reversed(emails):
        for raw in email.get("action_items", []):
            cleaned = _clean(raw)
            if len(cleaned) > 15 and _has_real_words(cleaned):
                return (cleaned[0].upper() + cleaned[1:])[:120]

    subject = _clean(emails[-1].get("subject", ""))
    is_important = any(e.get("is_important") for e in emails)
    verb = _CATEGORY_VERB.get(category, "Action" if is_important else "Review")
    return f"{verb}: {subject[:100]}"


# ── Informational check ───────────────────────────────────────────────────────

_BULK_DIGEST_RE = re.compile(
    r"(& your students|for you (and|&) your|handpicked.{0,20}for you|"
    r"\d+ (scholarships?|jobs?|internships?|opportunities?) for|"
    r"weekly digest|monthly digest|newsletter|"
    r"industry (security )?(advisory|disclosure)|"
    r"security (advisory|bulletin|disclosure)s?)",
    re.IGNORECASE,
)

_ACADEMIC_RECORD_RE = re.compile(
    r"\b(grade(s)? (posted|available|updated|released)|your (final |course )?grade|"
    r"gpa (update|report|summary)|transcript (ready|available|posted)|"
    r"score (posted|available|updated)|assignment (graded|scored|returned)|"
    r"midterm (grade|result)|final (grade|result|exam result))\b",
    re.IGNORECASE,
)


def _record_type(emails: list[dict], category: str) -> str | None:
    """Return a record_type string if this email group is data-only, else None (still actionable)."""
    combined_subject = " ".join(e.get("subject", "") for e in emails)
    combined_body    = " ".join(e.get("body", "")[:400] for e in emails)
    combined_all     = combined_subject + " " + combined_body

    requires_response = any(
        _RESPONSE_RE.search(e.get("subject", "") + " " + e.get("body", "")[:300])
        for e in emails
    )

    # Identity — OTPs and account-verification codes (subject-only to avoid false positives)
    if _OTP_SUBJECT_RE.search(combined_subject):
        return "identity"

    # Academic data — grade/score notifications (any school or platform)
    if _ACADEMIC_RECORD_RE.search(combined_all):
        return "academic"

    # Identity — account changes already completed, security confirmations
    if _CONFIRMATION_RE.search(combined_all):
        return "identity"

    # All informational transactions — any Groq "transaction" with no response needed
    if category == "transaction" and not requires_response:
        if _DELIVERED_RE.search(combined_subject):
            return "logistics"
        return "financial"

    # Financial — deposits, statements, informational payment notifications
    if (_DEPOSIT_RE.search(combined_all) or _DEPOSIT_RE.search(combined_subject)) and not requires_response:
        return "financial"

    # Bulk digests / newsletters — check subject and body
    if _BULK_DIGEST_RE.search(combined_all):
        return "financial"

    return None  # still actionable


# ── Scoring ───────────────────────────────────────────────────────────────────

def _derive_consequence(emails: list[dict], category: str) -> int:
    # Trust Groq's domain-knowledge consequence as the primary signal.
    # Heuristics nudge by at most ±1 so cumulative stacking can't override domain judgement.
    groq_consequence = emails[-1].get("consequence", 2)
    has_deadline = any(e.get("deadline") for e in emails)
    is_important = any(e.get("is_important") for e in emails)
    delta = 0
    if not has_deadline:                        delta -= 1   # missing deadline = less urgent
    if groq_consequence <= 2 and is_important:  delta += 1  # low-score item Gmail flagged
    return max(1, min(5, groq_consequence + delta))


def _derive_importance(emails: list[dict]) -> int:
    is_important     = any(e.get("is_important") for e in emails)
    has_action_items = any(e.get("action_items") for e in emails)
    avg_conf = sum(e.get("confidence", 0.5) for e in emails) / len(emails)

    score = 3
    if is_important:         score += 1
    if not has_action_items: score -= 1
    if avg_conf < 0.60:      score -= 1  # uncertain classification → less important

    # High-stakes actions that just became available (FAFSA, grants, scholarships, etc.)
    combined = " ".join(e.get("subject", "") + " " + e.get("body", "")[:300] for e in emails)
    if _HIGH_STAKES_AVAILABLE_RE.search(combined):
        score = max(score, 4)

    return max(1, min(score, 5))


def _derive_effort(category: str, emails: list[dict]) -> float:
    subjects = " ".join(e.get("subject", "") for e in emails)
    if re.search(r"\bapply\b", subjects, re.IGNORECASE):  return 60.0
    if re.search(r"\bsurvey\b", subjects, re.IGNORECASE): return 20.0
    return {"opportunity": 30.0}.get(category, 10.0)


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
    category = emails[-1].get("category", "noise")
    # Normalize legacy categories from cached emails.json
    category = _LEGACY_CATEGORY_MAP.get(category, category)
    if category is None:
        return None

    # Primary router: trust Groq's data classification directly;
    # fall back to regex _record_type() for action-labeled emails that are actually data
    rt = _DATA_RECORD_TYPE.get(category) or _record_type(emails, category)

    if rt is not None:
        description = " | ".join(
            dict.fromkeys(
                _clean((e.get("snippet") or e.get("body", ""))[:150])
                for e in emails
                if (e.get("snippet") or e.get("body", ""))
            )
        )[:500] or None
        return Item(
            source=source,
            source_item_id=source_item_id,
            item_type="record",
            status="archived",
            title=_record_title(emails, rt),
            description=description,
            importance=1,
            consequence=emails[-1].get("consequence", 1),
            effort_minutes=None,
            due_at=None,
            raw_payload={
                "record_type":  rt,
                "category":     category,
                "intent":       emails[-1].get("intent", "data"),
                "consequence":  emails[-1].get("consequence", 1),
                "email_ids":    [e.get("id") for e in emails],
                "amounts":      emails[-1].get("amounts", []),
                "merchant":     emails[-1].get("merchant"),
                "gmail_tab":    emails[-1].get("gmail_tab"),
                "is_important": any(e.get("is_important") for e in emails),
            },
        )

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
            "category":        category,
            "intent":          emails[-1].get("intent", "action"),
            "consequence":     emails[-1].get("consequence", 3),
            "urgency_context": emails[-1].get("urgency_context", "evergreen"),
            "email_ids":       [e.get("id") for e in emails],
            "gmail_tab":       emails[-1].get("gmail_tab"),
            "is_important":    any(e.get("is_important") for e in emails),
        },
    )


def emails_to_items(emails: list[dict], source: str = "gmail_personal") -> list[Item]:
    """Convert classified email dicts into deduplicated Items for the pool.

    Action items (task/event/opportunity) enter as open items for the Agenda.
    Data items (financial/academic/logistics/identity/social) enter as archived
    records for the Coach to learn from. Noise is discarded.
    """
    # Normalize legacy categories from cached emails.json
    for e in emails:
        legacy = _LEGACY_CATEGORY_MAP.get(e.get("category"))
        if legacy is not None:
            e["category"] = legacy

    # Promote calendar invites to event regardless of Groq classification
    for e in emails:
        if _INVITE_RE.match(e.get("subject", "")) and e.get("category") not in ACTION_CATEGORIES:
            e["category"] = "event"
            e["intent"]   = "action"

    # Include action AND data categories — noise is the only discard
    pooled = [e for e in emails if e.get("category") in ACTION_CATEGORIES | DATA_CATEGORIES]

    groups: dict[str, list[dict]] = {}
    singletons: list[dict] = []

    for email in pooled:
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
