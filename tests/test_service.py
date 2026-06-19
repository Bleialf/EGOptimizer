"""recommend() service: window, simulation-based budget, live-load adaptation."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from brain.api.service import _hours_left_in_window, recommend
from brain.config import load_config
from brain.storage import Store

DAY = "2026-01-16"
SUNNY = [{"period_start": f"{DAY}T{h:02d}:00:00", "pv_estimate10": v}
         for h, v in {8: 1.2, 9: 2.5, 10: 3.5, 11: 4.0, 12: 4.0}.items()]
CLOUDY = [{"period_start": f"{DAY}T{h:02d}:00:00", "pv_estimate10": v}
          for h, v in {9: 0.3, 10: 0.45, 11: 0.7, 12: 1.0}.items()]


class TestFeedWindow(unittest.TestCase):
    def test_inside_overnight_window(self):
        self.assertAlmostEqual(_hours_left_in_window(datetime(2026, 5, 20, 22, 0), 19, 7), 9.0)

    def test_after_midnight_still_in_window(self):
        self.assertAlmostEqual(_hours_left_in_window(datetime(2026, 5, 21, 2, 0), 19, 7), 5.0)

    def test_daytime_outside_window(self):
        self.assertEqual(_hours_left_in_window(datetime(2026, 5, 20, 14, 0), 19, 7), 0.0)


class TestRecommend(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self._tmp.name) / "t.sqlite")

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def _state(self, **kw):
        s = dict(
            timestamp="2026-01-15T22:00:00",
            soc_pct=85, capacity_kwh=12, target_morning_soc_pct=50,
            hard_min_soc_pct=10, load_now_kw=0.4, pv_forecast=SUNNY,
        )
        s.update(kw)
        return s

    def test_positive_feed_in_window(self):
        r = recommend(self._state(), self.cfg, self.store)
        self.assertGreater(r["feed_kw"], 0)
        self.assertGreater(r["eg_budget_kwh"], 0)

    def test_daytime_holds(self):
        r = recommend(self._state(timestamp="2026-01-16T14:00:00"), self.cfg, self.store)
        self.assertEqual(r["feed_kw"], 0.0)

    def test_higher_target_reduces_feed(self):
        low = recommend(self._state(target_morning_soc_pct=30), self.cfg, self.store)
        high = recommend(self._state(target_morning_soc_pct=80), self.cfg, self.store)
        self.assertGreaterEqual(low["eg_budget_kwh"], high["eg_budget_kwh"])

    def test_live_load_adapts_plan(self):
        light = recommend(self._state(load_now_kw=0.2), self.cfg, self.store)
        heavy = recommend(self._state(load_now_kw=1.5), self.cfg, self.store)
        self.assertGreater(light["eg_budget_kwh"], heavy["eg_budget_kwh"])

    def test_cloudy_morning_reduces_budget(self):
        sunny = recommend(self._state(pv_forecast=SUNNY), self.cfg, self.store)
        cloudy = recommend(self._state(pv_forecast=CLOUDY), self.cfg, self.store)
        self.assertLess(cloudy["eg_budget_kwh"], sunny["eg_budget_kwh"])

    def test_cloudy_pushes_trough_later(self):
        sunny = recommend(self._state(pv_forecast=SUNNY), self.cfg, self.store)
        cloudy = recommend(self._state(pv_forecast=CLOUDY), self.cfg, self.store)
        self.assertGreater(
            datetime.fromisoformat(cloudy["trough_time"]).hour,
            datetime.fromisoformat(sunny["trough_time"]).hour,
        )

    def test_feed_clamped_to_max_discharge(self):
        r = recommend(
            self._state(capacity_kwh=80, soc_pct=100, load_now_kw=0.1,
                        target_morning_soc_pct=20),
            self.cfg, self.store,
        )
        self.assertLessEqual(r["feed_kw"], self.cfg["battery"]["max_discharge_kw"])

    def test_target_clamped_to_hard_min(self):
        r = recommend(self._state(target_morning_soc_pct=2, hard_min_soc_pct=10),
                      self.cfg, self.store)
        self.assertEqual(r["target_morning_soc_pct"], 10.0)

    def test_decision_is_logged_without_big_arrays(self):
        recommend(self._state(), self.cfg, self.store)
        row = self.store.conn.execute(
            "SELECT response FROM decisions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertNotIn("soc_forecast", row["response"])  # not persisted
        self.assertNotIn("feed_plan", row["response"])      # not persisted

    def test_soc_forecast_present_in_response(self):
        r = recommend(self._state(), self.cfg, self.store)
        self.assertTrue(r["soc_forecast"])
        self.assertIn("soc_pct", r["soc_forecast"][0])

    def test_status_and_confidence_no_model(self):
        # No model loaded -> flat spread, confidence reports no_model.
        r = recommend(self._state(), self.cfg, self.store, model=None)
        self.assertEqual(r["confidence"], "no_model")
        self.assertIn(r["status"], ("feeding", "holding", "no_budget"))

    def test_no_budget_status(self):
        r = recommend(self._state(soc_pct=45), self.cfg, self.store)  # below target
        self.assertEqual(r["status"], "no_budget")
        self.assertEqual(r["feed_kw"], 0.0)

    def test_works_without_store(self):
        self.assertIn("feed_kw", recommend(self._state(), self.cfg, store=None))

    def test_response_schema(self):
        r = recommend(self._state(), self.cfg, self.store)
        for k in ("version", "decided_at", "feed_kw", "status", "confidence",
                  "explore", "mode", "eg_budget_kwh", "planned_tonight_kwh",
                  "context_observations", "next_feed_time",
                  "target_morning_soc_pct", "trough_soc_pct", "trough_time",
                  "pv_takeover_time", "load_kw", "rationale", "feed_plan",
                  "soc_forecast"):
            self.assertIn(k, r)


if __name__ == "__main__":
    unittest.main()
