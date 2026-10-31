import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from core import db
from core.items import Item, ItemRepository, sync_adapter
from apps.gradebook.calculator import (
    calculate_assignment_impact,
    calculate_class_grade_weighted,
    calculate_gpa,
    term_assignments,
)
from apps.gradebook.priority import calculate_priority
from apps.gradebook import powerschool_scraper
from apps.gradebook.powerschool_scraper import save_to_db
from core.db import _migrate_period_columns
from core.profile import get_current_term, validate_profile


def make_profile():
    return {
        "terms": [
            {
                "name": "Term A",
                "start": "2026-08-01",
                "end": "2026-12-31",
                "source_periods": ["S1", "S2"],
            },
            {
                "name": "Term B",
                "start": "2027-01-01",
                "end": "2027-05-31",
                "source_periods": ["S3"],
            },
        ],
        "grading": {
            "target_grade": 90,
            "scale": {"A": 90, "B": 80, "F": 0},
            "gpa_points": {"A": 4.0, "B": 3.0, "F": 0.0},
            "weighted_bonus": 0.0,
            "rounding": {"round_up_at": 0.5},
            "lookahead_days": 14,
        },
        "categories": {
            "Essay": {"weight": 0.5, "floor": 0},
            "Exam": {"weight": 0.5, "floor": 0},
        },
        "courses": {
            "Writing": {
                "credits": 3,
                "weighted": False,
                "category_weights": {"Essay": 0.2, "Exam": 0.8},
            },
            "History": {"credits": 1, "weighted": False},
        },
        "context": {"interests": [], "employer": None, "schools": []},
    }


class ProfileTermsTests(unittest.TestCase):
    def test_current_term_comes_from_configured_dates(self):
        profile = make_profile()
        self.assertEqual(get_current_term(profile, date(2026, 10, 1))["name"], "Term A")
        self.assertEqual(get_current_term(profile, date(2027, 3, 1))["name"], "Term B")

    def test_gpa_is_credit_weighted_and_uses_mapped_source_periods(self):
        profile = make_profile()
        grades_data = {
            "grades": [
                {
                    "course": "Writing",
                    "type": "Regular",
                    "grades": {"S1": {"letter": "B"}, "S2": {"letter": "A"}},
                    "assignments": {"S1": [{"name": "Draft"}], "S2": [{"name": "Essay"}]},
                },
                {
                    "course": "History",
                    "type": "Regular",
                    "grades": {"S2": {"letter": "B"}},
                },
            ]
        }
        gpa, breakdown = calculate_gpa(
            grades_data, term="Term A", weighted=True, profile=profile
        )
        self.assertEqual(gpa, 3.75)
        self.assertEqual([row["credits"] for row in breakdown], [3.0, 1.0])
        self.assertEqual(
            [item["name"] for item in term_assignments(grades_data["grades"][0], "Term A", profile)],
            ["Draft", "Essay"],
        )

    def test_course_category_weights_override_profile_defaults(self):
        result = calculate_assignment_impact(
            category="Essay",
            points_possible=100,
            score=100,
            category_avg=80,
            category_points_so_far=100,
            course_name="Writing",
            profile=make_profile(),
        )
        self.assertEqual(result["weight"], 0.2)
        self.assertEqual(result["delta_class"], 2.0)

    def test_weighted_class_grade_renormalizes_ungraded_categories(self):
        grade = calculate_class_grade_weighted(
            [{"category": "Essay", "earned": 80, "possible": 100, "percent": 80}],
            course_name="Writing",
            profile=make_profile(),
        )
        self.assertEqual(grade, 80.0)

    def test_priority_uses_profile_target_and_course_weight(self):
        result = calculate_priority(
            category="Essay",
            points_possible=100,
            current_class_grade=80,
            category_points_so_far=0,
            due_date=date.today(),
            course_name="Writing",
            profile=make_profile(),
        )
        self.assertEqual(result["metrics"]["wc"], 0.2)
        self.assertAlmostEqual(result["priority"], 0.02)

    def test_legacy_database_period_column_is_migrated_without_losing_label(self):
        profile = make_profile()
        validate_profile(profile)
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute(
            "CREATE TABLE grades (id INTEGER PRIMARY KEY, scraped_at TEXT, quarter TEXT)"
        )
        connection.execute(
            "INSERT INTO grades (scraped_at, quarter) VALUES ('2026-10-01T12:00:00Z', 'Q1')"
        )

        _migrate_period_columns(connection, "grades", profile)

        columns = {row["name"] for row in connection.execute("PRAGMA table_info(grades)")}
        row = connection.execute("SELECT term, source_period FROM grades").fetchone()
        self.assertIn("term", columns)
        self.assertNotIn("quarter", columns)
        self.assertEqual(row["term"], "Term A")
        self.assertEqual(row["source_period"], "Q1")
        connection.close()

    def test_new_database_uses_term_and_source_period_columns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "formal.db"
            with patch.object(db, "DB_PATH", database_path):
                db.init_db()
                connection = db.get_db()
                for table in ("grades", "assignments"):
                    columns = {
                        row["name"]
                        for row in connection.execute(f"PRAGMA table_info({table})")
                    }
                    self.assertIn("term", columns)
                    self.assertIn("source_period", columns)
                    self.assertNotIn("quarter", columns)
                connection.close()

    def test_scraper_persists_term_and_portal_period_separately(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "formal.db"
            grades_data = {
                "scraped_at": "2026-10-01T12:00:00Z",
                "term": "Term A",
                "grades": [
                    {
                        "course": "Writing",
                        "type": "Regular",
                        "grades": {"Q1": {"letter": "A", "numeric": 95}},
                        "assignments": {
                            "Q2": [
                                {
                                    "name": "Essay",
                                    "category": "Homework",
                                    "earned": 10,
                                    "possible": 10,
                                }
                            ]
                        },
                    }
                ],
            }
            with patch.object(db, "DB_PATH", database_path):
                with patch.object(powerschool_scraper, "load_profile", return_value=make_profile()):
                    save_to_db(grades_data)
                connection = db.get_db()
                grade = connection.execute(
                    "SELECT term, source_period FROM grades"
                ).fetchone()
                assignment = connection.execute(
                    "SELECT term, source_period FROM assignments"
                ).fetchone()
                self.assertEqual(tuple(grade), ("Term A", "Q1"))
                self.assertEqual(tuple(assignment), ("Term A", "Q2"))
                connection.close()


class ItemsTests(unittest.IsolatedAsyncioTestCase):
    async def test_adapter_sync_upserts_stably_without_overwriting_user_status(self):
        class ExampleAdapter:
            source = "calendar"

            async def fetch(self):
                return [
                    Item(
                        source="calendar",
                        source_item_id="event-1",
                        item_type="event",
                        title="Application meeting",
                        due_at="2026-10-25T15:00:00Z",
                        raw_payload={"calendar": "personal"},
                    )
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "formal.db"
            with patch.object(db, "DB_PATH", database_path):
                db.init_db()
                repository = ItemRepository()
                adapter = ExampleAdapter()
                self.assertEqual(await sync_adapter(adapter, repository), 1)
                item = repository.list_items()[0]
                self.assertEqual(item["source"], "calendar")
                self.assertEqual(item["item_type"], "event")
                self.assertEqual(item["raw_payload"], {"calendar": "personal"})

                repository._connection.execute(
                    "UPDATE items SET status = 'done' WHERE source_item_id = 'event-1'"
                )
                self.assertEqual(await sync_adapter(adapter, repository), 1)
                items = repository.list_items(status=None)
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["status"], "done")
                repository.close()

    async def test_adapter_cannot_write_items_under_another_source(self):
        class MismatchedAdapter:
            source = "gmail"

            async def fetch(self):
                return [
                    Item(source="calendar", item_type="task", title="Wrong source")
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "formal.db"
            with patch.object(db, "DB_PATH", database_path):
                db.init_db()
                repository = ItemRepository()
                with self.assertRaisesRegex(ValueError, "mismatched source"):
                    await sync_adapter(MismatchedAdapter(), repository)
                self.assertEqual(repository.list_items(status=None), [])
                repository.close()


class ArchivePastEventsTests(unittest.TestCase):
    def test_past_event_gets_archived(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "formal.db"
            with patch.object(db, "DB_PATH", database_path):
                db.init_db()
                repo = ItemRepository()
                repo.upsert([Item(
                    source="test", item_type="event", title="Past event",
                    due_at="2026-10-01T12:00:00Z",
                )])
                archived = repo.archive_past_events()
                self.assertEqual(archived, 1)
                items = repo.list_items(status="archived")
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["title"], "Past event")
                repo.close()

    def test_future_event_stays_open(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "formal.db"
            with patch.object(db, "DB_PATH", database_path):
                db.init_db()
                repo = ItemRepository()
                repo.upsert([Item(
                    source="test", item_type="event", title="Future event",
                    due_at="2099-12-31T23:59:00Z",
                )])
                archived = repo.archive_past_events()
                self.assertEqual(archived, 0)
                items = repo.list_items(status="open")
                self.assertEqual(len(items), 1)
                repo.close()

    def test_past_task_stays_open(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "formal.db"
            with patch.object(db, "DB_PATH", database_path):
                db.init_db()
                repo = ItemRepository()
                repo.upsert([Item(
                    source="test", item_type="task", title="Overdue task",
                    due_at="2026-10-01T12:00:00Z",
                )])
                archived = repo.archive_past_events()
                self.assertEqual(archived, 0)
                items = repo.list_items(status="open")
                self.assertEqual(len(items), 1)
                repo.close()


if __name__ == "__main__":
    unittest.main()
