"""Ingestion cleaning: drop physically-impossible feed-in spikes."""

from __future__ import annotations

import unittest
from datetime import datetime

from brain.ingest.clean import filter_outliers
from brain.records import EnergyRecord


def _rec(feed):
    return EnergyRecord(timestamp=datetime(2025, 7, 24, 0, 0), meter_id="AT00",
                        provider="netznoe", feed_in_kwh=feed,
                        eg_absorbed_kwh=None, eg_surplus_kwh=None)


class TestFilterOutliers(unittest.TestCase):
    def test_drops_meter_init_spike(self):
        kept, dropped = filter_outliers([_rec(7.556), _rec(0.13), _rec(0.0)], 5.0)
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(dropped), 1)
        self.assertAlmostEqual(dropped[0].feed_in_kwh, 7.556)

    def test_keeps_everything_below_threshold(self):
        kept, dropped = filter_outliers([_rec(0.5), _rec(4.99)], 5.0)
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, [])

    def test_boundary_value_kept(self):
        kept, dropped = filter_outliers([_rec(5.0)], 5.0)  # exactly at limit -> kept
        self.assertEqual(len(kept), 1)


if __name__ == "__main__":
    unittest.main()
