"""Edge/branch coverage: helpers, error paths, holding branch, provider registry,
config file loading, and DB filters — the corners the feature tests don't reach."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from brain.api.service import _f, _first, _horizon_to_forecast_end, recommend
from brain.config import load_config
from brain.forecast.simulate import simulate_soc


class TestServiceHelpers(unittest.TestCase):
    def test_f_uses_default_when_missing(self):
        self.assertEqual(_f({}, "x", 7.0), 7.0)
        self.assertEqual(_f({"x": "3.5"}, "x", 0.0), 3.5)

    def test_first_all_none_returns_zero(self):
        self.assertEqual(_first(None, None), 0.0)
        self.assertEqual(_first(None, 2.0, 9.0), 2.0)

    def test_horizon_ignores_bad_slots_and_falls_back(self):
        now = datetime(2026, 1, 1, 20, 0)
        # all slots unparseable -> falls back to the minimum horizon
        h = _horizon_to_forecast_end(now, [{"period_start": "nope"}, {"bad": 1}], min_h=24.0)
        self.assertEqual(h, 24.0)
        # a good slot 10h out -> ~11h, but min_h floors it to 24
        good = [{"period_start": "2026-01-02T08:00:00"}]
        self.assertEqual(_horizon_to_forecast_end(now, good, min_h=24.0), 24.0)


class TestSimulateBadSlot(unittest.TestCase):
    def test_unparseable_pv_slot_skipped(self):
        traj = simulate_soc(datetime(2026, 1, 1, 20, 0), 10.0, 20.0, 0.4,
                            [{"period_start": "garbage", "pv_estimate10": 1.0}], horizon_h=2.0)
        self.assertTrue(traj.points)  # ran fine despite the bad slot


class _CtlModel:
    """Controllable model: zero capacity at one hour, fixed elsewhere."""
    buckets = {"_": 1}

    def __init__(self, zero_hour: int):
        self.zero_hour = zero_hour

    def recommend_capacity(self, ts, mode=None, aggressiveness=None):
        cap = 0.0 if ts.hour == self.zero_hour else 0.6
        return cap, cap > 0, None


class TestHoldingBranch(unittest.TestCase):
    def test_holds_now_but_plans_later(self):
        # Current hour (22:00) has zero capacity -> feed 0 now, but other hours
        # get fed -> status "holding" (exercises the holding note + reasoning,
        # and the zero-capacity skip in the planner).
        cfg = load_config()
        d1 = (datetime(2026, 6, 21)).date()
        pv = [{"period_start": f"{d1}T{h:02d}:00:00", "pv_estimate10": 3.0} for h in range(6, 20)]
        state = dict(timestamp="2026-06-20T22:00:00", soc_pct=80, capacity_kwh=20,
                     target_morning_soc_pct=25, hard_min_soc_pct=20, load_now_kw=0.4,
                     night_load_kw=0.5, mode="explore", pv_forecast=pv)
        r = recommend(state, cfg, model=_CtlModel(zero_hour=22))
        self.assertEqual(r["status"], "holding")
        self.assertEqual(r["feed_kw"], 0.0)
        self.assertGreater(r["planned_tonight_kwh"], 0)
        self.assertIn("Waiting", r["rationale"])


class TestConfigFileLoading(unittest.TestCase):
    def test_yaml_override_deep_merges(self):
        try:
            import yaml  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("pyyaml not installed")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.yaml"
            p.write_text("autarky:\n  target_morning_soc_pct: 33\n", encoding="utf-8")
            cfg = load_config(p)
            self.assertEqual(cfg["autarky"]["target_morning_soc_pct"], 33)   # overridden
            self.assertIn("night_load_kw", cfg["autarky"])                   # default kept

    def test_falls_back_to_defaults_without_pyyaml(self):
        # When PyYAML isn't installed (the stdlib-only deployment), load_config
        # returns the built-in defaults even if a config file exists.
        import builtins
        real_import = builtins.__import__

        def no_yaml(name, *a, **k):
            if name == "yaml":
                raise ModuleNotFoundError("no yaml")
            return real_import(name, *a, **k)

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.yaml"
            p.write_text("autarky:\n  target_morning_soc_pct: 99\n", encoding="utf-8")
            with mock.patch("builtins.__import__", side_effect=no_yaml):
                cfg = load_config(p)
        self.assertEqual(cfg["autarky"]["target_morning_soc_pct"], 50.0)  # default, file ignored


class TestProviderRegistry(unittest.TestCase):
    def test_unknown_provider_exits(self):
        from brain.providers import available, get_provider
        self.assertIn("netznoe", available())
        with self.assertRaises(SystemExit):
            get_provider("does-not-exist")

    def test_base_methods_raise(self):
        from brain.providers.base import Provider
        from brain.providers.netznoe import NetzNoeProvider
        with self.assertRaises(NotImplementedError):
            NetzNoeProvider().fetch(Path("."))   # fetch not implemented

        class _Bare(Provider):
            def parse(self, source):
                return super().parse(source)     # hits the abstract body
        with self.assertRaises(NotImplementedError):
            list(_Bare().parse(Path(".")))


class TestStorageFilters(unittest.TestCase):
    def test_fetch_all_with_filters(self):
        from brain.records import EnergyRecord
        from brain.storage import Store
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / "t.sqlite")
            try:
                store.upsert_many([EnergyRecord(
                    timestamp=datetime(2026, 6, 1, 22, 0), meter_id="AT00", provider="netznoe",
                    feed_in_kwh=1.0, eg_absorbed_kwh=0.5, eg_surplus_kwh=0.5)])
                self.assertEqual(len(store.fetch_all(provider="netznoe", meter_id="AT00")), 1)
                self.assertEqual(len(store.fetch_all(provider="other")), 0)
            finally:
                store.close()


class TestImporterCleanupError(unittest.TestCase):
    def test_tempdir_cleanup_error_is_swallowed(self):
        from brain.ingest.importer import import_bytes
        csv = ("﻿Messzeitpunkt;Einspeisung (kWh);Qualität;Gemeinschaftsüberschuss (kWh);"
               "Qualität EG;Eigendeckung Teilnehmer (kWh);x;\n"
               "20.05.2026 22:00;0,500000;L1;0,000000;L2;0,500000;0,5;\n").encode("utf-8")
        with tempfile.TemporaryDirectory() as d:
            with mock.patch("pathlib.Path.rmdir", side_effect=OSError("boom")):
                res = import_bytes(csv, "AT00-x.csv", "netznoe", Path(d) / "t.sqlite", 5.0)
            self.assertGreaterEqual(res["imported"], 1)   # returned despite cleanup error


if __name__ == "__main__":
    unittest.main()
