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
from collections import Counter, defaultdict
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
    'task':          ['agenda'],
    'event':         ['agenda'],
    'opportunity':   ['agenda'],
    'financial':     ['checkbook'],
    'academic':      ['gradebook'],
    'logistics':     [],
    'identity':      [],
    'social':        [],
    'uncategorized': [],
    'noise':         [],
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


# ── Engagement index ──────────────────────────────────────────────────────────

_ENGAGEMENT_CACHE_PATH = Path('output/engagement_cache.json')
_ENGAGEMENT_TTL_HOURS  = 24 * 7   # a daily run reuses the index for a week

# Bulk tabs the gate filters; Primary/personal mail always passes
_BULK_TABS = ('promotions', 'social', 'updates', 'forums')

# A sender whose items you dismissed this often (and never finished) is dropped
_DISMISS_DROP_THRESHOLD = 3


def _sender_domain(address: str) -> str:
    """Extract the domain from a From: address like 'Name <user@domain.com>'."""
    m = re.search(r'@([\w.\-]+)', address)
    return m.group(1).lower() if m else address.lower().strip()


def build_engagement_index(service, cache_path: Path = _ENGAGEMENT_CACHE_PATH,
                           rebuild: bool = False) -> dict:
    """
    Build a sender-domain engagement signal from Gmail history.

    Returns a dict:
      {
        'replied_domains':  set of domains the user has sent mail to,
        'opened_domains':   {domain: count} of messages still in the inbox
                            (Primary/Updates, last 500) — kept, not necessarily read,
        'built_at':         ISO timestamp,
      }

    Cached to disk for _ENGAGEMENT_TTL_HOURS hours so it's not rebuilt every run.
    A build with any API error is used for this run only and never cached, so one
    network blip can't silently empty the bulk tabs for a week. rebuild=True
    ignores the cache.
    """
    os.makedirs('output', exist_ok=True)

    # Return cached index if fresh
    if cache_path.exists() and not rebuild:
        try:
            cached = json.loads(cache_path.read_text())
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
    errors = 0

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
                errors += 1
                continue
    except Exception as e:
        errors += 1
        print(f'  ⚠️  Could not fetch sent mail: {e}')

    # ── Signal 2: inbox open proxy — messages kept in INBOX by sender domain ──
    # Heuristic: if a message is in INBOX (and not a bulk tab) it was at minimum
    # seen and not immediately archived/trashed — a weak "opened" signal.
    # Exclude promotions/social/forums so opening a Facebook digest to investigate
    # doesn't accidentally credit facebookmail.com.
    _bulk_exclusion = ' '.join(f'-category:{t}' for t in ('promotions', 'social', 'forums'))
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
                errors += 1
                continue
    except Exception as e:
        errors += 1
        print(f'  ⚠️  Could not fetch inbox history: {e}')

    index = {
        'replied_domains': list(replied_domains),
        'opened_domains':  dict(opened_domains),
        'built_at':        datetime.now(timezone.utc).isoformat(),
    }
    if errors:
        print(f'  ⚠️  Engagement index had {errors} API error(s); using it for this run only (not cached).')
    else:
        cache_path.write_text(json.dumps(index, indent=2))
        print(f'  Engagement index: {len(replied_domains)} reply domains, '
              f'{len(opened_domains)} inbox domains cached.')

    index['replied_domains'] = replied_domains
    index['opened_domains']  = defaultdict(int, opened_domains)
    return index


def _engagement_reason(sender: str, index: dict | None, threshold: int) -> str | None:
    """Why this sender counts as engaged, or None if it doesn't.

      'replied'          — you have sent mail to this domain
      'kept_<threshold>' — ≥threshold of its messages are still in your inbox
      'no_index'         — no engagement data this run, so nothing is suppressed
      'no_domain'        — unparseable From, so nothing is suppressed
    """
    if index is None:
        return 'no_index'
    domain = _sender_domain(sender)
    if not domain:
        return 'no_domain'
    if domain in index.get('replied_domains', set()):
        return 'replied'
    if index.get('opened_domains', {}).get(domain, 0) >= threshold:
        return f'kept_{threshold}'
    return None


def _is_low_engagement(sender: str, index: dict | None) -> bool:
    """True if the sender has no engagement signal (≥2 kept or any reply)."""
    return _engagement_reason(sender, index, 2) is None


def _is_low_engagement_at_threshold(sender: str, index: dict | None,
                                     threshold: int = 5) -> bool:
    """Like _is_low_engagement but requires ≥threshold kept messages."""
    return _engagement_reason(sender, index, threshold) is None


def _is_dismissed_sender(domain: str, reputation: dict | None) -> bool:
    """True when you dismissed this sender's items ≥_DISMISS_DROP_THRESHOLD times
    and never marked one done (sender_reputation, written by the Agenda CLIs)."""
    done, dismissed = (reputation or {}).get(domain, (0, 0))
    return dismissed >= _DISMISS_DROP_THRESHOLD and done == 0


def _load_sender_reputation() -> dict[str, tuple[int, int]]:
    """Read sender_reputation as {domain: (done_count, dismiss_count)}; {} if unavailable."""
    try:
        conn = get_db()
        try:
            rows = conn.execute(
                'SELECT domain, done_count, dismiss_count FROM sender_reputation'
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return {}
    return {r['domain']: (r['done_count'], r['dismiss_count']) for r in rows}


# ── Sender frequency index ────────────────────────────────────────────────────

_FREQUENCY_THRESHOLD = 2  # domains with ≤ this many emails in history are "rare"


def _build_frequency_index(history_path: str = 'output/emails_history.json',
                           batch: list[dict] | None = None) -> dict[str, int]:
    """Count emails per sender domain from local history plus the current batch.

    Counting the batch (deduplicated by message ID) means a first run with no
    history doesn't treat every sender as rare. No API calls.
    """
    counts: dict[str, int] = defaultdict(int)
    seen: set = set()
    try:
        with open(history_path, encoding='utf-8') as f:
            history = json.load(f)
        for email in history.get('emails', []):
            seen.add(email.get('id'))
            d = _sender_domain(email.get('from', ''))
            if d:
                counts[d] += 1
    except (FileNotFoundError, json.JSONDecodeError, AttributeError):
        pass
    for email in batch or []:
        msg_id = email.get('id')
        if msg_id and msg_id in seen:
            continue
        seen.add(msg_id)
        d = _sender_domain(email.get('from', ''))
        if d:
            counts[d] += 1
    return dict(counts)


def _is_rare_sender(sender: str, frequency_index: dict[str, int]) -> bool:
    """Return True if the sender domain has appeared ≤ _FREQUENCY_THRESHOLD times
    (history plus the current batch, so this email counts toward its own total)."""
    domain = _sender_domain(sender)
    return frequency_index.get(domain, 0) <= _FREQUENCY_THRESHOLD


VALID_CATEGORIES = {
    # action
    'task', 'event', 'opportunity',
    # data
    'financial', 'academic', 'logistics', 'identity', 'social',
    # gate default — labeling deferred to downstream classifier
    'uncategorized',
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



def _make_result(email: dict, category: str, intent: str, confidence: float,
                 consequence: int, urgency_context: str,
                 summary: str | None, deadline: str | None,
                 stage: str = 'groq') -> dict:
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
        '_filter_stage':   stage,
    }


def _classify_one(email: dict, client=None,
                   engagement_index: dict | None = None,
                   frequency_index: dict[str, int] | None = None,
                   reputation: dict[str, tuple[int, int]] | None = None) -> dict:
    """Decide pool vs noise for one email (first match wins).

      0. Bulk tab, not Important, sender you keep dismissing  → noise 'dismissed_sender'
      1. Promotions/Social: Important, replied, or ≥5 kept     → pool, else 'tab_noise'
      2. Updates/Forums: Important, rare, replied, or ≥2 kept  → pool, else 'updates_noise'
      3. Primary/personal/other tabs                           → pool ('primary_tab')

    Pooled mail is labeled by the AI when a client is given, otherwise left
    'uncategorized' for items_adapter. Every result carries _pass_reason (None
    for noise) so each decision can be explained.
    """
    tab     = email.get('gmail_tab', 'unknown')
    subject = email.get('subject', '')
    sender  = email.get('from', '')
    raw_body = email.get('body') or ''
    body     = re.sub(r'<[^>]+>', ' ', raw_body)       # strip HTML tags
    body     = re.sub(r'\s+', ' ', body).strip()[:300]  # collapse whitespace, truncate

    # Compute engagement signals once — injected into every result
    _rare     = _is_rare_sender(sender, frequency_index or {})
    _engaged  = not _is_low_engagement(sender, engagement_index)
    _domain   = _sender_domain(sender)

    _pass_reason = None

    def _enrich(result: dict) -> dict:
        result['_rare_sender']    = _rare
        result['_engaged_sender'] = _engaged
        result['_sender_domain']  = _domain
        result['_pass_reason']    = _pass_reason
        return result

    is_important = email.get('is_important', False)

    # ── 0. Learned drop: senders you keep dismissing (bulk tabs only) ───────
    if tab in _BULK_TABS and not is_important and _is_dismissed_sender(_domain, reputation):
        return _enrich(_make_noise(email, stage='dismissed_sender'))

    # ── 1. Promotions/social → engagement check ─────────────────────────────
    if tab in ('promotions', 'social'):
        if is_important:
            _pass_reason = 'important'
        else:
            _pass_reason = _engagement_reason(sender, engagement_index, 5)
            if _pass_reason is None:
                return _enrich(_make_noise(email, stage='tab_noise'))

    # ── 2. Updates/forums → behavioral signals for mass-sent mail ───────────
    elif tab in ('updates', 'forums'):
        if is_important:
            _pass_reason = 'important'
        elif _rare:
            _pass_reason = 'rare_sender'
        else:
            _pass_reason = _engagement_reason(sender, engagement_index, 2)
            if _pass_reason is None:
                return _enrich(_make_noise(email, stage='updates_noise'))

    # ── 3. Everything else (Primary/personal) passes ────────────────────────
    else:
        _pass_reason = 'primary_tab'

    if client is None:
        return _enrich(_make_result(email, 'uncategorized', 'unknown', 0.50, 1, 'evergreen', None, None, stage='unclassified'))
    prompt = f"""You are classifying one email for a personal life OS. Return ONLY a JSON object — no markdown, no extra text.

This email passed structural filters — it likely requires a decision or action from the user.

Email:
  From: {sender[:80]}
  Subject: {subject[:120]}
  Body: {body}

What type of action does this email represent?

  task        — the user needs to DO something: reply, submit, sign up, confirm, pay, register, complete a form
  event       — the user is INVITED TO ATTEND something at a specific time: meeting, info session, reception, clinic, festival, webinar
  opportunity — OPTIONAL but worth considering: scholarship, internship, career fair, program, survey, challenge

If this email is purely informational with no action needed, return category "social".

Return: {{"category": "task|event|opportunity|social", "confidence": 0.0-1.0, "consequence": 1-5, "urgency_context": "available_window|hard_deadline|evergreen", "summary": "one sentence what the user should do or know", "deadline": "specific date if mentioned or null"}}

consequence — how bad is it if the user ignores this entirely?
  1=no impact, 2=minor, 3=moderate, 4=significant, 5=serious consequence

urgency_context:
  available_window — opportunity just opened, window will close (registration, enrollment, FAFSA)
  hard_deadline    — explicit date mentioned
  evergreen        — no time pressure"""

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
            cat    = data.get('category', 'social')
            # Legacy aliases
            if cat == 'transaction': cat = 'financial'
            if cat == 'other':       cat = 'social'
            if cat == 'noise':       cat = 'social'  # Groq shouldn't return noise anymore
            if cat not in VALID_CATEGORIES:
                cat = 'social'
            # Derive intent from category
            if cat in ('task', 'event', 'opportunity'):
                intent = 'action'
            else:
                intent = 'data'
            consequence = max(1, min(5, int(data.get('consequence', 3))))
            urgency_context = data.get('urgency_context', 'evergreen')
            if urgency_context not in ('available_window', 'hard_deadline', 'evergreen'):
                urgency_context = 'evergreen'
            return _enrich(_make_result(
                email, cat, intent,
                float(data.get('confidence', 0.5)),
                consequence,
                urgency_context,
                data.get('summary'),
                data.get('deadline'),
            ))
    except Exception:
        pass

    # Groq failed — pool as uncategorized instead of dropping as noise
    return _enrich(_make_result(email, 'uncategorized', 'unknown', 0.50, 1, 'evergreen', None, None, stage='unclassified'))


_ENGAGEMENT_CACHE_PERSONAL = Path('output/engagement_cache_personal.json')
_ENGAGEMENT_CACHE_SCHOOL   = Path('output/engagement_cache_school.json')


def classify_emails(emails: list[dict], gmail_services: list | None = None,
                     use_ai: bool = False, rebuild: bool = False) -> list[dict]:
    """Run every email through the pool-vs-noise gate (see _classify_one).

    Signals loaded once per run: the engagement index (built from ALL accounts
    and merged), sender frequency (history plus this batch), and the
    sender_reputation table written by the Agenda's done/dismiss commands.
    rebuild=True re-classifies emails already in the pool and ignores the
    engagement cache. Labels come from the AI only when use_ai=True.
    """
    # Build and merge engagement indexes from all accounts
    cache_paths = [_ENGAGEMENT_CACHE_PERSONAL, _ENGAGEMENT_CACHE_SCHOOL]
    engagement_index = None
    for i, service in enumerate(gmail_services or []):
        try:
            cache_path = cache_paths[i] if i < len(cache_paths) else _ENGAGEMENT_CACHE_PATH
            idx = build_engagement_index(service, cache_path=cache_path, rebuild=rebuild)
            if engagement_index is None:
                engagement_index = idx
            else:
                engagement_index['replied_domains'] |= idx['replied_domains']
                for domain, count in idx['opened_domains'].items():
                    engagement_index['opened_domains'][domain] += count
        except Exception as e:
            print(f'  ⚠️  Engagement index failed for account {i+1}: {e}')

    frequency_index = _build_frequency_index(batch=emails)
    reputation = _load_sender_reputation()

    client = None
    if use_ai:
        api_key = os.getenv('GROQ_API_KEY', '')
        if api_key:
            from groq import Groq
            client = Groq(api_key=api_key)
        else:
            print('  note: --ai requested but GROQ_API_KEY not set; using structural only.')

    # Skip emails already in the items pool (unless rebuilding)
    existing_ids = set()
    if not rebuild:
        try:
            pool_conn = get_db()
            existing_ids = {
                row["source_item_id"]
                for row in pool_conn.execute(
                    "SELECT source_item_id FROM items WHERE source IN ('gmail_personal', 'gmail_school') AND source_item_id IS NOT NULL"
                ).fetchall()
            }
            pool_conn.close()
        except Exception:
            pass

    results = []
    skipped = 0

    for i, email in enumerate(emails, 1):
        if email.get('id', '') in existing_ids:
            skipped += 1
            continue
        if i % 10 == 0 or i == len(emails):
            print(f'  Classifying {i}/{len(emails)}...')
        results.append(_classify_one(email, client, engagement_index, frequency_index, reputation))

    if skipped:
        print(f'  Skipped {skipped} email(s) already in pool.')

    for line in _pipeline_summary(results):
        print(line)

    return results


def _pipeline_summary(results: list[dict]) -> list[str]:
    """One line of counts per gate stage, one per reason mail was kept."""
    stages  = Counter(r.get('_filter_stage', 'unknown') for r in results)
    reasons = Counter(r['_pass_reason'] for r in results if r.get('_pass_reason'))
    lines = [f'  Pipeline: {len(results)} total -> '
             + ' | '.join(f'{n} {s}' for s, n in stages.most_common())]
    if reasons:
        lines.append('  Kept because: ' + ' | '.join(f'{n} {r}' for r, n in reasons.most_common()))
    return lines


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

async def main(creds: tuple = None, days: int = None, use_ai: bool = False, rebuild: bool = False):
    email_arg = username_arg = password_arg = None
    if creds:
        email_arg, username_arg, password_arg = creds

    # ── Date window + email cap prompts ───────────────────
    if days is None:
        print('─' * 60)
        raw = input('Fetch window in days (1-365)? [7] ').strip()
        days = min(365, max(1, int(raw))) if raw.isdigit() else 7
        raw2 = input('Max emails? [all] ').strip()
        max_emails = int(raw2) if raw2.isdigit() else 1000
    else:
        max_emails = 30
    print(f'  Fetching last {days} day(s), up to {max_emails} emails.')

    all_emails = []

    gmail_services = []

    # ── Personal email ─────────────────────────────────────
    print()
    print('─' * 60)
    print('GMAIL — Personal Account (API)')
    print('─' * 60)
    personal_emails, personal_service = scrape_gmail_api(
        max_results=max_emails, days=days,
        token_path=TOKEN_PATH, source='personal',
    )
    print(f'  ✅ {len(personal_emails)} emails fetched.')
    all_emails.extend(personal_emails)
    if personal_service:
        gmail_services.append(personal_service)

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
        if school_service:
            gmail_services.append(school_service)
    else:
        print('⏭️  School Gmail skipped (no token_school.json).')

    if not all_emails:
        print('⚠️  No emails fetched.')
        return

    # ── Deduplication (by Gmail message ID only) ─────────────
    seen_ids = set()
    deduped  = []
    for em in all_emails:
        key_id = em.get('id', '')
        if key_id in seen_ids:
            continue
        seen_ids.add(key_id)
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

    results = classify_emails(all_emails, gmail_services=gmail_services, use_ai=use_ai, rebuild=rebuild)
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
        repo.reopen_deferred_items()
        archived = repo.archive_past_items()
        if archived:
            print(f'  📁 {archived} past item(s) archived.')
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
    import argparse as _ap
    _parser = _ap.ArgumentParser(description='Gmail intake for Formal')
    _parser.add_argument('--ai', action='store_true', help='Use Groq AI for classification')
    _parser.add_argument('--rebuild', action='store_true', help='Re-classify all emails (ignore pool cache)')
    _args = _parser.parse_args()
    asyncio.run(main(use_ai=_args.ai, rebuild=_args.rebuild))
