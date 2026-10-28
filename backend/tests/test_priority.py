"""Tests for the generic Priority Engine (core/priority.py)."""

import unittest
from datetime import datetime, timedelta, timezone

from core.items import Item
from core.priority import EFFORT_CAP_MINUTES, score_item


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _item(**kwargs) -> Item:
    defaults = dict(source="test", item_type="task", title="Test item")
    defaults.update(kwargs)
    return Item(**defaults)


def _due(days: float) -> str:
    dt = _now() + timedelta(days=days)
    return dt.isoformat().replace("+00:00", "Z")


class TestUrgency(unittest.TestCase):
    def test_overdue_urgency_is_max(self):
        item = _item(due_at=_due(-1))
        result = score_item(item)
        self.assertEqual(result["reasons"]["urgency_factor"], 1.0)

    def test_due_today_urgency(self):
        item = _item(due_at=_due(0.5))
        result = score_item(item)
        self.assertAlmostEqual(result["reasons"]["urgency_factor"], 0.9, places=1)

    def test_due_in_7_days_urgency(self):
        item = _item(due_at=_due(7))
        result = score_item(item)
        self.assertAlmostEqual(result["reasons"]["urgency_factor"], 0.5, delta=0.05)

    def test_due_in_30_days_urgency(self):
        item = _item(due_at=_due(30))
        result = score_item(item)
        self.assertAlmostEqual(result["reasons"]["urgency_factor"], 0.1, delta=0.05)

    def test_no_due_date_urgency(self):
        item = _item(due_at=None)
        result = score_item(item)
        self.assertEqual(result["reasons"]["urgency_factor"], 0.3)

    def test_far_future_urgency_is_floor(self):
        item = _item(due_at=_due(60))
        result = score_item(item)
        self.assertEqual(result["reasons"]["urgency_factor"], 0.10)


class TestEffortROI(unittest.TestCase):
    def test_no_effort_value_equals_consequence_norm(self):
        item = _item(consequence=4, effort_minutes=None)
        result = score_item(item)
        self.assertAlmostEqual(result["reasons"]["value"], 4 / 5, places=4)
        self.assertIsNone(result["reasons"]["effort_norm"])

    def test_low_effort_high_consequence_high_value(self):
        item = _item(consequence=5, effort_minutes=30)
        result = score_item(item)
        self.assertGreater(result["reasons"]["value"], 0.8)

    def test_high_effort_low_consequence_low_value(self):
        item = _item(consequence=1, effort_minutes=EFFORT_CAP_MINUTES)
        result = score_item(item)
        self.assertLess(result["reasons"]["value"], 0.25)

    def test_value_capped_at_one(self):
        item = _item(consequence=5, effort_minutes=1)
        result = score_item(item)
        self.assertLessEqual(result["reasons"]["value"], 1.0)

    def test_zero_effort_uses_floor(self):
        # effort_minutes=0 should not divide by zero
        item = _item(consequence=5, effort_minutes=0)
        result = score_item(item)
        self.assertLessEqual(result["score"], 1.0)


class TestScoreAndTier(unittest.TestCase):
    def test_score_clamped_between_0_and_1(self):
        item = _item(importance=5, consequence=5, due_at=_due(-1), effort_minutes=1)
        result = score_item(item)
        self.assertLessEqual(result["score"], 1.0)
        self.assertGreaterEqual(result["score"], 0.0)

    def test_s_tier_for_critical_overdue(self):
        item = _item(importance=5, consequence=5, due_at=_due(-1))
        result = score_item(item)
        self.assertEqual(result["tier"], "S")

    def test_d_tier_for_trivial_far_future(self):
        item = _item(importance=1, consequence=1, due_at=_due(60))
        result = score_item(item)
        self.assertEqual(result["tier"], "D")

    def test_high_effort_lowers_tier(self):
        low_effort = _item(importance=4, consequence=4, due_at=_due(3), effort_minutes=30)
        high_effort = _item(importance=4, consequence=4, due_at=_due(3), effort_minutes=240)
        self.assertGreater(score_item(low_effort)["score"], score_item(high_effort)["score"])

    def test_reasons_dict_keys_present(self):
        item = _item(importance=3, consequence=3, due_at=_due(5), effort_minutes=60)
        result = score_item(item)
        for key in ("importance_norm", "consequence_norm", "effort_norm",
                    "value", "urgency_factor", "raw", "due_at"):
            self.assertIn(key, result["reasons"])


if __name__ == "__main__":
    unittest.main()
