"""Tests for user feedback: done/dismiss resolution and sender reputation."""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import db
from core.items import Item, ItemRepository


def _due(days: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat().replace("+00:00", "Z")


def _item(sid, title, domain, **kw) -> Item:
    return Item(
        source="test", source_item_id=sid, item_type="task", title=title,
        raw_payload={"sender_domain": domain}, **kw,
    )


class FeedbackTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patch = patch.object(db, "DB_PATH", Path(self._tmp.name) / "formal.db")
        self._patch.start()
        with redirect_stdout(io.StringIO()):
            db.init_db()
        self.conn = db.get_db()
        self.repo = ItemRepository(connection=self.conn)

    def tearDown(self):
        self.conn.close()
        self._patch.stop()
        self._tmp.cleanup()

    def _id(self, sid):
        return self.conn.execute(
            "SELECT id FROM items WHERE source_item_id = ?", (sid,)
        ).fetchone()["id"]

    def _reputation(self, domain):
        row = self.conn.execute(
            "SELECT done_count, dismiss_count FROM sender_reputation WHERE domain = ?",
            (domain,),
        ).fetchone()
        return (row["done_count"], row["dismiss_count"]) if row else (0, 0)


class TestRecordSenderFeedback(FeedbackTestCase):
    def test_dismiss_and_done_are_counted_per_sender(self):
        self.repo.upsert([_item("a", "A", "news.example.com"), _item("b", "B", "news.example.com")])
        self.assertTrue(self.repo.record_sender_feedback(self._id("a"), "dismiss"))
        self.assertTrue(self.repo.record_sender_feedback(self._id("b"), "dismiss"))
        self.repo.record_sender_feedback(self._id("a"), "done")
        self.assertEqual(self._reputation("news.example.com"), (1, 2))

    def test_item_without_sender_is_ignored(self):
        self.repo.upsert([Item(source="test", source_item_id="m", item_type="task", title="Manual")])
        self.assertFalse(self.repo.record_sender_feedback(self._id("m"), "done"))

    def test_unknown_outcome_rejected(self):
        with self.assertRaises(ValueError):
            self.repo.record_sender_feedback("x", "maybe")


class TestAgendaRowNumbers(FeedbackTestCase):
    def test_dismiss_row_hits_the_printed_row_even_with_closed_items(self):
        from apps.agenda import main as agenda

        self.repo.upsert([
            _item("urgent", "Urgent form", "school.example.edu", due_at=_due(1),
                  importance=5, consequence=5),
            _item("later", "Later thing", "shop.example.com", due_at=_due(20)),
            _item("closed", "Already done", "old.example.com", due_at=_due(0.5),
                  importance=5, consequence=5),
        ])
        self.repo.update_status(self._id("closed"), "done")

        with redirect_stdout(io.StringIO()):
            ranked = agenda._load_open_items(self.repo, conn=self.conn)
            self.assertEqual([i.title for i, _ in ranked][0], "Urgent form")
            self.assertNotIn("Already done", [i.title for i, _ in ranked])
            agenda.mark_dismissed("1")

        status = self.conn.execute(
            "SELECT status FROM items WHERE id = ?", (self._id("urgent"),)
        ).fetchone()["status"]
        self.assertEqual(status, "archived")
        self.assertEqual(self._reputation("school.example.edu"), (0, 1))
        self.assertEqual(self._reputation("old.example.com"), (0, 0))


class TestRankedItemsShortIds(FeedbackTestCase):
    def test_short_prefix_resolves(self):
        from apps.agenda.ranked_items import _resolve

        self.repo.upsert([_item("a", "Pay rent", "landlord.example.com")])
        full = self._id("a")
        self.assertEqual(_resolve(self.repo, full[:8]), (full, "Pay rent"))
        self.assertEqual(_resolve(self.repo, full), (full, "Pay rent"))
        self.assertEqual(_resolve(self.repo, "zzzzzzzz"), (None, None))


if __name__ == "__main__":
    unittest.main()
