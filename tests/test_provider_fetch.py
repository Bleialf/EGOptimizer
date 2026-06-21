"""Automated-fetch path: Vienna DST conversion, the NetzNOE API mapping
(client injected/mocked — no network), import_records, and the generic
Provider.fetch_records default.

The HTTP handler in api/server.py stays a thin adapter (untested, like the
other endpoints); the logic it calls lives here and is fully covered.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from brain.ingest.importer import import_records
from brain.providers.base import Provider
from brain.providers.netznoe import (
    NetzNoeApi,
    NetzNoeProvider,
    _last_sunday,
    _vienna_local,
)
from brain.records import EnergyRecord


def _day(times, metered, absorbed=None, surplus=None, estimated=None):
    """Build a ConsumptionRecord/Day-shaped response dict."""
    n = len(times)
    return {
        "peakDemandTimes": times,
        "meteredValues": metered,
        "selfCoverageValues": absorbed if absorbed is not None else [None] * n,
        "gridUsageLeftoverValues": surplus if surplus is not None else [None] * n,
        "estimatedValues": estimated if estimated is not None else [None] * n,
    }


class FakeApi:
    """Stands in for NetzNoeApi: records calls, serves canned days."""

    def __init__(self, days=None, meters=None, raise_on_extend=False):
        self.days = days or {}
        self._meters = meters
        self.raise_on_extend = raise_on_extend
        self.logins = 0
        self.extends = 0

    def login(self, user, pwd):
        self.logins += 1

    def extend_session(self):
        self.extends += 1
        if self.raise_on_extend:
            raise RuntimeError("session lapsed")

    def metering_points(self):
        if self._meters is not None:
            return self._meters
        return [
            {"typeOfRelation": "Bezug", "meteringPointId": "AT_CONS"},
            {"typeOfRelation": "Einspeisung", "meteringPointId": "AT_FEED"},
        ]

    def consumption_day(self, meter_id, day):
        return self.days.get(day)


class TestViennaDst(unittest.TestCase):
    def test_last_sunday(self):
        # 2026: last Sunday of March = 29th, of October = 25th.
        self.assertEqual(_last_sunday(2026, 3), 29)
        self.assertEqual(_last_sunday(2026, 10), 25)
        self.assertEqual(_last_sunday(2025, 12), 28)  # exercises the December branch

    def test_summer_is_utc_plus_2(self):
        # 22:15 UTC on a June day -> 00:15 next local day (CEST).
        self.assertEqual(
            _vienna_local(datetime(2026, 6, 6, 22, 15)), datetime(2026, 6, 7, 0, 15)
        )

    def test_winter_is_utc_plus_1(self):
        self.assertEqual(
            _vienna_local(datetime(2026, 1, 15, 12, 0)), datetime(2026, 1, 15, 13, 0)
        )

    def test_transition_boundaries(self):
        # Just before the spring-forward instant (01:00 UTC, 29 Mar 2026) -> +1.
        self.assertEqual(
            _vienna_local(datetime(2026, 3, 29, 0, 59)), datetime(2026, 3, 29, 1, 59)
        )
        # At/after it -> +2.
        self.assertEqual(
            _vienna_local(datetime(2026, 3, 29, 1, 0)), datetime(2026, 3, 29, 3, 0)
        )


class TestFetchRecords(unittest.TestCase):
    def test_maps_fields_and_quality(self):
        times = ["2026-06-06T22:15:00", "2026-06-06T22:30:00", "2026-06-06T22:45:00"]
        day = _day(
            times,
            metered=[0.10, None, 0.20],          # middle interval has no value -> skipped
            absorbed=[0.10, 0.05, None],          # last not settled -> eg_quality None
            surplus=[0.0, 0.05, None],
            estimated=[None, 0.0, None],          # presence flips quality L1->L2 (n/a here)
        )
        api = FakeApi(days={date(2026, 6, 6): day})
        recs = list(
            NetzNoeProvider().fetch_records(
                credentials={"user": "u", "pwd": "p"},
                since=date(2026, 6, 6), until=date(2026, 6, 6), api=api,
            )
        )
        self.assertEqual(api.logins, 1)
        self.assertEqual(len(recs), 2)  # the None-metered interval dropped
        r0 = recs[0]
        self.assertEqual(r0.meter_id, "AT_FEED")
        self.assertEqual(r0.provider, "netznoe")
        self.assertEqual(r0.timestamp, datetime(2026, 6, 7, 0, 15))  # UTC+2 (CEST)
        self.assertAlmostEqual(r0.feed_in_kwh, 0.10)
        self.assertAlmostEqual(r0.eg_absorbed_kwh, 0.10)
        self.assertTrue(r0.fully_absorbed)        # surplus 0 -> censored
        self.assertEqual(r0.quality, "L1")        # estimated None -> measured
        self.assertEqual(r0.eg_quality, "L2")     # absorbed present -> settled
        # last interval: absorbed None -> unsettled, eg_quality None
        self.assertIsNone(recs[1].eg_absorbed_kwh)
        self.assertIsNone(recs[1].eg_quality)

    def test_missing_credentials_raises(self):
        with self.assertRaises(ValueError):
            list(NetzNoeProvider().fetch_records(credentials={"user": "u"}))

    def test_no_feed_meter_raises(self):
        api = FakeApi(meters=[{"typeOfRelation": "Bezug", "meteringPointId": "AT_C"}])
        with self.assertRaises(ValueError):
            list(NetzNoeProvider().fetch_records(
                credentials={"user": "u", "pwd": "p"}, api=api))

    def test_empty_day_skipped_and_default_window(self):
        # No canned days -> every day returns None -> no records, but it runs the
        # full default window (until=today, since=today-7) without error.
        api = FakeApi(days={})
        recs = list(NetzNoeProvider().fetch_records(
            credentials={"user": "u", "pwd": "p"}, api=api))
        self.assertEqual(recs, [])

    def test_long_backfill_keeps_session_warm(self):
        # 40-day span crosses the i%30 keepalive; extend_session is called.
        api = FakeApi(days={})
        list(NetzNoeProvider().fetch_records(
            credentials={"user": "u", "pwd": "p"},
            since=date(2026, 4, 1), until=date(2026, 5, 11), api=api))
        self.assertGreaterEqual(api.extends, 1)

    def test_relogin_when_keepalive_fails(self):
        api = FakeApi(days={}, raise_on_extend=True)
        list(NetzNoeProvider().fetch_records(
            credentials={"user": "u", "pwd": "p"},
            since=date(2026, 4, 1), until=date(2026, 5, 11), api=api))
        self.assertGreaterEqual(api.logins, 2)  # initial + re-login after lapse


class TestImportRecords(unittest.TestCase):
    def test_upserts_and_drops_outliers(self):
        recs = [
            EnergyRecord(datetime(2026, 6, 1, 22, 0), "AT_FEED", "netznoe",
                         feed_in_kwh=0.5, eg_absorbed_kwh=0.5, eg_surplus_kwh=0.0),
            EnergyRecord(datetime(2026, 6, 1, 22, 15), "AT_FEED", "netznoe",
                         feed_in_kwh=99.0, eg_absorbed_kwh=None, eg_surplus_kwh=None),
        ]
        with tempfile.TemporaryDirectory() as d:
            res = import_records(recs, Path(d) / "t.sqlite", max_interval_kwh=5.0)
        self.assertEqual(res["imported"], 1)   # the 99 kWh spike dropped
        self.assertEqual(res["dropped"], 1)
        self.assertEqual(res["store"]["n"], 1)


class _Resp:
    def __init__(self, raw):
        self._raw = raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._raw


class _Opener:
    """Stand-in for the urllib opener: returns canned bytes, records requests."""

    def __init__(self, raw):
        self.raw = raw
        self.calls = []

    def open(self, req, timeout=None):
        self.calls.append(req)
        return _Resp(self.raw)


class TestNetzNoeApiClient(unittest.TestCase):
    def _api(self, raw):
        api = NetzNoeApi()
        api._opener = _Opener(raw)
        return api

    def test_credential_fields(self):
        self.assertEqual(NetzNoeProvider.credential_fields(), ["user", "pwd"])

    def test_get_returns_json(self):
        api = self._api(b'{"ok": true}')
        self.assertEqual(api._request("GET", "/x"), {"ok": True})

    def test_post_sends_json_body(self):
        api = self._api(b"")
        api.login("u", "p")          # POST + empty body response -> None
        req = api._opener.calls[-1]
        self.assertEqual(req.data, b'{"user": "u", "pwd": "p"}')
        self.assertEqual(req.get_header("Content-type"), "application/json")

    def test_empty_body_is_none(self):
        self.assertIsNone(self._api(b"")._request("GET", "/x"))

    def test_metering_points_none_to_empty_list(self):
        self.assertEqual(self._api(b"null").metering_points(), [])

    def test_consumption_day_variants(self):
        self.assertEqual(
            self._api(b'[{"d": 1}]').consumption_day("m", date(2026, 6, 7)), {"d": 1}
        )
        self.assertIsNone(self._api(b"[]").consumption_day("m", date(2026, 6, 7)))

    def test_extend_session_runs(self):
        self._api(b"").extend_session()  # exercises the keepalive call path


class TestBaseDefaults(unittest.TestCase):
    def test_fetch_records_default_raises_and_no_credentials(self):
        class _Bare(Provider):
            def parse(self, source):
                yield from ()

        self.assertEqual(_Bare.credential_fields(), [])
        with self.assertRaises(NotImplementedError):
            list(_Bare().fetch_records(credentials={}))


if __name__ == "__main__":
    unittest.main()
