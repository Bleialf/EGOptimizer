"""EG-absorption headline stats (drives the /stats endpoint + HA sensors)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from brain.analysis.stats import absorption_stats
from brain.records import EnergyRecord


def _rec(ts, feed, absorbed, surplus):
    return EnergyRecord(ts, "AT_FEED", "netznoe", feed_in_kwh=feed,
                        eg_absorbed_kwh=absorbed, eg_surplus_kwh=surplus)


class TestAbsorptionStats(unittest.TestCase):
    def test_empty(self):
        s = absorption_stats([])
        self.assertEqual(s["settled_intervals"], 0)
        self.assertEqual(s["fed_intervals"], 0)
        self.assertIsNone(s["first_ts"])
        self.assertIsNone(s["best_hour"])
        self.assertEqual(s["all_time"]["fed_kwh"], 0.0)

    def test_only_unsettled_returns_early(self):
        # feed-in present but EG not settled -> no fed/settled stats, ts still set
        recs = [_rec(datetime(2026, 6, 20, 12, 0), 1.0, None, None)]
        s = absorption_stats(recs)
        self.assertEqual(s["settled_intervals"], 0)
        self.assertEqual(s["fed_intervals"], 0)
        self.assertEqual(s["first_ts"], "2026-06-20T12:00:00")
        self.assertIsNone(s["last_settled_ts"])
        self.assertIsNone(s["best_hour"])

    def test_totals_rates_and_best_worst_hour(self):
        recent = datetime(2026, 6, 1, 12, 0)
        old = recent - timedelta(days=60)
        recs = [
            _rec(recent, 1.0, 1.0, 0.0),                 # hour 12: fully absorbed (censored)
            _rec(recent.replace(hour=6), 1.0, 0.2, 0.8),  # hour 6: mostly spilled
            _rec(old.replace(hour=3), 1.0, 0.5, 0.5),     # old (outside 30d window)
            _rec(recent.replace(hour=13), 0.0, 0.0, 0.0),  # zero feed -> ignored
            _rec(recent.replace(hour=14), 0.5, None, None),  # unsettled -> ignored
        ]
        s = absorption_stats(recs)

        self.assertEqual(s["settled_intervals"], 4)   # 3 settled-fed + the zero-feed settled
        self.assertEqual(s["fed_intervals"], 3)
        at = s["all_time"]
        self.assertAlmostEqual(at["fed_kwh"], 3.0)
        self.assertAlmostEqual(at["absorbed_kwh"], 1.7)
        self.assertAlmostEqual(at["surplus_kwh"], 1.3)
        self.assertAlmostEqual(at["absorption_rate"], round(1.7 / 3.0, 4))
        self.assertAlmostEqual(at["censored_pct"], round(1 / 3, 4))

        # recent window excludes the 60-day-old interval
        self.assertEqual(s["recent"]["intervals"], 2)
        self.assertAlmostEqual(s["recent"]["fed_kwh"], 2.0)

        # hour 12 absorbed 100%, hour 6 only 20%
        self.assertEqual(s["best_hour"], 12)
        self.assertAlmostEqual(s["best_hour_rate"], 1.0)
        self.assertEqual(s["worst_hour"], 6)
        self.assertAlmostEqual(s["worst_hour_rate"], 0.2)

        self.assertEqual(s["last_settled_ts"], "2026-06-01T13:00:00")


if __name__ == "__main__":
    unittest.main()
