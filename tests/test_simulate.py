"""Battery simulation -- realistic overnight scenarios.

The key behaviour: the trough (closest approach to empty) is DISCOVERED from
the PV curve, not assumed at a fixed time. A cloudy morning -> PV ramps later
and weaker -> the house keeps draining the battery longer -> the trough lands
later in the day and deeper.
"""

from __future__ import annotations

import unittest
from datetime import datetime

from brain.forecast.simulate import simulate_soc


def _slots(date_str: str, pv_by_hour: dict[int, float]) -> list[dict]:
    """Build Solcast-style detailedHourly slots (pv_estimate10 in kWh/h)."""
    return [
        {"period_start": f"{date_str}T{h:02d}:00:00", "pv_estimate10": kwh}
        for h, kwh in sorted(pv_by_hour.items())
    ]


# Winter day after a 22:00 start on 2026-01-15.
DAY = "2026-01-16"

# Sunny morning: PV clears a 0.5 kW house load by ~08:00.
SUNNY = _slots(DAY, {6: 0.1, 7: 0.4, 8: 1.2, 9: 2.5, 10: 3.5, 11: 4.0,
                     12: 4.2, 13: 3.8, 14: 2.8, 15: 1.5, 16: 0.5})
# Cloudy morning: PV stays under load until ~11:00, much weaker.
CLOUDY = _slots(DAY, {6: 0.02, 7: 0.05, 8: 0.15, 9: 0.3, 10: 0.45, 11: 0.7,
                      12: 1.0, 13: 0.9, 14: 0.6, 15: 0.3, 16: 0.1})

NOW = datetime(2026, 1, 15, 22, 0)
LOAD = 0.5


class TestSimulation(unittest.TestCase):
    def _sim(self, slots, soc_pct=80.0, cap=10.0, feed=0.0):
        return simulate_soc(NOW, cap * soc_pct / 100.0, cap, LOAD, slots, feed_kw=feed)

    def test_soc_never_negative_or_over_capacity(self):
        traj = self._sim(SUNNY, soc_pct=95)
        for p in traj.points:
            self.assertGreaterEqual(p.soc_kwh, 0.0)
            self.assertLessEqual(p.soc_pct, 100.0 + 1e-6)

    def test_pv_takeover_sunny_earlier_than_cloudy(self):
        sunny = self._sim(SUNNY)
        cloudy = self._sim(CLOUDY)
        self.assertIsNotNone(sunny.pv_takeover_time)
        self.assertIsNotNone(cloudy.pv_takeover_time)
        # Sunny clears 0.5 kW load at 08:00; cloudy not until ~11:00.
        self.assertEqual(sunny.pv_takeover_time.hour, 8)
        self.assertGreaterEqual(cloudy.pv_takeover_time.hour, 11)

    def test_cloudy_morning_pushes_trough_later(self):
        sunny = self._sim(SUNNY)
        cloudy = self._sim(CLOUDY)
        # The whole point: cloudy trough is later in the day than sunny.
        self.assertGreater(cloudy.trough_time.hour, sunny.trough_time.hour)

    def test_cloudy_morning_trough_is_deeper(self):
        sunny = self._sim(SUNNY)
        cloudy = self._sim(CLOUDY)
        self.assertLess(cloudy.trough_soc_pct, sunny.trough_soc_pct)

    def test_no_pv_means_monotonic_decline_no_takeover(self):
        # No sun at all -> battery only drains -> SoC never rises, PV never
        # takes over. (It may bottom out at 0% before the horizon ends.)
        traj = self._sim([], soc_pct=90)
        socs = [p.soc_kwh for p in traj.points]
        self.assertTrue(all(b <= a + 1e-9 for a, b in zip(socs, socs[1:])))
        self.assertIsNone(traj.pv_takeover_time)
        self.assertAlmostEqual(traj.trough_soc_kwh, min(socs))

    def test_feeding_before_trough_lowers_it(self):
        base = self._sim(CLOUDY)
        fed = self._sim(CLOUDY, feed=0.3)
        self.assertLess(fed.trough_soc_pct, base.trough_soc_pct)

    def test_trough_not_at_start(self):
        # With a healthy SoC the trough should be in the morning, not t0.
        traj = self._sim(SUNNY, soc_pct=80)
        self.assertNotEqual(traj.trough_time, NOW)


if __name__ == "__main__":
    unittest.main()
