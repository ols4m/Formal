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
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
load_dotenv(Path(__file__).parent.parent.parent / '.env')
from core.db import get_db, init_db

os.makedirs('output', exist_ok=True)
OUTPUT_PATH = 'output/emails.json'
GMAIL_URL   = 'https://mail.google.com/'

# Personal Gmail OAuth token/credentials (gitignored)
TOKEN_PATH = Path(__file__).parent / 'token.json'
CREDS_PATH = Path(__file__).parent / 'credentials.json'
SCOPES     = ['https://www.googleapis.com/auth/gmail.readonly']


# ==========================================
# CLASSIFIER
# ==========================================

_PATTERNS = {
    # Must match subject — these are unambiguous school signals
    'assignment': re.compile(
        r'\b(homework|assignment|classwork|problem set|worksheet|turn in|AP exam|exam terms|career pathways)\b',
        re.IGNORECASE
    ),
    # Must match subject — require PowerSchool/gradebook source signals, not just the word "grade"
    'grade': re.compile(
        r'\b(gradebook|powerschool|report card|points earned|your grade|GPA update|grade report)\b',
        re.IGNORECASE
    ),
    # Financial activity — very specific phrases that only appear in real bank/payment emails
    'transaction': re.compile(
        r'\b(you spent|direct debit|payment sent|payment received|your receipt|order confirmed|'
        r'your payment|payment at|payment of|a payment from|amount due|balance due|'
        r'purchase confirmed|transaction alert|bank transfer|you were charged)\b',
        re.IGNORECASE
    ),
    # College/scholarship/financial aid signals — checked BEFORE deadline so college emails land here
    'opportunity': re.compile(
        r'\b(scholarship|financial aid|fellowship|internship|stipend|'
        r'fafsa|hesaa|eof program|aid award|grant|'
        r'admissions|enrollment deposit|open house|campus visit|'
        r'congratulations.*admitted|you have been admitted|application status)\b',
        re.IGNORECASE
    ),
    # Deadline: require a specific action + time signal, not just "apply by" in marketing copy
    'deadline': re.compile(
        r'\b(action required|verify your|activate your|complete by|respond by|'
        r'submission closes|final deadline|last chance|'
        r'rsvp\s+(?:by|before|now)|due\s+(?:by|date)[:\s]|expires\s+(?:in|on))\b',
        re.IGNORECASE
    ),
}

# Worth surfacing — informational but not urgent
_WORTH_LOOKING_PATTERNS = re.compile(
    r'\b(fellow reminder|backrs|stanford|harvard|yale|columbia|MIT\b|'
    r'upcoming event|webinar|virtual session|info session|'
    r'thinking ahead|preparing for|open house|campus tour)\b',
    re.IGNORECASE
)

# Senders that are always noise regardless of subject
_NOISE_SENDERS = re.compile(
    r'(linkedin\.com|substack\.com|medium\.com|mailchimp|sendgrid|'
    r'constantcontact|klaviyo|hubspot|indeed\.com|glassdoor|'
    r'noreply@|no-reply@|donotreply@|do-not-reply@|'
    r'nike\.com|adidas\.|poshmark\.com|goat\.com|ssense\.|'
    r'canva|adobe\.|chess\.com|dunkin|sugarwish|customink|'
    r'generalassemb|info\.generalasse)',
    re.IGNORECASE
)

# General marketing/promotional signals — not tied to specific emails
_NOISE_PATTERNS = re.compile(
    r'(\d+%\s*off|free shipping|\bsale\b|promo code|coupon|'
    r'unsubscribe|newsletter|weekly digest|digest email|'
    r'product update|new feature|changelog|release notes|'
    r'recently posted|share their thought|and others (commented|reacted|shared)|'
    r'wants \d+% off|order by \w+day|'
    r'level up your|free \d+-hour|\bperfected\b|learn .{1,25} skills|'
    r'sneakers of the week|boost your beverage|designers people|'
    r'play coach|running style|work with pdfs|custom gifts|'
    r'smarter campaigns|just in time for|TL;DR of)',
    re.IGNORECASE
)

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
    'assignment':  ['agenda'],
    'grade':       ['gradebook'],
    'transaction': ['checkbook'],
    'deadline':    ['agenda'],
    'opportunity': ['agenda'],
    'other':        [],
    'worth a look': [],
    'noise':        [],
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


_GROQ_SYSTEM = """You are the intake classifier for Formal, a personal life OS for Samuel, a high school senior who:
- Is applying to college (Rutgers, RIT, Kean, NJIT, Rowan, and others)
- Works at In-Tandem, a youth program
- Tracks finances and purchases
- Routes email data to: Agenda (tasks/deadlines), Gradebook (grades), Checkbook (transactions)

CATEGORIES — pick exactly one per email:
- assignment  : school work, AP exams, homework, classwork
- grade       : grade reports, scores, GPA, PowerSchool, report cards
- transaction : purchases, payments, receipts, bank activity, money sent/received
- deadline    : time-sensitive action required (enrollment deadlines, RSVP, verify account)
- opportunity : college admissions, scholarships, financial aid, fellowships, internships
- noise       : marketing, promotions, newsletters, product announcements, brand emails, AI course ads, beverage deals, sneaker sales, Canva tips, anything selling something Samuel didn't ask for
- other       : everything that doesn't fit above

NOISE RULE — if the email is trying to sell something, promote a product, or is a newsletter Samuel didn't request, it is ALWAYS noise, even if it mentions AI, skills, or opportunities."""


def _classify_with_regex(subject: str, body: str) -> dict:
    """Regex-only classifier — subject matches are high confidence, body matches lower."""
    body_snippet = body[:600]

    # Priority order matters: transaction and assignment are unambiguous;
    # opportunity checked before deadline so college emails don't become deadlines.
    for cat in ('assignment', 'transaction', 'grade', 'opportunity', 'deadline'):
        if _PATTERNS[cat].search(subject):
            return _empty_classification(cat, 0.80)
        if _PATTERNS[cat].search(body_snippet):
            return _empty_classification(cat, 0.60)

    if _WORTH_LOOKING_PATTERNS.search(subject):
        return _empty_classification('worth a look', 0.60)
    if _WORTH_LOOKING_PATTERNS.search(body_snippet):
        return _empty_classification('worth a look', 0.45)

    return _empty_classification('other', 0.3)


def _noise_check(subject: str, sender: str) -> bool:
    """True if email should be filtered as noise before hitting Groq."""
    if _NOISE_PATTERNS.search(subject) or _NOISE_SENDERS.search(sender):
        # Never drop transactions or opportunities even from noisy/noreply senders
        if _PATTERNS['transaction'].search(subject) or _PATTERNS['opportunity'].search(subject):
            return False
        return True
    return False


def _noise_result() -> dict:
    return {
        'category': 'noise', 'confidence': 1.0,
        'deadline': None, 'financial_impact': False,
        'amounts': [], 'merchant': None,
        'action_items': [], 'routes_to': [], 'summary': None,
    }


def _finalize(result: dict, subject: str, body: str) -> dict:
    """Attach amounts, merchant, routes, and action items after Groq classification."""
    full = f"{subject} {body}"
    result['amounts'] = extract_amounts(full)
    result['merchant'] = extract_merchant(full) if result.get('category') == 'transaction' else None
    result['routes_to'] = ROUTES.get(result.get('category', 'other'), [])
    result['financial_impact'] = bool(_FINANCIAL_PATTERN.search(full))
    result['action_items'] = extract_action_items(body or subject)
    return result


def classify_email(subject: str, body: str, sender: str, is_important: bool = False) -> dict:
    """Single-email classify — used when not batching."""
    if _noise_check(subject, sender):
        return _noise_result()
    result = _classify_with_groq(subject, body[:800], sender, is_important)
    return _finalize(result, subject, body)


def classify_batch(emails: list[dict]) -> list[dict]:
    """Classify a list of email dicts in one Groq call. Returns results in same order."""
    from groq import Groq

    api_key = os.getenv('GROQ_API_KEY', '')
    if not api_key:
        return [_finalize(_empty_classification('other', 0.0, 'No GROQ_API_KEY'), e['subject'], e.get('body', '')) for e in emails]

    def _safe(text: str, limit: int) -> str:
        """Strip control chars and non-ASCII that can corrupt JSON responses."""
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text or '')
        text = text.encode('ascii', 'ignore').decode('ascii')
        return text[:limit].replace('\n', ' ')

    # Build numbered email list for the prompt
    lines = []
    for i, e in enumerate(emails):
        imp = 'IMPORTANT' if e.get('is_important') else 'normal'
        lines.append(
            f"[{i}] importance={imp}\n"
            f"  From: {_safe(e['from'], 80)}\n"
            f"  Subject: {_safe(e['subject'], 120)}\n"
            f"  Body: {_safe(e.get('body', ''), 150)}"
        )

    prompt = (
        _GROQ_SYSTEM + "\n\n"
        "Classify each email below. Return a JSON ARRAY with one object per email, indexed 0 to "
        f"{len(emails)-1}. No markdown, no extra text — only the JSON array.\n\n"
        "Each object must have:\n"
        '  {"index": N, "category": "...", "confidence": 0.0-1.0, '
        '"deadline": "date or null", "summary": "1 sentence or null"}\n\n'
        + '\n\n'.join(lines)
    )

    try:
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model='llama-3.1-8b-instant',
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.1,
            max_tokens=80 * len(emails),
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        # Extract just the JSON array in case Groq appends commentary after it
        start = raw.find('[')
        end = raw.rfind(']') + 1
        if start != -1 and end > start:
            raw = raw[start:end]
        parsed = json.loads(raw)

        # Build index → result map
        by_index = {}
        for item in parsed:
            idx = item.get('index')
            if idx is not None:
                by_index[idx] = {
                    'category':   item.get('category', 'other'),
                    'confidence': float(item.get('confidence', 0.5)),
                    'deadline':   item.get('deadline'),
                    'summary':    item.get('summary'),
                }

        results = []
        for i, e in enumerate(emails):
            base = by_index.get(i) or _classify_with_regex(e['subject'], e.get('body', ''))
            results.append(_finalize(base, e['subject'], e.get('body', '')))
        return results

    except Exception as ex:
        print(f'  ⚠️  Groq batch failed ({ex.__class__.__name__}: {ex}) — falling back to regex')
        return [_finalize(_classify_with_regex(e['subject'], e.get('body', '')), e['subject'], e.get('body', '')) for e in emails]


def _classify_with_groq(subject: str, body_snippet: str, sender: str, is_important: bool = False) -> dict:
    """Single-email Groq call (used as fallback)."""
    from groq import Groq

    api_key = os.getenv('GROQ_API_KEY', '')
    if not api_key:
        return _empty_classification('other', 0.0, 'No GROQ_API_KEY set')

    prompt = (
        _GROQ_SYSTEM + "\n\n"
        "Return ONLY valid JSON, no markdown:\n"
        '{"category":"...","confidence":0.0,"deadline":"...or null","summary":"...or null"}\n\n'
        f'Gmail marked this as important: {"YES — treat this seriously" if is_important else "no"}\n\n'
        f"From: {sender}\nSubject: {subject}\nBody: {body_snippet}"
    )

    try:
        client   = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model='llama-3.1-8b-instant',
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.1,
            max_tokens=150,
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        data = json.loads(raw)
        return {
            'category':   data.get('category', 'other'),
            'confidence': float(data.get('confidence', 0.5)),
            'deadline':   data.get('deadline'),
            'summary':    data.get('summary'),
        }
    except Exception as e:
        return _classify_with_regex(subject, body_snippet)


def _empty_classification(category: str, confidence: float, reason: str = '') -> dict:
    return {
        'category':     category,
        'confidence':   confidence,
        'deadline':     None,
        'summary':      reason or None,
        'action_items': [],
    }


def _classify_with_ollama(subject: str, body_snippet: str) -> tuple:
    """Legacy Ollama fallback — kept for offline use."""
    try:
        import ollama
        prompt = (
            "Classify this email into exactly one category: "
            "assignment, grade, transaction, deadline, opportunity, other.\n\n"
            f"Subject: {subject}\nBody: {body_snippet}\n\n"
            "Reply with just the category word, nothing else."
        )
        response = ollama.chat(
            model='llama3.2',
            messages=[{'role': 'user', 'content': prompt}]
        )
        raw      = response['message']['content'].strip().lower().split()[0]
        category = raw if raw in _PATTERNS else 'other'
        return category, 0.7
    except Exception:
        # Ollama not installed or model not pulled — silently fall back
        return 'other', 0.0


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

def scrape_personal_gmail(max_results: int = 200, days: int = 7) -> list:
    """Fetch emails from personal Gmail via OAuth2 API."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        print('⚠️  Google client libs not installed.')
        print('    pip install google-api-python-client google-auth-oauthlib')
        return []

    if not CREDS_PATH.exists():
        print(f'⚠️  {CREDS_PATH} not found.')
        print('    Download OAuth2 credentials from Google Cloud Console → save as credentials.json')
        return []

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

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
        with open(TOKEN_PATH, 'w') as f:
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

            emails.append({
                'id':         msg['id'],
                'from':       sender,
                'subject':    subject,
                'snippet':    full.get('snippet', ''),
                'date':       date_str,
                'body':       body[:2000],
                'source':     'personal',
                'is_important': 'IMPORTANT' in full.get('labelIds', []),
            })
        except Exception:
            continue

    return emails


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

    # ── School email ───────────────────────────────────────
    if email_arg:
        print('─' * 60)
        print('GMAIL — School Account (Playwright)')
        print('─' * 60)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page    = await (await browser.new_context()).new_page()
            school_emails = await scrape_school_gmail(page, email_arg, username_arg, password_arg)
            await browser.close()
        print(f'  ✅ {len(school_emails)} emails fetched.')
        all_emails.extend(school_emails)
    else:
        print('⏭️  School Gmail skipped (no credentials).')

    # ── Personal email ─────────────────────────────────────
    print()
    print('─' * 60)
    print('GMAIL — Personal Account (API)')
    print('─' * 60)
    personal_emails = scrape_personal_gmail(max_results=max_emails, days=days)
    print(f'  ✅ {len(personal_emails)} emails fetched.')
    all_emails.extend(personal_emails)

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

    results         = []
    category_counts = {}
    BATCH_SIZE      = 15

    # Split emails: instant noise filter vs Groq queue
    noise_results = []
    groq_queue    = []
    for em in all_emails:
        if _noise_check(em['subject'], em['from']):
            noise_results.append({**em, **_noise_result()})
        else:
            groq_queue.append(em)

    # Batch classify remaining emails
    groq_classified = []
    for i in range(0, len(groq_queue), BATCH_SIZE):
        batch = groq_queue[i:i + BATCH_SIZE]
        batch_end = min(i + BATCH_SIZE, len(groq_queue))
        print(f'  Classifying emails {i+1}–{batch_end} of {len(groq_queue)}...')
        classifications = classify_batch(batch)
        for em, cl in zip(batch, classifications):
            groq_classified.append({**em, **cl})

    all_classified = noise_results + groq_classified
    for r in all_classified:
        route_to_db(r, r)
        cat = r['category']
        category_counts[cat] = category_counts.get(cat, 0) + 1

    results = all_classified

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
    print(f'  ✅ {len(results)} classified → {OUTPUT_PATH}  (+{len(new_emails)} new → {HISTORY_PATH})')
    print(f'  {noise_count} noise filtered  |  {other_count} unclassified  |  {len(results) - noise_count - other_count} actionable')

    CATEGORY_ORDER = ['assignment', 'deadline', 'grade', 'transaction', 'opportunity', 'worth a look']
    CATEGORY_LABEL = {
        'assignment':   'ASSIGNMENTS',
        'deadline':     'DEADLINES',
        'grade':        'GRADES',
        'transaction':  'TRANSACTIONS',
        'opportunity':  'OPPORTUNITIES',
        'worth a look': 'WORTH A LOOK',
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
    ACTION_CATS = {'assignment', 'deadline', 'opportunity'}
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
