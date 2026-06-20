"""End-to-end recommend() WITH a trained model — the main decision path.

This was the coverage hole: the existing service tests only hit the no-model
fallback, so the model branch (autarky planner + the trough/forecast wiring)
was never exercised. The trough-display bug (reported the no-feed baseline
instead of the planned trajectory) lived right here and slipped through.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from brain.api.service import recommend
from brain.config import load_config
from brain.model.capacity import CapacityModel, HourObs

NOW = "2026-06-20T22:00:00"            # summer Saturday evening
_T = datetime(2026, 6, 21)            # tomorrow
SUNNY = [{"period_start": f"{_T.date()}T{h:02d}:00:00", "pv_estimate10": max(0.0, 3.0 - abs(13 - h) * 0.35)}
         for h in range(5, 21)]
CLOUDY = [{"period_start": f"{_T.date()}T{h:02d}:00:00", "pv_estimate10": 0.2} for h in range(8, 18)]


def _trained_model() -> CapacityModel:
    # Every hour absorbed 0.8 and was fully absorbed (censored) -> the model
    # wants to probe ~0.8+ each hour: a real feeding plan.
    obs = []
    for d in range(1, 15):
        ts = datetime(2026, 6, 1) + timedelta(days=d)
        for h in range(24):
            obs.append(HourObs(str(ts.date()), h, ts.replace(hour=h),
                               feed_kwh=0.8, absorbed_kwh=0.8, surplus_kwh=0.0))
    return CapacityModel(half_life_days=45).fit_from_obs(obs)


class TestRecommendWithModel(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()
        self.model = _trained_model()

    def _state(self, **kw):
        s = dict(timestamp=NOW, soc_pct=80, capacity_kwh=20, target_morning_soc_pct=25,
                 hard_min_soc_pct=20, load_now_kw=0.4, night_load_kw=0.5,
                 mode="explore", exploration_aggressiveness=0.2, pv_forecast=SUNNY)
        s.update(kw)
        return s

    def test_feeds_with_a_plan(self):
        r = recommend(self._state(), self.cfg, model=self.model)
        self.assertEqual(r["status"], "feeding")
        self.assertGreater(r["feed_kw"], 0)
        self.assertGreater(r["planned_tonight_kwh"], 0)

    def test_reported_trough_reflects_plan_not_baseline(self):
        # REGRESSION: the trough must be the PLANNED low (driven toward the
        # target by the feeds), not the high no-feed baseline.
        r = recommend(self._state(soc_pct=80), self.cfg, model=self.model)
        target = r["target_morning_soc_pct"]
        self.assertAlmostEqual(r["trough_soc_pct"], target, delta=4,
                               msg="trough should reflect the plan feeding down to target")
        self.assertLess(r["trough_soc_pct"], 60, "must be well below the no-feed baseline")
        # and it must match the displayed SoC-forecast curve
        fmin = min(p["soc_pct"] for p in r["soc_forecast"])
        self.assertAlmostEqual(fmin, r["trough_soc_pct"], delta=1.0)

    def test_autarky_floor_holds_end_to_end(self):
        r = recommend(self._state(soc_pct=80), self.cfg, model=self.model)
        fmin = min(p["soc_pct"] for p in r["soc_forecast"])
        self.assertGreaterEqual(fmin, r["target_morning_soc_pct"] - 1.0)

    def test_feeds_during_the_day_when_sunny(self):
        r = recommend(self._state(soc_pct=80), self.cfg, model=self.model)
        day = [p for p in r["feed_plan"] if 8 <= p["hour"] <= 18 and p["feed_kwh"] > 0]
        self.assertTrue(day, "sunny tomorrow should let it feed daytime surplus")

    def test_cloudy_tomorrow_reduces_plan(self):
        sunny = recommend(self._state(soc_pct=55, pv_forecast=SUNNY), self.cfg, model=self.model)
        cloudy = recommend(self._state(soc_pct=55, pv_forecast=CLOUDY), self.cfg, model=self.model)
        self.assertLess(cloudy["planned_tonight_kwh"], sunny["planned_tonight_kwh"])

    def test_reasoning_fits_ha_sensor_limit(self):
        # HA caps sensor STATE at 255 chars; the sentence must not be truncated.
        for st in (self._state(soc_pct=80), self._state(soc_pct=40),
                   self._state(soc_pct=80, mode="locked")):
            r = recommend(st, self.cfg, model=self.model)
            self.assertLessEqual(len(r["rationale"]), 255, r["rationale"])


if __name__ == "__main__":
    unittest.main()
