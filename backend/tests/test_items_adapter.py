"""Tests for the Gmail items adapter (apps/gmail/items_adapter.py).

Golden path: gate (_classify_one, no AI) -> emails_to_items -> score_item.
All addresses use example domains; no personal data.
"""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from apps.gmail.gmail_scraper import _classify_one
from apps.gmail.items_adapter import _extract_action_key, _parse_due_at, emails_to_items
from core.priority import score_item

SENT = "Sun, 15 Nov 2026 10:00:00 -0500"
NOW = datetime(2026, 11, 15, 15, 0, tzinfo=timezone.utc)

# Facebook-style sender counted as "engaged" (>=5 kept), so the gate lets it in
ENGAGED = {"replied_domains": set(), "opened_domains": {"facebookmail.com": 5}}
FREQ = {"facebookmail.com": 40, "example.edu": 10, "shop.example.com": 10}


def _email(id_, subject, body="", **overrides) -> dict:
    base = {
        "id": id_,
        "from": "Office <office@example.edu>",
        "subject": subject,
        "snippet": body[:100],
        "date": SENT,
        "body": body,
        "source": "personal",
        "gmail_tab": "personal",
        "is_important": False,
        "has_list_unsubscribe": False,
    }
    base.update(overrides)
    return base


def _pipeline(emails: list[dict]) -> dict:
    """Run emails through the gate and adapter; return items keyed by first email id."""
    classified = [_classify_one(e, None, ENGAGED, FREQ) for e in emails]
    items = emails_to_items(classified)
    return {item.raw_payload["email_ids"][0]: item for item in items}


class TestGoldenPath(unittest.TestCase):
    """What the Agenda receives for a small synthetic inbox."""

    @classmethod
    def setUpClass(cls):
        cls.items = _pipeline([
            _email(
                "fb", "Alex commented on your post",
                "Alex wrote: nice photo! Reply to this email to comment on this post.",
                **{"from": "Facebook <notification@facebookmail.com>", "gmail_tab": "social",
                   "has_list_unsubscribe": True},
            ),
            _email("invite", "Invitation: Advising meeting @ Tue Nov 18, 2026 2pm",
                   "You have been invited to the following event."),
            _email("fafsa", "Complete your FAFSA verification by Dec 1",
                   "Submit the requested documents to keep your aid."),
            _email("delivered", "Your package has been delivered",
                   "Your order was left at the front door.",
                   **{"from": "Shop <ship@shop.example.com>", "has_list_unsubscribe": True}),
            _email("done", "Your request is complete", "No further action is needed."),
            _email("card", "You spent $42.10 at Corner Cafe", "Card ending in 1234.",
                   **{"from": "Bank <alerts@bank.example.com>"}),
            _email("fraud", "Did you make this purchase?",
                   "Please verify the charge of $910.00 on your card.",
                   **{"from": "Bank <alerts@bank.example.com>"}),
            _email("digest", "Your weekly digest", "Top stories this week.",
                   **{"has_list_unsubscribe": True}),
            _email("welcome", "Welcome to the Honors Program", "We are glad you are here."),
            _email("welcome-action", "Welcome to orientation",
                   "Register for a session by Dec 3."),
        ])

    def test_facebook_notification_gets_no_fake_deadline(self):
        fb = self.items["fb"]
        self.assertIsNone(fb.due_at)
        self.assertNotEqual(fb.raw_payload["urgency_context"], "hard_deadline")
        self.assertNotIn(score_item(fb)["tier"], ("S", "A"))

    def test_invite_is_event(self):
        invite = self.items["invite"]
        self.assertEqual(invite.item_type, "event")
        self.assertEqual(invite.status, "open")
        self.assertTrue(invite.due_at.startswith("2026-11-18"))

    def test_fafsa_deadline_stays_open_task(self):
        fafsa = self.items["fafsa"]
        self.assertEqual(fafsa.item_type, "task")
        self.assertEqual(fafsa.status, "open")
        self.assertTrue(fafsa.due_at.startswith("2026-12-01"))

    def test_delivery_is_record(self):
        self.assertEqual(self.items["delivered"].item_type, "record")
        self.assertEqual(self.items["delivered"].raw_payload["record_type"], "logistics")

    def test_finished_request_is_record(self):
        self.assertEqual(self.items["done"].item_type, "record")

    def test_purchase_alert_is_financial_record(self):
        self.assertEqual(self.items["card"].item_type, "record")
        self.assertEqual(self.items["card"].raw_payload["record_type"], "financial")

    def test_fraud_check_stays_task(self):
        self.assertEqual(self.items["fraud"].item_type, "task")

    def test_digest_is_social_record(self):
        self.assertEqual(self.items["digest"].raw_payload["record_type"], "social")

    def test_plain_welcome_is_record(self):
        self.assertEqual(self.items["welcome"].item_type, "record")

    def test_welcome_with_action_stays_task(self):
        self.assertEqual(self.items["welcome-action"].item_type, "task")


class TestOrderGrouping(unittest.TestCase):
    def test_in_order_to_does_not_group(self):
        self.assertIsNone(_extract_action_key(_email("a", "Read this", "in order to register, log in")))

    def test_real_order_number_groups(self):
        self.assertEqual(
            _extract_action_key(_email("a", "Order #112-3345 shipped")), "order-112-3345"
        )

    def test_separate_emails_stay_separate(self):
        items = _pipeline([
            _email("x", "Housing form", "In order to keep your room, submit the form."),
            _email("y", "Parking permit", "In order to park, buy a permit."),
        ])
        self.assertEqual(set(items), {"x", "y"})


class TestParseDueAt(unittest.TestCase):
    def _due(self, subject, sent=SENT):
        return _parse_due_at([_email("d", subject, date=sent)], now=NOW)

    def test_january_in_november_is_next_year(self):
        self.assertTrue(self._due("Registration closes Jan 5").startswith("2027-01-05"))

    def test_recent_past_date_stays_this_year(self):
        self.assertTrue(self._due("Form was due Nov 10").startswith("2026-11-10"))

    def test_sept_abbreviation(self):
        due = self._due("Tuition due Sept 5", sent="Sat, 01 Aug 2026 09:00:00 -0400")
        self.assertTrue(due.startswith("2026-09-05"))

    def test_month_and_year_is_not_a_day(self):
        self.assertIsNone(self._due("Your October 2026 statement is ready"))

    def test_slash_date_wraps(self):
        self.assertTrue(self._due("Payment due 1/15").startswith("2027-01-15"))

    def test_reply_boilerplate_is_not_a_deadline(self):
        self.assertIsNone(self._due("New comment. Reply to this email to respond."))

    def test_unparseable_date_header_falls_back_to_now(self):
        due = _parse_due_at([_email("d", "Closes Jan 5", date="yesterday-ish")], now=NOW)
        self.assertTrue(due.startswith("2027-01-05"))


if __name__ == "__main__":
    unittest.main()
