#!/usr/bin/env python3
"""Extract NYT articles from a Google Takeout mbox export.

Outputs:
  - JSON  → backend/data/nyt_takeout.json  (always)
  - Obsidian markdown notes                (if --obsidian given)

Usage:
  python backend/tools/takeout_nyt.py --input ~/takeout/All_mail.mbox
  python backend/tools/takeout_nyt.py --input ~/takeout/All_mail.mbox --obsidian ~/vault/NYT/
"""

import argparse
import json
import mailbox
import os
import re
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

# ── NYT sender domains ───────────────────────────────────────────────────────

_NYT_DOMAINS = {
    'nytimes.com',
    'email.nytimes.com',
    'e.nytimes.com',
    'nyt.com',
    'mail.nytimes.com',
}

_NYT_LINK_RE = re.compile(r'https?://(?:www\.)?nytimes\.com/[^\s"<>\)]+')


def _sender_domain(from_header: str) -> str:
    """Extract domain from a From header like 'Name <user@domain.com>'."""
    match = re.search(r'@([\w.-]+)', from_header)
    return match.group(1).lower() if match else ''


def _is_nyt(from_header: str) -> bool:
    domain = _sender_domain(from_header)
    return any(domain == d or domain.endswith('.' + d) for d in _NYT_DOMAINS)


# ── Body extraction ──────────────────────────────────────────────────────────

def _extract_body(msg) -> str:
    """Get text body from an email.message.Message, preferring text/plain."""
    if msg.is_multipart():
        # Try text/plain first
        for part in msg.walk():
            if part.get_content_type() == 'text/plain':
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or 'utf-8'
                    return payload.decode(charset, errors='replace')
        # Fall back to text/html stripped
        for part in msg.walk():
            if part.get_content_type() == 'text/html':
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or 'utf-8'
                    return _strip_html(payload.decode(charset, errors='replace'))
        return ''
    else:
        payload = msg.get_payload(decode=True)
        if not payload:
            return ''
        charset = msg.get_content_charset() or 'utf-8'
        text = payload.decode(charset, errors='replace')
        if msg.get_content_type() == 'text/html':
            return _strip_html(text)
        return text


def _strip_html(html: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# ── Date parsing ─────────────────────────────────────────────────────────────

def _parse_date(msg) -> str:
    """Parse the Date header to ISO format, fall back to empty string."""
    date_str = msg.get('Date', '')
    if not date_str:
        return ''
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return date_str


# ── Obsidian output ──────────────────────────────────────────────────────────

def _slugify(text: str, max_len: int = 80) -> str:
    slug = re.sub(r'[^\w\s-]', '', text.lower())
    slug = re.sub(r'[\s_]+', '-', slug).strip('-')
    return slug[:max_len]


def _write_obsidian_note(article: dict, output_dir: str) -> str:
    date_prefix = article['date'][:10] if article['date'] else 'undated'
    slug = _slugify(article['subject']) or 'untitled'
    filename = f"{date_prefix}_{slug}.md"
    filepath = os.path.join(output_dir, filename)

    if os.path.exists(filepath):
        return ''  # skip duplicate

    links_section = ''
    if article['links']:
        links_section = '\n## Links\n' + '\n'.join(
            f'- {link}' for link in article['links']
        )

    note = f"""---
date: {date_prefix}
from: {article['from']}
subject: {article['subject']}
---

# {article['subject']}

{article['body'][:3000]}
{links_section}
"""
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(note)
    return filepath


# ── Main ─────────────────────────────────────────────────────────────────────

def extract_nyt(mbox_path: str, obsidian_dir: str | None = None) -> dict:
    """Parse mbox, extract NYT emails, return summary dict."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(script_dir, '..', 'data')
    os.makedirs(data_dir, exist_ok=True)
    json_path = os.path.join(data_dir, 'nyt_takeout.json')

    if obsidian_dir:
        os.makedirs(obsidian_dir, exist_ok=True)

    mbox_file = mailbox.mbox(mbox_path)
    seen_ids = set()
    articles = []
    total_scanned = 0
    nyt_found = 0

    for msg in mbox_file:
        total_scanned += 1
        if total_scanned % 1000 == 0:
            print(f"  scanned {total_scanned} emails...")

        from_header = msg.get('From', '')
        if not _is_nyt(from_header):
            continue

        nyt_found += 1
        msg_id = msg.get('Message-ID', '')
        if msg_id in seen_ids:
            continue
        seen_ids.add(msg_id)

        body = _extract_body(msg)
        links = list(dict.fromkeys(_NYT_LINK_RE.findall(body)))  # dedup, preserve order

        article = {
            'id': msg_id,
            'from': from_header,
            'subject': msg.get('Subject', ''),
            'date': _parse_date(msg),
            'body': body[:5000],
            'links': links,
            'source': 'takeout-highschool',
        }
        articles.append(article)

        if obsidian_dir:
            _write_obsidian_note(article, obsidian_dir)

    mbox_file.close()

    output = {
        'source': 'takeout-highschool',
        'extracted_at': datetime.now(timezone.utc).isoformat(),
        'total': len(articles),
        'articles': articles,
    }
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    return {
        'total_scanned': total_scanned,
        'nyt_found': nyt_found,
        'unique': len(articles),
        'json_path': json_path,
        'obsidian_dir': obsidian_dir,
    }


def main():
    parser = argparse.ArgumentParser(description='Extract NYT articles from Google Takeout mbox')
    parser.add_argument('--input', required=True, help='Path to the mbox file')
    parser.add_argument('--obsidian', default=None, help='Output directory for Obsidian markdown notes')
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"Error: {args.input} not found")
        sys.exit(1)

    print(f"Parsing {args.input}...")
    result = extract_nyt(args.input, args.obsidian)

    print(f"\nDone.")
    print(f"  Scanned:  {result['total_scanned']} emails")
    print(f"  NYT:      {result['nyt_found']} found")
    print(f"  Unique:   {result['unique']} after dedup")
    print(f"  JSON:     {result['json_path']}")
    if result['obsidian_dir']:
        print(f"  Obsidian: {result['obsidian_dir']}")


if __name__ == '__main__':
    main()
