"""Storage: idempotent upsert, in-place EG upgrade, decision log."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from brain.records import EnergyRecord
from brain.storage import Store


def _rec(ts, feed, absorbed=None, surplus=None, meter="AT00"):
    return EnergyRecord(
        timestamp=ts,
        meter_id=meter,
        provider="netznoe",
        feed_in_kwh=feed,
        eg_absorbed_kwh=absorbed,
        eg_surplus_kwh=surplus,
    )


class TestStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "t.sqlite"
        self.store = Store(self.db)

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def test_insert_and_fetch(self):
        ts = datetime(2026, 5, 20, 22, 0)
        self.store.upsert_many([_rec(ts, 1.0, 0.6, 0.4)])
        rows = self.store.fetch_all()
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0].feed_in_kwh, 1.0)

    def test_reimport_is_idempotent(self):
        ts = datetime(2026, 5, 20, 22, 0)
        rec = _rec(ts, 1.0, 0.6, 0.4)
        self.store.upsert_many([rec])
        self.store.upsert_many([rec])  # same key again
        self.assertEqual(len(self.store.fetch_all()), 1)

    def test_unsettled_then_settled_upgrade(self):
        ts = datetime(2026, 6, 18, 23, 45)
        self.store.upsert_many([_rec(ts, 0.13, None, None)])  # first export: unsettled
        self.store.upsert_many([_rec(ts, 0.13, 0.10, 0.03)])  # later export: settled
        r = self.store.fetch_all()[0]
        self.assertAlmostEqual(r.eg_absorbed_kwh, 0.10)
        self.assertAlmostEqual(r.eg_surplus_kwh, 0.03)

    def test_settled_not_clobbered_by_later_null(self):
        # COALESCE must keep a known EG value if a later import lacks it.
        ts = datetime(2026, 6, 18, 23, 45)
        self.store.upsert_many([_rec(ts, 0.13, 0.10, 0.03)])
        self.store.upsert_many([_rec(ts, 0.13, None, None)])
        r = self.store.fetch_all()[0]
        self.assertAlmostEqual(r.eg_absorbed_kwh, 0.10)

    def test_distinct_meters_coexist(self):
        ts = datetime(2026, 5, 20, 22, 0)
        self.store.upsert_many([_rec(ts, 1.0, 0.6, 0.4, meter="AT01")])
        self.store.upsert_many([_rec(ts, 2.0, 1.0, 1.0, meter="AT02")])
        self.assertEqual(len(self.store.fetch_all()), 2)

    def test_cross_file_overlap_dedups(self):
        # Two "files" sharing the boundary interval -> one stored row.
        a = [_rec(datetime(2025, 12, 31, 23, 45), 0.5, 0.5, 0.0),
             _rec(datetime(2026, 1, 1, 0, 0), 0.4, 0.4, 0.0)]
        b = [_rec(datetime(2026, 1, 1, 0, 0), 0.4, 0.4, 0.0),  # overlap
             _rec(datetime(2026, 1, 1, 0, 15), 0.3, 0.3, 0.0)]
        self.store.upsert_many(a)
        self.store.upsert_many(b)
        self.assertEqual(len(self.store.fetch_all()), 3)

    def test_summary(self):
        self.store.upsert_many([
            _rec(datetime(2026, 5, 20, 22, 0), 1.0, 0.6, 0.4),
            _rec(datetime(2026, 6, 18, 23, 45), 0.13, None, None),
        ])
        s = self.store.summary()
        self.assertEqual(s["n"], 2)
        self.assertEqual(s["settled"], 1)

    def test_decision_log(self):
        rid = self.store.log_decision(
            decided_at="2026-05-20T22:00", request="{}", response="{}",
            feed_kw=0.5, eg_budget_kwh=4.2, explore=True,
        )
        self.assertIsInstance(rid, int)
        row = self.store.conn.execute(
            "SELECT feed_kw, explore FROM decisions WHERE id=?", (rid,)
        ).fetchone()
        self.assertAlmostEqual(row["feed_kw"], 0.5)
        self.assertEqual(row["explore"], 1)


if __name__ == "__main__":
    unittest.main()
