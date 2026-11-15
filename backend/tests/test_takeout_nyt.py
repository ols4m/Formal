"""Tests for the Takeout NYT extractor (backend/tools/takeout_nyt.py)."""

import sys
import unittest
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from takeout_nyt import _decode_header, _extract_body, _extract_links

HTML = """<html><head><title>The Morning</title>
<style>#outlook a{padding:0} body{margin:0}</style></head>
<body><!-- preheader --><div>‌͏ Good morning.&nbsp;Here&#8217;s the news.</div>
<a href="https://nl.nytimes.com/f/a/abc?x=1&amp;y=2">Lasers that kill mosquitoes</a>
<a href="https://nl.nytimes.com/f/a/abc?x=1&amp;y=2">Lasers that kill mosquitoes</a>
<a href="https://nl.nytimes.com/f/a/pixel"><img src="p.gif"></a>
<a href="https://example.com/ad">Sponsor</a>
<script>track()</script></body></html>"""


def _msg(html=None, plain=None, subject="Hello") -> EmailMessage:
    m = EmailMessage()
    m["From"] = "The New York Times <nytdirect@nytimes.com>"
    m["Subject"] = subject
    if plain:
        m.set_content(plain)
    if html:
        if plain:
            m.add_alternative(html, subtype="html")
        else:
            m.set_content(html, subtype="html")
    return m


class TestTakeoutNyt(unittest.TestCase):
    def test_encoded_subject_is_decoded(self):
        raw = "=?utf-8?B?QnJlYWtpbmcgbmV3czogRmVkZXJhbCBhZ2VudHMgc2VhcmNo?="
        self.assertEqual(_decode_header(raw), "Breaking news: Federal agents search")

    def test_html_body_drops_css_scripts_and_filler(self):
        body = _extract_body(_msg(html=HTML))
        self.assertTrue(body.startswith("Good morning. Here’s the news."), body)
        for junk in ("padding", "track()", "The Morning", "‌", "͏"):
            self.assertNotIn(junk, body)

    def test_links_come_from_html_with_text_and_tracking_hosts(self):
        links = _extract_links(_msg(html=HTML))
        self.assertEqual(links, [{
            "text": "Lasers that kill mosquitoes",
            "url": "https://nl.nytimes.com/f/a/abc?x=1&y=2",
        }])

    def test_plain_text_part_preferred_for_body(self):
        body = _extract_body(_msg(html=HTML, plain="Plain version https://www.nytimes.com/2026/a.html"))
        self.assertEqual(body, "Plain version https://www.nytimes.com/2026/a.html")

    def test_plain_text_links_used_when_no_html(self):
        links = _extract_links(_msg(plain="Read https://www.nytimes.com/2026/a.html today"))
        self.assertEqual(links, [{"text": "", "url": "https://www.nytimes.com/2026/a.html"}])


if __name__ == "__main__":
    unittest.main()
