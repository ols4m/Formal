"""Tests for the generic Priority Engine (core/priority.py)."""

import unittest
from datetime import datetime, timedelta, timezone

from core.items import Item
from core.priority import score_item, W_URGENCY, W_CONSEQUENCE, W_IMPORTANCE


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


class TestAdditiveScoring(unittest.TestCase):
    def test_due_tomorrow_c1_outranks_no_date_c2(self):
        """Urgency dominates — due tomorrow beats no-date even with lower consequence."""
        tomorrow = _item(importance=2, consequence=1, due_at=_due(0.5))
        no_date = _item(importance=3, consequence=2, due_at=None)
        self.assertGreater(score_item(tomorrow)["score"], score_item(no_date)["score"])

    def test_overdue_high_consequence_is_s_tier(self):
        item = _item(importance=5, consequence=5, due_at=_due(-1))
        result = score_item(item)
        self.assertEqual(result["tier"], "S")

    def test_no_date_low_everything_is_bottom_tier(self):
        """Lowest possible inputs — should be C or D tier."""
        item = _item(importance=1, consequence=1, due_at=None)
        result = score_item(item)
        self.assertIn(result["tier"], ("C", "D"))

    def test_is_important_nudges_score_up(self):
        base = _item(importance=3, consequence=3, due_at=None)
        important = _item(importance=3, consequence=3, due_at=None,
                         raw_payload={"is_important": True})
        self.assertGreater(score_item(important)["score"], score_item(base)["score"])

    def test_high_consequence_no_date_still_ranks_well(self):
        """FAFSA-like: high consequence + available_window should be A tier."""
        item = _item(importance=4, consequence=3, due_at=None,
                     raw_payload={"urgency_context": "available_window"})
        result = score_item(item)
        self.assertIn(result["tier"], ("S", "A"))

    def test_no_factor_can_zero_score(self):
        """Even with minimum inputs, score should be above zero."""
        item = _item(importance=1, consequence=1, due_at=_due(60))
        result = score_item(item)
        self.assertGreater(result["score"], 0.0)

    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(W_URGENCY + W_CONSEQUENCE + W_IMPORTANCE, 1.0)


class TestScoreAndTier(unittest.TestCase):
    def test_score_clamped_between_0_and_1(self):
        item = _item(importance=5, consequence=5, due_at=_due(-1))
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

    def test_reasons_dict_keys_present(self):
        item = _item(importance=3, consequence=3, due_at=_due(5))
        result = score_item(item)
        for key in ("importance_norm", "consequence_norm",
                    "urgency_factor", "raw", "due_at"):
            self.assertIn(key, result["reasons"])


if __name__ == "__main__":
    unittest.main()
