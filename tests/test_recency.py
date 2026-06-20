"""v0.7 recency-weighted learning: the model adapts UP and DOWN, weights recent
days more, and re-explores contexts that have gone quiet."""

from __future__ import annotations

import unittest
from datetime import datetime

from brain.model.capacity import MIN_CONFIDENT_OBS, CapacityModel
from brain.model.context import bucket_key

H = 22
BUCKET = "summer|weekday|22"
# All Mondays, 22:00, summer -> one bucket. Old block then a recent block.
OLD = [datetime(2025, 7, d, H, 0) for d in (7, 14, 21, 28)]      # Jul Mondays
NEW = [datetime(2025, 8, d, H, 0) for d in (4, 11, 18, 25)]      # Aug Mondays


def _obs(ts, absorbed, surplus):
    from brain.model.capacity import HourObs
    return HourObs(str(ts.date()), ts.hour, ts, feed_kwh=absorbed + surplus,
                   absorbed_kwh=absorbed, surplus_kwh=surplus)


class TestRecency(unittest.TestCase):
    def test_ceiling_corrects_down(self):
        # Used to absorb 1.4 (old), now only 0.8 (recent), both uncensored.
        obs = ([_obs(t, 1.4, 0.3) for t in OLD] + [_obs(t, 0.8, 0.3) for t in NEW])
        recency = CapacityModel(half_life_days=20).fit_from_obs(obs)
        legacy = CapacityModel(half_life_days=0).fit_from_obs(obs)  # all-time max
        self.assertAlmostEqual(legacy.buckets[BUCKET].max_absorbed, 1.4, places=2)
        self.assertLess(recency.buckets[BUCKET].max_absorbed, 1.0,
                        "recent low uptake should pull the ceiling down from 1.4")

    def test_ceiling_climbs_when_recent_higher(self):
        # Old low, recent high -> ceiling reflects the new higher uptake.
        obs = ([_obs(t, 0.5, 0.3) for t in OLD] + [_obs(t, 1.2, 0.3) for t in NEW])
        m = CapacityModel(half_life_days=20).fit_from_obs(obs)
        self.assertGreater(m.buckets[BUCKET].max_absorbed, 1.0)

    def test_mean_weights_recent(self):
        # Recent absorption should dominate the weighted mean.
        obs = ([_obs(t, 0.2, 0.3) for t in OLD] + [_obs(t, 1.0, 0.3) for t in NEW])
        m = CapacityModel(half_life_days=20).fit_from_obs(obs)
        self.assertGreater(m.buckets[BUCKET].mean_absorbed, 0.6)  # > simple mean 0.6-ish

    def test_stale_bucket_re_explores(self):
        # A bucket fed only long ago (old) loses effective weight -> becomes
        # "unsure" again and is probed, even though it was uncensored.
        old_only = [_obs(t, 0.8, 0.3) for t in OLD]                   # uncensored, but old
        recent_other = [_obs(datetime(2025, 9, 1, 10, 0), 0.5, 0.3)]  # autumn -> advances "now"
        m = CapacityModel(half_life_days=20).fit_from_obs(old_only + recent_other)
        # query the SAME (summer|weekday|22) bucket the old obs are in
        cap, explore, stats = m.recommend_capacity(datetime(2025, 7, 7, H, 0))
        self.assertEqual(bucket_key(datetime(2025, 7, 7, H, 0)), BUCKET)
        self.assertLess(stats.weight, MIN_CONFIDENT_OBS)
        self.assertTrue(explore, "a context gone quiet should be re-explored")


if __name__ == "__main__":
    unittest.main()
