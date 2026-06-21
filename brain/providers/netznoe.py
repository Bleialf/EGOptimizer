"""NetzNOE (Netz Niederoesterreich) provider.

Parses the *Jahreseinspeisung* CSV export from the NetzNOE smart-meter portal.

Format quirks handled here:
  * UTF-8 BOM on the first byte
  * ``;`` field separator
  * German decimal comma ("0,374347")
  * timestamps "DD.MM.YYYY HH:MM" (interval END), local time Europe/Vienna
  * trailing empty field (line ends with ``;``)
  * recent rows where the EG allocation columns are blank (not yet settled)

Header (columns we use):
  0 Messzeitpunkt
  1 Einspeisung (kWh)                         -> feed_in_kwh
  2 Qualitaet                                 -> quality
  3 Gemeinschaftsueberschuss (kWh)            -> eg_surplus_kwh
  4 Qualitaet EG                              -> eg_quality
  5 Eigendeckung Teilnehmer (kWh)             -> eg_absorbed_kwh
  6 Eigendeckung Teilnehmer (kWh) <EG name>   -> (duplicate, ignored)
"""

from __future__ import annotations

import csv
import http.cookiejar
import json
import re
import urllib.request
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path

from brain.providers.base import Provider
from brain.records import EnergyRecord

# Metering-point id is embedded in the export filename, e.g.
# "AT0020000000000000000000100487200-Jahreseinspeisung-2026.csv"
_METER_RE = re.compile(r"(AT\d{30,})", re.IGNORECASE)


def _num(raw: str) -> float | None:
    """Parse a German-formatted number; blank -> None."""
    raw = raw.strip()
    if not raw:
        return None
    return float(raw.replace(".", "").replace(",", "."))


def _meter_id_from_name(path: Path) -> str:
    m = _METER_RE.search(path.name)
    return m.group(1) if m else "unknown"


# --- Europe/Vienna local time without a tz database -----------------------
# The portal's API returns interval-end timestamps in UTC; our records store
# naive *local* time (same as the CSV export) so hourly buckets line up. We
# can't rely on zoneinfo/tzdata being present (the brain is dependency-free
# and runs on slim images), so apply the EU DST rule directly: CEST (UTC+2)
# from the last Sunday of March 01:00 UTC to the last Sunday of October 01:00
# UTC, otherwise CET (UTC+1).


def _last_sunday(year: int, month: int) -> int:
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last = nxt - timedelta(days=1)
    return (last - timedelta(days=(last.weekday() + 1) % 7)).day


def _vienna_local(dt_utc: datetime) -> datetime:
    """Naive UTC -> naive Europe/Vienna wall-clock time."""
    y = dt_utc.year
    start = datetime(y, 3, _last_sunday(y, 3), 1, 0, 0)
    end = datetime(y, 10, _last_sunday(y, 10), 1, 0, 0)
    offset = timedelta(hours=2) if start <= dt_utc < end else timedelta(hours=1)
    return dt_utc + offset


class NetzNoeApi:
    """Thin stdlib client for the NetzNOE Smart-Meter portal's JSON API.

    Auth is a session cookie set by ``Login`` and kept alive by
    ``ExtendSessionLifetime``; a ``CookieJar`` opener carries it across calls.
    Reverse-engineered from the portal's own XHR traffic.
    """

    BASE = "https://smartmeter.netz-noe.at/orchestration"

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def _request(self, method: str, path: str, payload: dict | None = None):
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(self.BASE + path, data=body, method=method)
        req.add_header("Accept", "application/json, text/plain, */*")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        with self._opener.open(req, timeout=self.timeout) as resp:
            raw = resp.read()
        return json.loads(raw) if raw else None

    def login(self, user: str, pwd: str) -> None:
        self._request("POST", "/Authentication/Login", {"user": user, "pwd": pwd})

    def extend_session(self) -> None:
        self._request("GET", "/Authentication/ExtendSessionLifetime")

    def metering_points(self) -> list[dict]:
        return self._request(
            "GET", "/User/GetMeteringPointsByBusinesspartnerId?context=2"
        ) or []

    def consumption_day(self, meter_id: str, day: date) -> dict | None:
        # The portal uses unpadded month/day (e.g. 2026-6-7).
        arr = self._request(
            "GET",
            f"/ConsumptionRecord/Day?meterId={meter_id}&day={day.year}-{day.month}-{day.day}",
        )
        return arr[0] if isinstance(arr, list) and arr else None


class NetzNoeProvider(Provider):
    name = "netznoe"

    def parse(self, source: Path) -> Iterator[EnergyRecord]:
        meter_id = _meter_id_from_name(source)
        # utf-8-sig transparently strips the BOM.
        with source.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh, delimiter=";")
            header = next(reader, None)
            if not header or not header[0].lstrip().startswith("Messzeitpunkt"):
                raise ValueError(
                    f"{source.name}: unexpected header, not a NetzNOE Einspeisung export: {header!r}"
                )
            for row in reader:
                if not row or not row[0].strip():
                    continue
                ts = datetime.strptime(row[0].strip(), "%d.%m.%Y %H:%M")
                yield EnergyRecord(
                    timestamp=ts,
                    meter_id=meter_id,
                    provider=self.name,
                    feed_in_kwh=_num(row[1]) or 0.0,
                    eg_surplus_kwh=_num(row[3]) if len(row) > 3 else None,
                    eg_absorbed_kwh=_num(row[5]) if len(row) > 5 else None,
                    quality=(row[2].strip() or None) if len(row) > 2 else None,
                    eg_quality=(row[4].strip() or None) if len(row) > 4 else None,
                )

    # --- automated fetch (portal JSON API) --------------------------------

    @classmethod
    def credential_fields(cls) -> list[str]:
        return ["user", "pwd"]

    def fetch_records(
        self,
        *,
        credentials: dict[str, str],
        since: date | None = None,
        until: date | None = None,
        api: NetzNoeApi | None = None,
    ) -> Iterator[EnergyRecord]:
        """Log in and pull daily 15-min records for the feed-in meter.

        The same ``ConsumptionRecord/Day`` endpoint carries feed-in immediately
        and the EG split (absorbed / surplus) once it settles ~1-2 days later;
        re-pulling a rolling tail therefore upgrades earlier rows in place.
        """
        user = credentials.get("user")
        pwd = credentials.get("pwd")
        if not user or not pwd:
            raise ValueError("netznoe needs 'user' and 'pwd' credentials")

        api = api or NetzNoeApi()
        api.login(user, pwd)

        meters = api.metering_points()
        feed = next((m for m in meters if m.get("typeOfRelation") == "Einspeisung"), None)
        if feed is None:
            raise ValueError("no Einspeisung (feed-in) metering point on this account")
        meter_id = feed["meteringPointId"]

        until = until or date.today()
        since = since or (until - timedelta(days=7))

        day = since
        i = 0
        while day <= until:
            # Keep the session warm on long backfills; re-login if it lapsed.
            if i and i % 30 == 0:
                try:
                    api.extend_session()
                except Exception:
                    api.login(user, pwd)
            yield from self._records_for_day(api, meter_id, day)
            day += timedelta(days=1)
            i += 1

    def _records_for_day(
        self, api: NetzNoeApi, meter_id: str, day: date
    ) -> Iterator[EnergyRecord]:
        rec = api.consumption_day(meter_id, day)
        if not rec or not rec.get("peakDemandTimes"):
            return
        times = rec["peakDemandTimes"]
        metered = rec.get("meteredValues") or []
        absorbed = rec.get("selfCoverageValues") or [None] * len(times)
        surplus = rec.get("gridUsageLeftoverValues") or [None] * len(times)
        estimated = rec.get("estimatedValues") or [None] * len(times)
        for i, ts in enumerate(times):
            feed_in = metered[i] if i < len(metered) else None
            if feed_in is None:
                continue
            yield EnergyRecord(
                timestamp=_vienna_local(datetime.fromisoformat(ts)),
                meter_id=meter_id,
                provider=self.name,
                feed_in_kwh=feed_in,
                eg_surplus_kwh=surplus[i],
                eg_absorbed_kwh=absorbed[i],
                quality="L2" if estimated[i] is not None else "L1",
                eg_quality="L2" if absorbed[i] is not None else None,
            )
