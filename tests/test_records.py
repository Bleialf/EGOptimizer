"""EnergyRecord semantics: settled / censored flags."""

from __future__ import annotations

import unittest
from datetime import datetime

from brain.records import EnergyRecord


def _rec(feed, absorbed, surplus):
    return EnergyRecord(
        timestamp=datetime(2026, 5, 20, 22, 0),
        meter_id="AT00",
        provider="netznoe",
        feed_in_kwh=feed,
        eg_absorbed_kwh=absorbed,
        eg_surplus_kwh=surplus,
    )


class TestRecordFlags(unittest.TestCase):
    def test_settled_when_eg_present(self):
        self.assertTrue(_rec(1.0, 0.5, 0.5).eg_settled)

    def test_unsettled_when_eg_none(self):
        self.assertFalse(_rec(1.0, None, None).eg_settled)

    def test_fully_absorbed_zero_surplus(self):
        self.assertTrue(_rec(1.0, 1.0, 0.0).fully_absorbed)

    def test_not_fully_absorbed_with_surplus(self):
        self.assertFalse(_rec(1.0, 0.6, 0.4).fully_absorbed)

    def test_not_fully_absorbed_without_feed(self):
        # no feed-in -> nothing to absorb -> not a censoring signal
        self.assertFalse(_rec(0.0, 0.0, 0.0).fully_absorbed)

    def test_unsettled_never_fully_absorbed(self):
        self.assertFalse(_rec(1.0, None, None).fully_absorbed)

    def test_tiny_surplus_below_epsilon_counts_as_absorbed(self):
        self.assertTrue(_rec(1.0, 1.0, 1e-9).fully_absorbed)


if __name__ == "__main__":
    unittest.main()
