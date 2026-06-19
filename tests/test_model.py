"""Phase 3 bandit: context bucketing, censored capacity, UCB, scheduling."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from brain.model.capacity import (
    COLD_START_PROBE_KWH,
    CapacityModel,
    aggregate_hourly,
)
from brain.model.context import bucket_key, daytype, season
from brain.model.schedule import _window_hours, feed_now_kw, plan_feed
from brain.records import EnergyRecord


def _rec(ts, feed, absorbed, surplus):
    return EnergyRecord(timestamp=ts, meter_id="AT00", provider="netznoe",
                        feed_in_kwh=feed, eg_absorbed_kwh=absorbed, eg_surplus_kwh=surplus)


class TestContext(unittest.TestCase):
    def test_season(self):
        self.assertEqual(season(1), "winter")
        self.assertEqual(season(7), "summer")
        self.assertEqual(season(4), "spring")

    def test_daytype(self):
        self.assertEqual(daytype(0), "weekday")   # Monday
        self.assertEqual(daytype(6), "weekend")   # Sunday

    def test_bucket_key_stable(self):
        self.assertEqual(bucket_key(datetime(2026, 1, 5, 22, 0)), "winter|weekday|22")


class TestAggregation(unittest.TestCase):
    def test_hourly_sums_quarter_hours(self):
        base = datetime(2026, 1, 5, 22, 0)
        recs = [_rec(base.replace(minute=m), 0.1, 0.1, 0.0) for m in (0, 15, 30, 45)]
        obs = aggregate_hourly(recs)
        self.assertEqual(len(obs), 1)
        self.assertAlmostEqual(obs[0].feed_kwh, 0.4)
        self.assertTrue(obs[0].censored)  # all absorbed, no surplus

    def test_unsettled_ignored(self):
        obs = aggregate_hourly([_rec(datetime(2026, 1, 5, 22, 0), 0.1, None, None)])
        self.assertEqual(obs, [])

    def test_uncensored_when_surplus(self):
        obs = aggregate_hourly([_rec(datetime(2026, 1, 5, 22, 0), 1.0, 0.6, 0.4)])
        self.assertFalse(obs[0].censored)


class TestCapacityModel(unittest.TestCase):
    def test_censored_history_triggers_probe(self):
        # Always fully absorbed -> ceiling never seen -> recommend ABOVE the max.
        ts = datetime(2026, 1, 5, 22, 0)
        recs = [_rec(ts.replace(day=d), 1.0, 1.0, 0.0) for d in range(1, 8)]
        m = CapacityModel(aggressiveness=0.2).fit(recs)
        cap, explore, _ = m.recommend_capacity(ts)
        self.assertTrue(explore)
        self.assertGreater(cap, 1.0)  # probes above the 1.0 we always saw absorbed

    def test_uncensored_confident_exploits(self):
        # Enough observations with surplus -> ceiling known -> aim at it, no probe.
        ts = datetime(2026, 1, 5, 22, 0)
        recs = [_rec(ts.replace(day=d), 2.0, 1.2, 0.8) for d in range(1, 9)]
        m = CapacityModel().fit(recs)
        cap, explore, stats = m.recommend_capacity(ts)
        self.assertFalse(explore)
        self.assertAlmostEqual(cap, 1.2)
        # weekday bucket only (Jan 1-8 2026 mixes weekdays/weekends)
        self.assertGreaterEqual(stats.n, 5)  # past MIN_CONFIDENT_OBS

    def test_locked_mode_feeds_mean_no_probe(self):
        # After training, "locked" feeds the typical uptake and never probes.
        ts = datetime(2026, 1, 5, 22, 0)
        recs = [_rec(ts.replace(day=d), 1.0, 1.0, 0.0) for d in range(1, 8)]  # all censored
        m = CapacityModel(aggressiveness=0.5, mode="locked").fit(recs)
        cap, explore, b = m.recommend_capacity(ts)
        self.assertFalse(explore)
        self.assertAlmostEqual(cap, b.mean_absorbed)
        self.assertLessEqual(cap, b.max_absorbed)  # never above what was taken

    def test_mode_override_at_inference(self):
        ts = datetime(2026, 1, 5, 22, 0)
        recs = [_rec(ts.replace(day=d), 1.0, 1.0, 0.0) for d in range(1, 8)]
        m = CapacityModel(aggressiveness=0.5, mode="explore").fit(recs)
        explore_cap, ex, _ = m.recommend_capacity(ts, mode="explore")
        locked_cap, lk, _ = m.recommend_capacity(ts, mode="locked")
        self.assertTrue(ex)
        self.assertFalse(lk)
        self.assertGreater(explore_cap, locked_cap)  # explore overshoots, locked doesn't

    def test_locked_unknown_context_feeds_nothing(self):
        m = CapacityModel(mode="locked").fit([])
        cap, explore, _ = m.recommend_capacity(datetime(2026, 1, 5, 22, 0))
        self.assertEqual(cap, 0.0)
        self.assertFalse(explore)

    def test_mode_persists_in_roundtrip(self):
        m = CapacityModel(mode="locked").fit([])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.json"
            m.save(p)
            self.assertEqual(CapacityModel.load(p).mode, "locked")

    def test_cold_start_probes_small(self):
        m = CapacityModel().fit([])
        cap, explore, stats = m.recommend_capacity(datetime(2026, 1, 5, 22, 0))
        self.assertTrue(explore)
        self.assertEqual(cap, COLD_START_PROBE_KWH)
        self.assertIsNone(stats)

    def test_save_load_roundtrip(self):
        ts = datetime(2026, 1, 5, 22, 0)
        m = CapacityModel(aggressiveness=0.3).fit([_rec(ts, 1.0, 0.6, 0.4)])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "model.json"
            m.save(p)
            m2 = CapacityModel.load(p)
        self.assertEqual(m2.aggressiveness, 0.3)
        self.assertEqual(m2.recommend_capacity(ts)[0], m.recommend_capacity(ts)[0])

    def test_load_missing_returns_none(self):
        self.assertIsNone(CapacityModel.load("does/not/exist.json"))


class TestSchedule(unittest.TestCase):
    def setUp(self):
        # Model where 22:00 absorbs a lot, 23:00 little.
        recs = []
        for d in range(1, 9):
            recs.append(_rec(datetime(2026, 1, d, 22, 0), 3.0, 2.0, 1.0))  # cap ~2.0
            recs.append(_rec(datetime(2026, 1, d, 23, 0), 1.0, 0.3, 0.7))  # cap ~0.3
        self.model = CapacityModel().fit(recs)

    def test_window_hours_overnight(self):
        hrs = _window_hours(datetime(2026, 1, 5, 22, 0), 19, 7)
        self.assertEqual(hrs[0].hour, 22)
        self.assertTrue(all(h.hour >= 19 or h.hour < 7 for h in hrs))

    def test_waterfill_respects_budget(self):
        plan = plan_feed(1.0, datetime(2026, 1, 5, 22, 0), 19, 7, self.model, 5.0)
        self.assertLessEqual(sum(p.feed_kwh for p in plan), 1.0 + 1e-6)

    def test_waterfill_prefers_high_capacity_hour(self):
        # Small budget should go to the 22:00 (high-capacity) hour first.
        plan = plan_feed(1.0, datetime(2026, 1, 5, 22, 0), 19, 7, self.model, 5.0)
        by_hour = {p.hour: p.feed_kwh for p in plan}
        self.assertGreater(by_hour[22], by_hour.get(23, 0.0))

    def test_feed_now_kw_matches_current_hour(self):
        now = datetime(2026, 1, 5, 22, 0)
        plan = plan_feed(1.0, now, 19, 7, self.model, 5.0)
        kw, _ = feed_now_kw(plan, now)
        self.assertGreater(kw, 0.0)

    def test_feed_now_kw_zero_off_plan(self):
        now = datetime(2026, 1, 5, 22, 0)
        plan = plan_feed(1.0, now, 19, 7, self.model, 5.0)
        kw, explore = feed_now_kw(plan, datetime(2026, 1, 5, 14, 0))  # daytime, not in plan
        self.assertEqual(kw, 0.0)


if __name__ == "__main__":
    unittest.main()
