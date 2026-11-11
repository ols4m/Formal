"""
Future labeling patterns — not used in the gate pipeline.

These functions and patterns were removed from the gate because the gate's
job is only pool-vs-noise. They're preserved here for the downstream labeling
step that will assign categories (task, event, financial, etc.) to pooled items.

Functions:
  _structural_classify()  — regex-based category detection (high precision)
  _unclassified_fallback() — signal-based fallback (action words, newsletter tier)
  _make_record()          — personal email detection (no List-Unsubscribe)
  _INVITE_RE              — calendar invite detection ("Invitation: ...")

The regex patterns themselves live in items_adapter.py:
  _COMPLETED_RE, _STATUS_UPDATE_RE, _CONFIRMATION_RE, _OTP_SUBJECT_RE,
  _ACADEMIC_RECORD_RE, _DEPOSIT_RE, _DELIVERED_RE, _BULK_DIGEST_RE,
  _RESPONSE_RE, _HIGH_STAKES_AVAILABLE_RE

Category mapping (what each regex was detecting):
  _COMPLETED_RE        → logistics   (completed, restored, back online)
  _STATUS_UPDATE_RE    → logistics   (system maintenance, outage)
  _CONFIRMATION_RE     → identity    (thank you for registering, order confirmed)
  _OTP_SUBJECT_RE      → identity    (verification code, OTP)
  _ACADEMIC_RECORD_RE  → academic    (grades posted, transcript ready)
  _DEPOSIT_RE          → financial   (will be deposited, statement ready)
  _DELIVERED_RE        → logistics   (order delivered, package arriving)
  _BULK_DIGEST_RE      → social      (weekly digest, newsletter, scholarships)
  _INVITE_RE           → event       (Invitation: ... @ date)

Signal-based patterns:
  _SUBJECT_ACTION_RE   → task        (action required, confirm, reminder, due)
  has_list_unsubscribe + bulk tab → social (newsletter/digest)
  no list_unsubscribe  → identity    (personal/transactional)
"""

import re

# Calendar invite detection — moved from items_adapter.py gate logic
_INVITE_RE = re.compile(r"^(invitation|invite)\s*:", re.IGNORECASE)
