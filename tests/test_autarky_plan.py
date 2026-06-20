"""v0.6 per-hour autarky-bounded planner.

The contract: feed any hour (daytime included) up to the EG's learned capacity,
but the simulated SoC must NEVER drop below the morning target across the
horizon. Tomorrow's forecast is part of that horizon, so a cloudy tomorrow
holds feeding back; a sunny one frees daytime surplus.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from brain.forecast.simulate import simulate_soc
from brain.model.schedule import plan_feed_autarky


class _FlatModel:
    """Every hour has the same learned, confident absorption ceiling."""

    buckets = {"_": 1}

    def __init__(self, cap: float = 0.8):
        self._cap = cap

    def recommend_capacity(self, ts, mode=None, aggressiveness=None):
        return self._cap, False, None


def _sun(day_offset: int, base_day: datetime, kw: float = 3.0):
    """Hourly PV slots 08–18 for a given day."""
    d = (base_day + timedelta(days=day_offset)).date()
    return [{"period_start": f"{d}T{h:02d}:00:00", "pv_estimate10": kw} for h in range(8, 19)]


CAP_KWH = 20.0
NOW = datetime(2026, 1, 5, 20, 0)          # winter Monday evening
TARGET_KWH = 0.25 * CAP_KWH                # 25%


def _plan(soc_frac, pv, load=0.4, cap=0.8, horizon=30.0):
    model = _FlatModel(cap)
    plan = plan_feed_autarky(
        NOW, soc_frac * CAP_KWH, CAP_KWH, TARGET_KWH, load, pv, model,
        max_per_hour_kwh=5.0, horizon_h=horizon,
    )
    feed_by_hour = {p.ts: p.feed_kwh for p in plan}
    total = sum(p.feed_kwh for p in plan)
    return plan, feed_by_hour, total


class TestAutarkyPlan(unittest.TestCase):
    def test_floor_never_violated(self):
        # Even with a generous battery and sunny tomorrow, the resulting plan
        # must keep the simulated trough at/above target.
        pv = _sun(1, NOW)
        _, feed_by_hour, total = _plan(0.8, pv)
        self.assertGreater(total, 0.0)
        traj = simulate_soc(NOW, 0.8 * CAP_KWH, CAP_KWH, 0.4, pv,
                            feed_by_hour=feed_by_hour, horizon_h=30.0)
        self.assertGreaterEqual(traj.trough_soc_kwh, TARGET_KWH - 1e-3)

    def test_feeds_during_the_day(self):
        # Sunny tomorrow -> battery refills -> daytime hours (08–18) get fed
        # from the surplus, not just the night.
        pv = _sun(1, NOW)
        plan, _, _ = _plan(0.8, pv)
        day = [p for p in plan if 8 <= p.hour <= 18 and p.feed_kwh > 0]
        self.assertTrue(day, "expected at least one daytime hour to be fed when sunny")

    def test_cloudy_tomorrow_holds_back(self):
        # A rainy tomorrow (battery can't refill) must plan strictly less than a
        # sunny one -- the autarky floor binds.
        _, _, sunny = _plan(0.6, _sun(1, NOW, kw=3.0))
        _, _, rainy = _plan(0.6, _sun(1, NOW, kw=0.2))
        self.assertLess(rainy, sunny)

    def test_party_lower_soc_reduces_feed(self):
        # Lower live SoC (e.g. a party drained the battery) -> less feed. This is
        # the feedback that protects autarky on the next recompute.
        pv = _sun(1, NOW)
        _, _, full = _plan(0.8, pv)
        _, _, low = _plan(0.35, pv)
        self.assertLess(low, full)

    def test_deficit_feeds_nothing(self):
        # SoC already below target with weak PV -> cannot hold the floor -> 0.
        _, _, total = _plan(0.20, _sun(1, NOW, kw=0.2))
        self.assertEqual(total, 0.0)


if __name__ == "__main__":
    unittest.main()
