"""Autarky reserve -- simulation-based, target protected at the trough."""

from __future__ import annotations

import unittest
from datetime import datetime

from brain.forecast.reserve import ReserveInputs, compute_reserve
from brain.forecast.simulate import simulate_soc

DAY = "2026-01-16"
SUNNY = [{"period_start": f"{DAY}T{h:02d}:00:00", "pv_estimate10": v}
         for h, v in {7: 0.4, 8: 1.2, 9: 2.5, 10: 3.5, 11: 4.0, 12: 4.0}.items()]
CLOUDY = [{"period_start": f"{DAY}T{h:02d}:00:00", "pv_estimate10": v}
          for h, v in {8: 0.15, 9: 0.3, 10: 0.45, 11: 0.7, 12: 1.0}.items()]
NOW = datetime(2026, 1, 15, 22, 0)


def _inputs(**kw):
    base = dict(
        now=NOW, soc_pct=90.0, capacity_kwh=14.0,
        target_morning_soc_pct=50.0, hard_min_soc_pct=10.0,
        load_kw=0.4, pv_slots=tuple(SUNNY), horizon_h=24.0,
    )
    base.update(kw)
    if "pv_slots" in base:
        base["pv_slots"] = tuple(base["pv_slots"])
    return ReserveInputs(**base)


class TestReserve(unittest.TestCase):
    def test_budget_non_negative(self):
        self.assertGreaterEqual(compute_reserve(_inputs()).eg_budget_kwh, 0.0)

    def test_target_clamped_to_hard_min(self):
        r = compute_reserve(_inputs(target_morning_soc_pct=5, hard_min_soc_pct=10))
        self.assertEqual(r.effective_target_pct, 10.0)

    def test_higher_target_reduces_budget(self):
        low = compute_reserve(_inputs(target_morning_soc_pct=30))
        high = compute_reserve(_inputs(target_morning_soc_pct=70))
        self.assertGreaterEqual(low.eg_budget_kwh, high.eg_budget_kwh)

    def test_cloudy_reduces_budget_vs_sunny(self):
        # Cloudy morning -> deeper/later trough -> less free to give.
        sunny = compute_reserve(_inputs(pv_slots=SUNNY))
        cloudy = compute_reserve(_inputs(pv_slots=CLOUDY))
        self.assertLess(cloudy.eg_budget_kwh, sunny.eg_budget_kwh)

    def test_budget_equals_trough_headroom(self):
        # The core identity: budget is exactly the simulated trough's headroom
        # above the target (every kWh fed before the trough lowers it 1:1).
        cap = 14.0
        r = compute_reserve(_inputs())
        trough_kwh = cap * r.trough_soc_pct / 100.0
        target_kwh = cap * r.effective_target_pct / 100.0
        self.assertAlmostEqual(r.eg_budget_kwh, max(0.0, trough_kwh - target_kwh), places=2)

    def test_low_soc_no_budget(self):
        r = compute_reserve(_inputs(soc_pct=45))  # below 50% target already
        self.assertEqual(r.eg_budget_kwh, 0.0)

    def test_trough_time_and_takeover_exposed(self):
        r = compute_reserve(_inputs())
        self.assertTrue(r.trough_time)
        self.assertTrue(r.pv_takeover_time)

    def test_bigger_battery_more_budget(self):
        small = compute_reserve(_inputs(capacity_kwh=6.0))
        big = compute_reserve(_inputs(capacity_kwh=20.0))
        self.assertGreater(big.eg_budget_kwh, small.eg_budget_kwh)

    def test_rationale_present(self):
        self.assertTrue(compute_reserve(_inputs()).rationale)


if __name__ == "__main__":
    unittest.main()
