"""
Gmail Scraper — two-mode email intake for Formal.

School email  → Playwright (Google SSO, API blocked by school admin)
Personal email → Gmail API (OAuth2, requires credentials.json)

Classified emails → output/emails.json
Deadline / assignment emails → inserted into tasks table in formal.db
"""

import asyncio
import base64
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
load_dotenv(Path(__file__).parent.parent.parent / '.env')
from core.db import get_db, init_db

os.makedirs('output', exist_ok=True)
OUTPUT_PATH = 'output/emails.json'
GMAIL_URL   = 'https://mail.google.com/'

# Gmail OAuth tokens/credentials (gitignored)
TOKEN_PATH        = Path(__file__).parent / 'token.json'
TOKEN_PATH_SCHOOL = Path(__file__).parent / 'token_school.json'
CREDS_PATH        = Path(__file__).parent / 'credentials.json'
SCOPES            = ['https://www.googleapis.com/auth/gmail.readonly']


# ==========================================
# CLASSIFIER
# ==========================================

_DATE_PATTERN = re.compile(
    r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)'
    r'\s+\d{1,2}(?:,\s*\d{4})?\b'
    r'|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b',
    re.IGNORECASE
)

_FINANCIAL_PATTERN = re.compile(
    r'\b(paid|stipend|payment|scholarship|financial|money|dollars|\$|compensation)\b',
    re.IGNORECASE
)

# Dollar amounts: $12.34, $1,200, $500
_DOLLAR_PATTERN = re.compile(r'\$[\d,]+(?:\.\d{1,2})?')

# Merchant name after "at", "from", "to", or labeled "merchant:"
_MERCHANT_PATTERN = re.compile(
    r'(?:(?:at|from|to)\s+([A-Z][A-Za-z0-9\s&\'.\-]{2,28})'
    r'|(?:merchant|vendor)[:\s]+([A-Za-z0-9\s&\'.\-]{2,28}))',
    re.IGNORECASE
)

# Sentences/phrases that signal something requires the user's attention
_ACTION_PATTERN = re.compile(
    r'[^.!?\n]*'
    r'(?:please\s+(?:reply|respond|confirm|review|sign|submit|complete|fill\s+out|click|register|pay|schedule)'
    r'|action\s+(?:required|needed)'
    r'|response\s+(?:required|needed)'
    r'|click\s+(?:here|below|the\s+link)'
    r'|register\s+(?:now|by|before)'
    r'|submit\s+(?:your|by|before)'
    r'|confirm\s+(?:your|receipt|attendance)'
    r'|rsvp\s+by'
    r'|apply\s+(?:now|by)'
    r'|don\'t\s+(?:miss|forget)'
    r'|deadline[:\s]'
    r'|due\s+(?:date)?[:\s]'
    r'|reminder[:\s])'
    r'[^.!?\n]*[.!?]?',
    re.IGNORECASE
)

ROUTES = {
    'task':        ['agenda'],
    'event':       ['agenda'],
    'opportunity': ['agenda'],
    'financial':   ['checkbook'],
    'academic':    ['gradebook'],
    'logistics':   [],
    'identity':    [],
    'social':      [],
    'noise':       [],
}


def extract_amounts(text: str) -> list[str]:
    """Pull unique dollar amounts found in the text."""
    seen, out = set(), []
    for m in _DOLLAR_PATTERN.findall(text):
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def extract_merchant(text: str) -> str | None:
    """Best-effort merchant name from a transaction email."""
    m = _MERCHANT_PATTERN.search(text)
    if not m:
        return None
    name = (m.group(1) or m.group(2) or '').strip()
    return name if len(name) > 2 else None


def extract_action_items(body: str) -> list[str]:
    """Return sentences that contain an explicit call to action."""
    hits = _ACTION_PATTERN.findall(body[:1500])
    seen, items = set(), []
    for h in hits:
        h = h.strip()
        if h and h not in seen and len(h) > 10:
            seen.add(h)
            items.append(h)
    return items[:5]  # cap at 5 so it doesn't explode


# ── Groq classifier ───────────────────────────────────────────────────────────

_AUTO_NOISE_TABS = {'promotions', 'social'}

# Subject-line signals that indicate an action even on transactional (no List-Unsubscribe) emails.
# Prevents the record shortcut from swallowing actionable emails.
_SUBJECT_ACTION_RE = re.compile(
    r'(?:action\s+(?:required|needed|advised)'
    r'|confirm\s+(?:your|the|a)'
    r'|(?:please|kindly)\s+(?:confirm|verify|reply|respond|review|sign|submit|complete)'
    r'|pending\s+(?:request|action|review)'
    r'|reminder:'
    r'|new message from'
    r'|\[(?:action|due|reminder))',
    re.IGNORECASE
)

# ── Engagement index ──────────────────────────────────────────────────────────

_ENGAGEMENT_CACHE_PATH = Path('output/engagement_cache.json')
_ENGAGEMENT_TTL_HOURS  = 24


def _sender_domain(address: str) -> str:
    """Extract the domain from a From: address like 'Name <user@domain.com>'."""
    m = re.search(r'@([\w.\-]+)', address)
    return m.group(1).lower() if m else address.lower().strip()


def build_engagement_index(service) -> dict:
    """
    Build a sender-domain engagement signal from Gmail history.

    Returns a dict:
      {
        'replied_domains':  set of domains the user has sent mail to,
        'opened_domains':   {domain: open_count} for inbox messages not from self,
        'built_at':         ISO timestamp,
      }

    Cached to disk for _ENGAGEMENT_TTL_HOURS hours so it's not rebuilt every run.
    """
    os.makedirs('output', exist_ok=True)

    # Return cached index if fresh
    if _ENGAGEMENT_CACHE_PATH.exists():
        try:
            cached = json.loads(_ENGAGEMENT_CACHE_PATH.read_text())
            built  = datetime.fromisoformat(cached['built_at'])
            if datetime.now(timezone.utc) - built < timedelta(hours=_ENGAGEMENT_TTL_HOURS):
                cached['replied_domains'] = set(cached['replied_domains'])
                cached['opened_domains']  = defaultdict(int, cached['opened_domains'])
                return cached
        except Exception:
            pass

    print('  Building engagement index from Gmail history...')

    replied_domains: set              = set()
    opened_domains:  defaultdict[str, int] = defaultdict(int)

    # ── Signal 1: domains you've replied to (sent mail) ──────────────────────
    try:
        sent_results = service.users().messages().list(
            userId='me', q='in:sent', maxResults=500
        ).execute()
        for msg in sent_results.get('messages', []):
            try:
                meta = service.users().messages().get(
                    userId='me', id=msg['id'], format='metadata',
                    metadataHeaders=['To']
                ).execute()
                to_header = next(
                    (h['value'] for h in meta['payload']['headers'] if h['name'] == 'To'),
                    ''
                )
                for addr in to_header.split(','):
                    d = _sender_domain(addr.strip())
                    if d:
                        replied_domains.add(d)
            except Exception:
                continue
    except Exception as e:
        print(f'  ⚠️  Could not fetch sent mail: {e}')

    # ── Signal 2: inbox open proxy — messages kept in INBOX by sender domain ──
    # Heuristic: if a message is in INBOX (and not a bulk tab) it was at minimum
    # seen and not immediately archived/trashed — a weak "opened" signal.
    # Exclude promotions/social/forums so opening a Facebook digest to investigate
    # doesn't accidentally credit facebookmail.com.
    _bulk_exclusion = ' '.join(f'-category:{t}' for t in _AUTO_NOISE_TABS)
    try:
        inbox_results = service.users().messages().list(
            userId='me', q=f'in:inbox {_bulk_exclusion}', maxResults=500
        ).execute()
        for msg in inbox_results.get('messages', []):
            try:
                meta = service.users().messages().get(
                    userId='me', id=msg['id'], format='metadata',
                    metadataHeaders=['From']
                ).execute()
                from_header = next(
                    (h['value'] for h in meta['payload']['headers'] if h['name'] == 'From'),
                    ''
                )
                d = _sender_domain(from_header)
                if d:
                    opened_domains[d] += 1
            except Exception:
                continue
    except Exception as e:
        print(f'  ⚠️  Could not fetch inbox history: {e}')

    index = {
        'replied_domains': list(replied_domains),
        'opened_domains':  dict(opened_domains),
        'built_at':        datetime.now(timezone.utc).isoformat(),
    }
    _ENGAGEMENT_CACHE_PATH.write_text(json.dumps(index, indent=2))
    print(f'  Engagement index: {len(replied_domains)} reply domains, '
          f'{len(opened_domains)} inbox domains cached.')

    index['replied_domains'] = replied_domains
    index['opened_domains']  = defaultdict(int, opened_domains)
    return index


def _is_low_engagement(sender: str, index: dict | None) -> bool:
    """
    Return True if the sender's domain shows no behavioral engagement signal.

    High engagement (returns False — let it through):
      - User has ever replied to this domain (strong signal)
      - User has kept ≥2 messages from this domain in inbox (moderate signal)

    Low engagement (returns True — candidate for auto-noise):
      - No replies AND fewer than 2 inbox appearances
    """
    if index is None:
        return False  # no data → don't suppress

    domain = _sender_domain(sender)
    if not domain:
        return False

    if domain in index.get('replied_domains', set()):
        return False

    opened = index.get('opened_domains', {})
    if opened.get(domain, 0) >= 2:
        return False

    return True


# ── Sender frequency index ────────────────────────────────────────────────────

_FREQUENCY_THRESHOLD = 2  # domains with ≤ this many emails in history are "rare"


def _build_frequency_index(history_path: str = 'output/emails_history.json') -> dict[str, int]:
    """Count emails per sender domain from local history. No API calls."""
    counts: dict[str, int] = defaultdict(int)
    try:
        with open(history_path, encoding='utf-8') as f:
            history = json.load(f)
        for email in history.get('emails', []):
            d = _sender_domain(email.get('from', ''))
            if d:
                counts[d] += 1
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return dict(counts)


def _is_rare_sender(sender: str, frequency_index: dict[str, int]) -> bool:
    """Return True if the sender domain has appeared ≤ _FREQUENCY_THRESHOLD times in history."""
    domain = _sender_domain(sender)
    return frequency_index.get(domain, 0) <= _FREQUENCY_THRESHOLD


VALID_CATEGORIES = {
    # action
    'task', 'event', 'opportunity',
    # data
    'financial', 'academic', 'logistics', 'identity', 'social',
    # legacy aliases — remapped at parse time
    'transaction', 'other',
    # discard
    'noise',
}


def _make_noise(email: dict, stage: str = 'tab_noise') -> dict:
    return {
        **email,
        'category': 'noise', 'intent': 'noise', 'confidence': 0.95,
        'consequence': 1, 'urgency_context': 'evergreen',
        'summary': None, 'deadline': None,
        'financial_impact': False, 'amounts': [], 'merchant': None,
        'action_items': [], 'routes_to': [],
        '_filter_stage': stage,
    }


def _make_record(email: dict) -> dict:
    """Build a classification dict for a personal record that bypasses Groq."""
    body = email.get('body', '')
    full = email.get('subject', '') + ' ' + body
    return {
        **email,
        'category': 'identity', 'intent': 'data', 'confidence': 1.0,
        'consequence': 3, 'urgency_context': 'evergreen',
        'summary': None, 'deadline': None,
        'financial_impact': bool(_FINANCIAL_PATTERN.search(full)),
        'amounts': extract_amounts(full),
        'merchant': extract_merchant(full),
        'action_items': [], 'routes_to': [],
        '_filter_stage': 'record',
    }


def _make_result(email: dict, category: str, intent: str, confidence: float,
                 consequence: int, urgency_context: str,
                 summary: str | None, deadline: str | None) -> dict:
    body = email.get('body', '')
    full = email.get('subject', '') + ' ' + body
    return {
        **email,
        'category':        category,
        'intent':          intent,
        'confidence':      confidence,
        'consequence':     consequence,
        'urgency_context': urgency_context,
        'summary':         summary,
        'deadline':        deadline,
        'financial_impact': bool(_FINANCIAL_PATTERN.search(full)),
        'amounts':         extract_amounts(full),
        'merchant':        extract_merchant(full) if category == 'financial' else None,
        'action_items':    extract_action_items(body),
        'routes_to':       ROUTES.get(category, []),
        '_filter_stage':   'groq',
    }


def _classify_one(email: dict, client,
                   engagement_index: dict | None = None,
                   frequency_index: dict[str, int] | None = None) -> dict:
    """Classify a single email through the filter pipeline.

    Pipeline (first match wins):
      1. Tab auto-noise   — promotions/social → dropped
      2. No List-Unsubscribe → personal record → items pool (skip Groq)
      3. Updates/forums gate — is_important=False + high-freq + low-engagement → dropped
      4. Everything else   → Groq classifier
    """
    tab     = email.get('gmail_tab', 'unknown')
    subject = email.get('subject', '')
    sender  = email.get('from', '')
    body    = (email.get('body') or '')[:1500]

    # ── 1. Tab auto-noise: promotions/social ───────────────────────────────
    if tab in _AUTO_NOISE_TABS:
        return _make_noise(email, stage='tab_noise')

    # ── 2. No List-Unsubscribe → 1:1 personal record (skip Groq) ────────────
    # CAN-SPAM requires bulk senders to include this header. Its absence means
    # a transactional/personal email: receipts, security alerts, account events.
    # Default True for backward compat with older emails missing the field.
    has_unsub = email.get('has_list_unsubscribe', True)
    if not has_unsub and not _SUBJECT_ACTION_RE.search(subject):
        return _make_record(email)

    # ── 3. Updates/forums gate: behavioral signals for mass-sent mail ───────
    is_important = email.get('is_important', False)
    if tab in ('updates', 'forums') and not is_important:
        rare = _is_rare_sender(sender, frequency_index or {})
        engaged = not _is_low_engagement(sender, engagement_index)
        if not rare and not engaged:
            return _make_noise(email, stage='updates_noise')

    importance_line = (
        "★ IMPORTANT — Gmail flagged this as significant based on the user's own email behavior."
        if is_important else
        "normal (Gmail did not flag as important)"
    )

    engaged = not _is_low_engagement(sender, engagement_index)
    engagement_line = (
        "yes — user has replied to or repeatedly opened mail from this sender domain"
        if engaged else
        "no — sender domain has no reply or open history"
    )

    prompt = f"""You are classifying one email for a personal life OS. Return ONLY a JSON object — no markdown, no extra text.

Gmail signals:
  importance: {importance_line}
  tab: {tab}
  engagement: {engagement_line}

Email:
  From: {sender[:80]}
  Subject: {subject[:120]}
  Body: {body}

Step 1 — Is this DATA or ACTION?
  DATA   → an observation about how the user lives. No response needed.
  ACTION → the user still needs to do something deliberate.

Step 2 — Pick the specific category:

  ACTION categories:
    task        — deliberate action still possible: reply, submit form, confirm charge, RSVP, pay bill
    event       — scheduled event the user is invited to: meeting, appointment, webinar, call
    opportunity — optional but worth considering: scholarship, survey, focus group, challenge, internship

  DATA categories:
    financial   — financial activity or info: purchase receipt, payment, statement, refund, charge, disbursement
    logistics   — shipment/delivery status with no response needed: tracking update, delivery confirmation
    identity    — account/security events already done: OTP, verification code, login alert, password reset, account change
    academic    — academic data from any platform: grade posted, assignment notification, course announcement, LMS reminder
    social      — community/social info: alumni update, club digest, event recap, newsletter

  noise         — zero personal signal: pure promotional ads, spam

Rules:
  - If the action already happened and nothing is left to do → DATA, not action
  - "Invitation:" subject → event (action)
  - OTP/verification code → identity (data) — expires in minutes, useless by review time
  - Assignment/grade notification from LMS → academic (data) — LMS is the source of truth for the action
  - Shipping/delivery update → logistics (data)
  - ★ IMPORTANT emails: classify carefully, almost never noise

Return: {{"category": "task|event|opportunity|financial|academic|logistics|identity|social|noise", "intent": "action|data|noise", "confidence": 0.0-1.0, "consequence": 1-5, "urgency_context": "available_window|hard_deadline|evergreen", "summary": "one sentence what the user must do, or null for data/noise", "deadline": "specific date if mentioned or null"}}

consequence — how bad is it if the user ignores this entirely?
  1=no impact, 2=minor, 3=moderate, 4=significant, 5=serious financial/academic/legal consequence
Use world knowledge about the domain.

urgency_context — time pressure independent of whether a date is mentioned:
  available_window — important opportunity just opened; no hard date but the window will close (FAFSA, enrollment, scholarship, benefit application)
  hard_deadline    — explicit date mentioned or strongly implied by context
  evergreen        — no real time pressure; can be done anytime"""

    try:
        response = client.chat.completions.create(
            model='qwen/qwen3.8-27b',
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.1,
            max_tokens=512,
        )
        raw = response.choices[0].message.content.strip()
        # Strip Qwen3 thinking blocks (<think>...</think>)
        raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
        # Strip markdown fences if present
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        start, end = raw.find('{'), raw.rfind('}') + 1
        if start != -1 and end > start:
            data = json.loads(raw[start:end])
            cat    = data.get('category', 'noise')
            intent = data.get('intent', 'noise')
            # Legacy aliases
            if cat == 'transaction': cat = 'financial'
            if cat == 'other':       cat = 'noise'
            if cat not in VALID_CATEGORIES:
                cat = 'noise'
            # Derive intent from category if Groq omits it
            if intent not in ('action', 'data', 'noise'):
                if cat in ('task', 'event', 'opportunity'):
                    intent = 'action'
                elif cat == 'noise':
                    intent = 'noise'
                else:
                    intent = 'data'
            consequence = max(1, min(5, int(data.get('consequence', 3))))
            urgency_context = data.get('urgency_context', 'evergreen')
            if urgency_context not in ('available_window', 'hard_deadline', 'evergreen'):
                urgency_context = 'evergreen'
            return _make_result(
                email, cat, intent,
                float(data.get('confidence', 0.5)),
                consequence,
                urgency_context,
                data.get('summary'),
                data.get('deadline'),
            )
    except Exception:
        pass

    return _make_result(email, 'noise', 'noise', 0.0, 1, 'evergreen', None, None)


def classify_emails(emails: list[dict], gmail_service=None) -> list[dict]:
    """Classify all emails through the filter pipeline.

    Pipeline order:
      1. Tab auto-noise (promotions/social/forums)
      2. No List-Unsubscribe → personal record (skip Groq)
      3. Updates gate (frequency + engagement)
      4. Everything else → Groq

    gmail_service: authenticated Gmail API service object. When provided, the
    engagement index is built (or loaded from cache) to gate the updates tab.
    """
    api_key = os.getenv('GROQ_API_KEY', '')
    if not api_key:
        print('  ⚠️  GROQ_API_KEY not set — all emails marked other.')
        return [_make_result(e, 'other', 'noise', 0.0, 1, 'evergreen', None, None) for e in emails]

    # Build behavioral indexes once
    engagement_index = None
    if gmail_service is not None:
        try:
            engagement_index = build_engagement_index(gmail_service)
        except Exception as e:
            print(f'  ⚠️  Engagement index failed, updates tab will pass through: {e}')

    frequency_index = _build_frequency_index()

    from groq import Groq
    client  = Groq(api_key=api_key)
    results = []

    for i, email in enumerate(emails, 1):
        if i % 10 == 0 or i == len(emails):
            print(f'  Classifying {i}/{len(emails)}...')
        results.append(_classify_one(email, client, engagement_index, frequency_index))

    # Pipeline summary
    stages = defaultdict(int)
    for r in results:
        stages[r.get('_filter_stage', 'unknown')] += 1
    parts = [f'{stages.get(s, 0)} {s}' for s in ('tab_noise', 'record', 'updates_noise', 'groq') if stages.get(s)]
    print(f'  Pipeline: {len(results)} total → {" | ".join(parts)}')

    return results


# ==========================================
# SCHOOL EMAIL — Playwright backend
# ==========================================

# Search for emails likely to matter — broad enough to catch most cases
_GMAIL_SEARCH = (
    'subject:(assignment OR homework OR due OR deadline OR grade OR '
    'payment OR receipt OR opportunity OR fellowship OR scholarship)'
)


async def scrape_school_gmail(page: Page, email: str, username: str, password: str) -> list:
    """Log into school Gmail via Playwright SSO and return raw email list."""
    from apps.gradebook.classroom_scraper import login_with_google_sso

    success = await login_with_google_sso(page, email, username, password, target_url=GMAIL_URL)
    if not success:
        print('❌ School Gmail login failed.')
        return []

    print('📬 Navigating Gmail...')
    await page.wait_for_timeout(2000)

    # Search for relevant emails
    try:
        search_box = page.locator('input[name="q"], input[aria-label*="Search"]').first
        await search_box.fill(_GMAIL_SEARCH)
        await search_box.press('Enter')
        await page.wait_for_timeout(2000)
    except Exception:
        print('⚠️  Gmail search unavailable — reading inbox as-is.')

    emails = []
    try:
        rows = await page.locator('tr.zA').all()
        print(f'  Found {len(rows)} rows.')

        for row in rows[:25]:
            try:
                sender   = (await row.locator('.yX, .zF').first.inner_text()).strip()
                subject  = (await row.locator('.y6 span, .bog').first.inner_text()).strip()
                snippet  = (await row.locator('.y2').first.inner_text()).strip()
                date_str = (await row.locator('.xW span, .xY span').first.inner_text()).strip()

                emails.append({
                    'id':      f'school_{abs(hash(subject + sender))}',
                    'from':    sender,
                    'subject': subject,
                    'snippet': snippet,
                    'date':    date_str,
                    'body':    snippet,
                    'source':  'school',
                })
            except Exception:
                continue
    except Exception as e:
        print(f'⚠️  Error reading rows: {e}')

    return emails


# ==========================================
# PERSONAL EMAIL — Gmail API backend
# ==========================================

def scrape_gmail_api(max_results: int = 200, days: int = 7,
                     token_path: Path = TOKEN_PATH,
                     source: str = 'personal') -> tuple[list, object | None]:
    """Fetch emails from a Gmail account via OAuth2 API.

    Args:
        token_path: Path to the OAuth token file for this account.
        source: Label for the email source ('personal' or 'school').

    Returns (emails, service) so the caller can reuse the authenticated
    service object for the engagement index without re-authenticating.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        print('⚠️  Google client libs not installed.')
        print('    pip install google-api-python-client google-auth-oauthlib')
        return [], None

    if not CREDS_PATH.exists():
        print(f'⚠️  {CREDS_PATH} not found.')
        print('    Download OAuth2 credentials from Google Cloud Console → save as credentials.json')
        return [], None

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDS_PATH), SCOPES, redirect_uri='http://localhost'
            )
            auth_url, _ = flow.authorization_url(prompt='consent')
            print(f'\n  Open this URL in your browser:\n  {auth_url}\n')
            print('  After authorizing, your browser will redirect to localhost (it will fail to load).')
            print('  Copy the FULL URL from the address bar and paste it here.')
            redirect_response = input('  Paste redirect URL: ').strip()
            flow.fetch_token(authorization_response=redirect_response)
            creds = flow.credentials
        with open(token_path, 'w') as f:
            f.write(creds.to_json())

    service  = build('gmail', 'v1', credentials=creds)
    results  = service.users().messages().list(
        userId='me', q=f'in:inbox newer_than:{days}d', maxResults=max_results
    ).execute()
    messages = results.get('messages', [])

    emails = []
    for msg in messages:
        try:
            full    = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
            headers = {h['name']: h['value'] for h in full['payload']['headers']}
            subject = headers.get('Subject', '')
            sender  = headers.get('From', '')
            date_str = headers.get('Date', '')

            def _extract_body(part):
                """Recursively find the first text/plain part."""
                if part.get('mimeType') == 'text/plain':
                    data = part.get('body', {}).get('data', '')
                    if data:
                        return base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                for sub in part.get('parts', []):
                    result = _extract_body(sub)
                    if result:
                        return result
                return ''

            body = _extract_body(full['payload'])

            label_ids = full.get('labelIds', [])
            gmail_categories = [
                l.replace('CATEGORY_', '').lower()
                for l in label_ids
                if l.startswith('CATEGORY_')
            ]
            gmail_tab = gmail_categories[0] if gmail_categories else 'primary'

            emails.append({
                'id':         msg['id'],
                'from':       sender,
                'subject':    subject,
                'snippet':    full.get('snippet', ''),
                'date':       date_str,
                'body':       body[:2000],
                'source':     source,
                'gmail_tab':  gmail_tab,
                'is_important': 'IMPORTANT' in label_ids,
                'has_list_unsubscribe': 'List-Unsubscribe' in headers,
            })
        except Exception:
            continue

    return emails, service


# ==========================================
# ROUTER — agenda tasks from email
# ==========================================

def route_to_db(email: dict, classification: dict):
    """Insert deadline/assignment emails as tasks into formal.db."""
    if 'agenda' not in classification.get('routes_to', []):
        return

    due_date = None
    if classification.get('deadline'):
        for fmt in ('%B %d, %Y', '%B %d', '%m/%d/%Y', '%m/%d/%y', '%m/%d'):
            try:
                parsed = datetime.strptime(classification['deadline'], fmt)
                if parsed.year == 1900:
                    parsed = parsed.replace(year=datetime.now().year)
                due_date = parsed.strftime('%Y-%m-%d')
                break
            except ValueError:
                continue

    notes = '; '.join(classification.get('action_items', [])) or None

    init_db()
    conn = get_db()
    with conn:
        conn.execute(
            """INSERT OR IGNORE INTO tasks (source, title, due_date, notes, status, created_at)
               VALUES (?, ?, ?, ?, 'pending', ?)""",
            ('gmail', email['subject'], due_date, notes, datetime.now(timezone.utc).isoformat())
        )
    conn.close()


# ==========================================
# MAIN
# ==========================================

async def main(creds: tuple = None, days: int = None):
    email_arg = username_arg = password_arg = None
    if creds:
        email_arg, username_arg, password_arg = creds

    # ── Date window + email cap prompts ───────────────────
    if days is None:
        print('─' * 60)
        raw = input('Fetch window — 1, 7, or 30 days? [7] ').strip()
        days = int(raw) if raw in ('1', '7', '30') else 7
        raw2 = input('Max emails? [all] ').strip()
        max_emails = int(raw2) if raw2.isdigit() else 500
    else:
        max_emails = 30
    print(f'  Fetching last {days} day(s), up to {max_emails} emails.')

    all_emails = []

    gmail_service = None

    # ── Personal email ─────────────────────────────────────
    print()
    print('─' * 60)
    print('GMAIL — Personal Account (API)')
    print('─' * 60)
    personal_emails, gmail_service = scrape_gmail_api(
        max_results=max_emails, days=days,
        token_path=TOKEN_PATH, source='personal',
    )
    print(f'  ✅ {len(personal_emails)} emails fetched.')
    all_emails.extend(personal_emails)

    # ── School email ──────────────────────────────────────
    if TOKEN_PATH_SCHOOL.exists() or not TOKEN_PATH.exists():
        print()
        print('─' * 60)
        print('GMAIL — School Account (API)')
        print('─' * 60)
        school_emails, school_service = scrape_gmail_api(
            max_results=max_emails, days=days,
            token_path=TOKEN_PATH_SCHOOL, source='school',
        )
        print(f'  ✅ {len(school_emails)} emails fetched.')
        all_emails.extend(school_emails)
        if gmail_service is None:
            gmail_service = school_service
    else:
        print('⏭️  School Gmail skipped (no token_school.json).')

    if not all_emails:
        print('⚠️  No emails fetched.')
        return

    # ── Deduplication ──────────────────────────────────────
    def _norm_subject(s: str) -> str:
        s = re.sub(r'^\[[\w\s]+\]\s*', '', s)          # strip [Updated], [Reminder], etc.
        s = re.sub(r'^(re|fwd?)[:\s]+', '', s, flags=re.IGNORECASE)
        return s.strip().lower()

    seen_ids   = set()
    seen_pairs = set()
    deduped    = []
    for em in all_emails:
        key_id   = em.get('id', '')
        key_pair = (em.get('from', '').lower(), _norm_subject(em.get('subject', '')))
        if key_id in seen_ids or key_pair in seen_pairs:
            continue
        seen_ids.add(key_id)
        seen_pairs.add(key_pair)
        deduped.append(em)

    dupes = len(all_emails) - len(deduped)
    if dupes:
        print(f'  🔁 {dupes} duplicate(s) removed → {len(deduped)} unique emails.')
    all_emails = deduped

    # ── Classify + route ───────────────────────────────────
    print()
    print('─' * 60)
    print('GMAIL — Classify & Route')
    print('─' * 60)

    category_counts = {}

    results = classify_emails(all_emails, gmail_service=gmail_service)
    for r in results:
        route_to_db(r, r)
        cat = r['category']
        category_counts[cat] = category_counts.get(cat, 0) + 1

    # ── Items pool ────────────────────────────────────────
    try:
        from apps.gmail.items_adapter import emails_to_items
        from core.items import ItemRepository

        pool_items = emails_to_items(results)
        init_db()
        repo = ItemRepository()
        if pool_items:
            repo.upsert(pool_items)
            print(f'  📦 {len(pool_items)} items upserted to pool.')
        else:
            print('  📦 No items to upsert.')
        archived = repo.archive_past_events()
        if archived:
            print(f'  📁 {archived} past event(s) archived.')
        repo.close()
    except Exception as e:
        print(f'  ⚠️  Items pool write failed: {e}')

    now_iso = datetime.now(timezone.utc).isoformat() + 'Z'

    # Current session — always overwritten
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump({'scraped_at': now_iso, 'total': len(results), 'emails': results}, f, indent=2, ensure_ascii=False)

    # Persistent history — append only, dedupe by email ID
    HISTORY_PATH = 'output/emails_history.json'
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH, encoding='utf-8') as f:
            history = json.load(f)
    else:
        history = {'emails': []}

    existing_ids = {e['id'] for e in history['emails']}
    new_emails   = [r for r in results if r.get('id') and r['id'] not in existing_ids]
    history['emails'].extend(new_emails)
    history['last_updated'] = now_iso
    history['total'] = len(history['emails'])
    with open(HISTORY_PATH, 'w', encoding='utf-8') as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

    noise_count = category_counts.get('noise', 0)
    other_count = category_counts.get('other', 0)
    actionable_count = sum(v for k, v in category_counts.items() if k not in ('noise', 'other'))
    print(f'  ✅ {len(results)} classified → {OUTPUT_PATH}  (+{len(new_emails)} new → {HISTORY_PATH})')
    print(f'  {noise_count} noise  |  {other_count} unclassified  |  {actionable_count} actionable')

    CATEGORY_ORDER = ['task', 'event', 'transaction', 'opportunity']
    CATEGORY_LABEL = {
        'task':        'TASKS',
        'event':       'EVENTS',
        'transaction': 'TRANSACTIONS',
        'opportunity': 'OPPORTUNITIES',
    }

    by_cat = {}
    for r in results:
        if r['category'] in ('noise', 'other'):
            continue
        by_cat.setdefault(r['category'], []).append(r)

    W = 80
    print()
    print('=' * W)
    print(f'  GMAIL INTAKE — {datetime.now().strftime("%Y-%m-%d")}  ·  last {days}d  ·  {len(results)} emails  ·  ★ = Gmail Important')
    print('=' * W)

    for cat in CATEGORY_ORDER:
        emails = by_cat.get(cat, [])
        if not emails:
            continue

        print(f'\n  {CATEGORY_LABEL[cat]}')
        print('  ' + '-' * (W - 2))
        # fixed cols: star(1) + conf(5) + deadline(14) + amounts(10) = 30, plus spacing = 38; subject gets rest
        subj_w = W - 40
        print(f'  {"":1}  {"CONF":>5}  {"DEADLINE":<14}  {"AMT":<8}  {"SUBJECT":<{subj_w}}')
        print('  ' + '-' * (W - 2))

        # Collapse threads: group by normalized subject, keep the most important row
        thread_map = {}
        for r in emails:
            key = re.sub(r'^\[[\w\s]+\]\s*', '', r['subject'])
            key = re.sub(r'^(re|fwd?)[:\s]+', '', key, flags=re.IGNORECASE).strip().lower()
            if key not in thread_map:
                thread_map[key] = {'row': r, 'count': 1}
            else:
                thread_map[key]['count'] += 1
                # Keep the most important/highest-confidence version
                cur = thread_map[key]['row']
                if (r.get('is_important') and not cur.get('is_important')) or r['confidence'] > cur['confidence']:
                    thread_map[key]['row'] = r

        collapsed = sorted(thread_map.values(), key=lambda x: (-x['row'].get('is_important', False), -x['row']['confidence']))
        for entry in collapsed:
            r       = entry['row']
            count   = entry['count']
            star    = '★' if r.get('is_important') else ' '
            conf    = f'{r["confidence"]:.2f}'
            dl      = (r.get('deadline') or '')[:14]
            amounts = ' '.join(r.get('amounts', []))[:8]
            cnt_tag = f' (×{count})' if count > 1 else ''
            subj    = (r['subject'][:subj_w - len(cnt_tag)] + cnt_tag)
            print(f'  {star}  {conf:>5}  {dl:<14}  {amounts:<8}  {subj}')

    # Unclassified — just a count, no rows
    if other_count:
        print(f'\n  {other_count} unclassified emails hidden  (see output/emails.json for full data)')

    print('\n' + '=' * W)

    # ── Action items ───────────────────────────────────────
    ACTION_CATS = {'task', 'event', 'opportunity'}
    actionable = [r for r in results if r['category'] in ACTION_CATS and r.get('action_items')]
    # Sort: Gmail Important first, then by confidence
    actionable.sort(key=lambda x: (-x.get('is_important', False), -x['confidence']))
    if actionable:
        print()
        print('=' * W)
        print('  ACTION ITEMS')
        print('=' * W)
        for r in actionable:
            star = '★ ' if r.get('is_important') else '  '
            print(f'\n  {star}[{r["category"].upper()}]  {r["subject"][:60]}')
            for item in r['action_items'][:3]:
                print(f'      →  {item[:W - 10]}')
        print('\n' + '=' * W)


if __name__ == '__main__':
    asyncio.run(main())
