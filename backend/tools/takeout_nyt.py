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
import html
import json
import mailbox
import os
import re
import sys
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

# ── NYT sender domains ───────────────────────────────────────────────────────

_NYT_DOMAINS = {
    'nytimes.com',
    'email.nytimes.com',
    'e.nytimes.com',
    'nyt.com',
    'mail.nytimes.com',
}

# Any nytimes.com host (newsletters link through nl.nytimes.com tracking redirects)
_NYT_LINK_RE = re.compile(r'https?://(?:[\w-]+\.)*(?:nytimes\.com|nyt\.com|nyti\.ms)/[^\s"\'<>)]+', re.IGNORECASE)

# <a href="...">text</a> — links are taken from the HTML before tags are stripped
_ANCHOR_RE = re.compile(r'<a\b[^>]*?\bhref\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)

# Blocks whose contents are never readable text
_NON_TEXT_BLOCK_RE = re.compile(r'<(style|script|head|title)\b.*?</\1\s*>|<!--.*?-->', re.IGNORECASE | re.DOTALL)

# Zero-width and filler characters newsletters use to pad preview text
_INVISIBLE_RE = re.compile(r'[​-‏­﻿͏⁠]+')


def _decode_header(value) -> str:
    """Decode RFC 2047 encoded words ('=?utf-8?B?...?=') in a header."""
    if not value:
        return ''
    try:
        text = str(make_header(decode_header(str(value))))
    except Exception:
        text = str(value)
    return re.sub(r'\s+', ' ', text).strip()


def _sender_domain(from_header: str) -> str:
    """Extract domain from a From header like 'Name <user@domain.com>'."""
    match = re.search(r'@([\w.-]+)', from_header)
    return match.group(1).lower() if match else ''


def _is_nyt(from_header: str) -> bool:
    domain = _sender_domain(from_header)
    return any(domain == d or domain.endswith('.' + d) for d in _NYT_DOMAINS)


# ── Body extraction ──────────────────────────────────────────────────────────

def _part_text(part) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ''
    charset = part.get_content_charset() or 'utf-8'
    try:
        return payload.decode(charset, errors='replace')
    except LookupError:  # unknown charset name
        return payload.decode('utf-8', errors='replace')


def _first_part(msg, content_type: str) -> str:
    for part in msg.walk():
        if part.get_content_type() == content_type:
            text = _part_text(part)
            if text:
                return text
    return ''


def _extract_body(msg) -> str:
    """Readable text body: text/plain if present, else stripped text/html."""
    plain = _first_part(msg, 'text/plain')
    if plain:
        return _clean_text(plain)
    return _strip_html(_first_part(msg, 'text/html'))


def _extract_links(msg) -> list[dict]:
    """NYT links with their anchor text, from the HTML part (plain text as fallback).

    Deduplicated by URL in order of appearance; anchors without text (images,
    tracking pixels) are skipped.
    """
    links: dict[str, str] = {}
    page = _first_part(msg, 'text/html')
    for href, inner in _ANCHOR_RE.findall(page):
        url = html.unescape(href).strip()
        text = _strip_html(inner)
        if text and _NYT_LINK_RE.fullmatch(url) and url not in links:
            links[url] = text
    if not links:
        for url in _NYT_LINK_RE.findall(_first_part(msg, 'text/plain')):
            links.setdefault(url, '')
    return [{'text': text, 'url': url} for url, text in links.items()]


def _clean_text(text: str) -> str:
    text = _INVISIBLE_RE.sub('', text.replace(' ', ' '))
    return re.sub(r'\s+', ' ', text).strip()


def _strip_html(page: str) -> str:
    text = _NON_TEXT_BLOCK_RE.sub(' ', page)
    text = re.sub(r'<br\s*/?>|</(p|div|tr|li|h\d)>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    return _clean_text(html.unescape(text))


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
            f"- [{link['text'] or link['url']}]({link['url']})" for link in article['links']
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

        article = {
            'id': msg_id,
            'from': _decode_header(from_header),
            'subject': _decode_header(msg.get('Subject', '')),
            'date': _parse_date(msg),
            'body': _extract_body(msg)[:5000],
            'links': _extract_links(msg),   # [{'text', 'url'}]
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
