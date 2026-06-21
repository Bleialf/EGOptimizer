"""Coordinator: gather HA state, call the brain, expose the recommendation."""

from __future__ import annotations

import logging
from collections import deque
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AGGRESSIVENESS,
    CONF_BASE_LOAD_PERCENTILE,
    CONF_BASE_LOAD_WINDOW_MINUTES,
    CONF_BRAIN_URL,
    CONF_CAPACITY_KWH,
    CONF_FETCH_HOUR,
    CONF_FETCH_PASSWORD,
    CONF_FETCH_USERNAME,
    CONF_HARD_MIN_ENTITY,
    CONF_LOAD_AVG_MINUTES,
    CONF_LOAD_ENTITY,
    CONF_MODE,
    CONF_NIGHT_LOAD_OVERRIDE_KW,
    CONF_PROVIDER,
    CONF_SCAN_MINUTES,
    CONF_SOC_ENTITY,
    CONF_SOLCAST_ENTITY,
    CONF_SOLCAST_TOMORROW_ENTITY,
    CONF_TARGET_MORNING_SOC,
    DEFAULT_AGGRESSIVENESS,
    DEFAULT_BASE_LOAD_PERCENTILE,
    DEFAULT_BASE_LOAD_WINDOW_MINUTES,
    DEFAULT_FETCH_HOUR,
    DEFAULT_LOAD_AVG_MINUTES,
    DEFAULT_MODE,
    DEFAULT_NIGHT_LOAD_OVERRIDE_KW,
    DEFAULT_PROVIDER,
    DEFAULT_SCAN_MINUTES,
    DEFAULT_TARGET_MORNING_SOC,
    LOAD_SAMPLE_SECONDS,
    NIGHT_END_HOUR,
    NIGHT_HISTORY_DAYS,
    NIGHT_LOAD_REFRESH_MIN,
    NIGHT_START_HOUR,
    SOLCAST_ATTR,
    STATS_REFRESH_MIN,
)

_LOGGER = logging.getLogger(__name__)


class EGOptimizerCoordinator(DataUpdateCoordinator):
    """Polls the brain's /recommend endpoint with current HA state."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        data = {**entry.data, **entry.options}
        self.base_url = data[CONF_BRAIN_URL].rstrip("/")
        self._url = self.base_url + "/recommend"
        # Automated daily pull from the grid operator (brain does the login).
        self._provider = data.get(CONF_PROVIDER, DEFAULT_PROVIDER)
        self._fetch_user = data.get(CONF_FETCH_USERNAME) or ""
        self._fetch_pwd = data.get(CONF_FETCH_PASSWORD) or ""
        self._fetch_hour = int(data.get(CONF_FETCH_HOUR, DEFAULT_FETCH_HOUR))
        # EG-absorption stats (GET /stats), surfaced as sensors. Throttled.
        self.stats: dict = {}
        self._stats_at = None
        # Result of the most recent /fetch, surfaced as a status sensor.
        self.last_fetch: dict = {}
        self._capacity = float(data[CONF_CAPACITY_KWH])
        self._soc = data[CONF_SOC_ENTITY]
        self._load = data.get(CONF_LOAD_ENTITY)
        self._solcast = data.get(CONF_SOLCAST_ENTITY)
        self._solcast_tomorrow = data.get(CONF_SOLCAST_TOMORROW_ENTITY)
        self._hard_min = data.get(CONF_HARD_MIN_ENTITY)
        # Sample the raw load on a fast internal timer (no statistics sensor
        # needed). From the buffer we derive two figures:
        #   load_now  = mean of the last `avg_min`  -> the immediate draw
        #   base_load = low percentile over `base_min` -> the SUSTAINED baseline,
        #               sent as night_load_kw so the overnight battery sim isn't
        #               drained by a transient daytime spike.
        self._avg_min = float(data.get(CONF_LOAD_AVG_MINUTES, DEFAULT_LOAD_AVG_MINUTES))
        self._base_min = float(data.get(CONF_BASE_LOAD_WINDOW_MINUTES, DEFAULT_BASE_LOAD_WINDOW_MINUTES))
        self._base_pct = float(data.get(CONF_BASE_LOAD_PERCENTILE, DEFAULT_BASE_LOAD_PERCENTILE))
        self._night_override = float(data.get(CONF_NIGHT_LOAD_OVERRIDE_KW, DEFAULT_NIGHT_LOAD_OVERRIDE_KW))
        maxlen = max(1, round(max(self._avg_min, self._base_min) * 60 / LOAD_SAMPLE_SECONDS))
        self._load_samples: deque[float] = deque(maxlen=maxlen)
        # Last computed values, exposed as sensors.
        self.load_now_kw: float | None = None
        self.base_load_kw: float | None = None
        self.base_load_source: str = "—"
        # Cached overnight estimate from recorder history (refreshed hourly).
        self._hist_night_kw: float | None = None
        self._hist_at = None
        # Mutable runtime controls, also editable via number/select entities.
        self.target_morning_soc = float(
            data.get(CONF_TARGET_MORNING_SOC, DEFAULT_TARGET_MORNING_SOC)
        )
        self.mode = data.get(CONF_MODE, DEFAULT_MODE)
        self.aggressiveness = float(data.get(CONF_AGGRESSIVENESS, DEFAULT_AGGRESSIVENESS))

        super().__init__(
            hass,
            _LOGGER,
            name="EGOptimizer",
            update_interval=timedelta(
                minutes=int(data.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES))
            ),
        )

    def _num(
        self,
        entity_id: str | None,
        scale: float = 1.0,
        *,
        power_to_kw: bool = False,
    ) -> float | None:
        if not entity_id:
            return None
        st = self.hass.states.get(entity_id)
        if st is None or st.state in ("unknown", "unavailable", None, ""):
            return None
        try:
            value = float(st.state) * scale
            if power_to_kw:
                unit = str(st.attributes.get("unit_of_measurement") or "").strip().lower()
                if unit in ("w", "watt", "watts"):
                    return value / 1000.0
                if unit in ("mw", "megawatt", "megawatts"):
                    return value * 1000.0
            return value
        except (ValueError, TypeError):
            return None

    @callback
    def _sample_load(self, _now=None) -> None:
        """Append the current raw load (kW) to the rolling buffer (fast timer)."""
        val = self._num(self._load, power_to_kw=True)
        if val is not None:
            self._load_samples.append(val)

    def start_load_sampling(self):
        """Begin sampling the load sensor; returns an unsubscribe callback."""
        if not self._load:
            return lambda: None
        self._sample_load()  # seed immediately so the first recompute isn't raw
        return async_track_time_interval(
            self.hass, self._sample_load, timedelta(seconds=LOAD_SAMPLE_SECONDS)
        )

    def _compute_loads(self) -> tuple[float | None, float | None]:
        """(load_now, base_load) in kW from the sample buffer.

        load_now  = mean of the most recent `avg_min` of samples.
        base_load = `base_pct` percentile over the whole (`base_min`) buffer --
                    the sustained baseline, used as the overnight draw.
        Falls back to the instantaneous reading until samples accumulate.
        """
        samples = list(self._load_samples)
        if not samples:
            inst = self._num(self._load, power_to_kw=True)
            return inst, inst
        now_n = max(1, round(self._avg_min * 60 / LOAD_SAMPLE_SECONDS))
        recent = samples[-now_n:]
        load_now = sum(recent) / len(recent)
        ordered = sorted(samples)
        k = min(len(ordered) - 1, int(self._base_pct / 100.0 * len(ordered)))
        base = ordered[k]
        return round(load_now, 4), round(base, 4)

    def _build_payload(self) -> dict:
        soc = self._num(self._soc)
        payload: dict = {
            # LOCAL wall-clock (naive). The brain buckets by hour and the model
            # was trained on local CSV timestamps; the Solcast forecast is local
            # too. Without this the brain falls back to the container clock (UTC)
            # and both the bucket lookup and the displayed times are offset.
            "timestamp": dt_util.now().replace(microsecond=0, tzinfo=None).isoformat(),
            "soc_pct": soc,
            "capacity_kwh": self._capacity,
            "target_morning_soc_pct": self.target_morning_soc,
            "mode": self.mode,
            "exploration_aggressiveness": self.aggressiveness,
        }
        # load_now_kw / base_load_kw were resolved in _async_update_data.
        if self.load_now_kw is not None:
            payload["load_now_kw"] = self.load_now_kw
        if self.base_load_kw is not None:
            # The overnight draw the autarky sim uses -> the sustained baseline
            # (recorder night history preferred), not the spiky instantaneous load.
            payload["night_load_kw"] = self.base_load_kw
        hard_min = self._num(self._hard_min)
        if hard_min is not None:
            payload["hard_min_soc_pct"] = hard_min
        # The brain ALWAYS needs the forward curve = today's remaining hours +
        # tomorrow's (the overnight trough's morning recharge lives in
        # tomorrow). Concatenate both every call; the brain ignores past slots.
        tomorrow = self._solcast_tomorrow or self._auto_tomorrow()
        slots = self._solcast_slots(self._solcast) + self._solcast_slots(tomorrow)
        if slots:
            payload["pv_forecast"] = slots
        return payload

    def _auto_tomorrow(self) -> str | None:
        """Guess the 'tomorrow' sensor from the 'today' one if not set explicitly.

        Solcast names them ..._forecast_today / ..._forecast_tomorrow, so a user
        who only picks 'today' still gets tomorrow's recharge automatically.
        """
        if self._solcast and "today" in self._solcast:
            return self._solcast.replace("today", "tomorrow")
        return None

    def _solcast_slots(self, entity_id: str | None) -> list:
        if not entity_id:
            return []
        st = self.hass.states.get(entity_id)
        slots = st.attributes.get(SOLCAST_ATTR) if st else None
        return list(slots) if slots else []

    async def _async_resolve_base_load(self, rolling_base: float | None) -> float | None:
        """Pick the overnight base load: manual override > recorder history > live."""
        if self._night_override and self._night_override > 0:
            self.base_load_source = "manual override"
            return round(self._night_override, 4)
        hist = await self._async_history_night_load()
        if hist is not None:
            self.base_load_source = (
                f"history {NIGHT_START_HOUR:02d}-{NIGHT_END_HOUR:02d}h p{int(self._base_pct)}"
            )
            return hist
        if rolling_base is not None:
            self.base_load_source = "live (history warming up)"
            return rolling_base
        self.base_load_source = "—"
        return None

    async def _async_history_night_load(self) -> float | None:
        """`base_pct` percentile of the load's hourly-mean during the night window,
        over the last NIGHT_HISTORY_DAYS, from the recorder. Cached hourly."""
        if not self._load:
            return None
        now = dt_util.utcnow()
        if (self._hist_night_kw is not None and self._hist_at is not None
                and (now - self._hist_at) < timedelta(minutes=NIGHT_LOAD_REFRESH_MIN)):
            return self._hist_night_kw
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import statistics_during_period
        except Exception:  # recorder not available
            return None

        start = now - timedelta(days=NIGHT_HISTORY_DAYS)

        def _query():
            return statistics_during_period(
                self.hass, start, now, {self._load}, "hour", None, {"mean"}
            )

        try:
            stats = await get_instance(self.hass).async_add_executor_job(_query)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("night-load history query failed: %s", exc)
            return None
        rows = (stats or {}).get(self._load)
        if not rows:
            return None

        st = self.hass.states.get(self._load)
        unit = str((st.attributes.get("unit_of_measurement") if st else "") or "").strip().lower()
        div = 1000.0 if unit in ("w", "watt", "watts") else 1.0
        wraps = NIGHT_START_HOUR >= NIGHT_END_HOUR

        night: list[float] = []
        for row in rows:
            mean = row.get("mean")
            if mean is None:
                continue
            ts = row.get("start")
            dt = dt_util.utc_from_timestamp(ts) if isinstance(ts, (int, float)) else ts
            hour = dt_util.as_local(dt).hour
            in_night = (hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR) if wraps \
                else (NIGHT_START_HOUR <= hour < NIGHT_END_HOUR)
            if in_night:
                night.append(mean / div)
        if not night:
            return None
        night.sort()
        k = min(len(night) - 1, int(self._base_pct / 100.0 * len(night)))
        self._hist_night_kw = round(night[k], 4)
        self._hist_at = now
        _LOGGER.debug("night base load from history: %.3f kW (%d night hours)",
                      self._hist_night_kw, len(night))
        return self._hist_night_kw

    async def _async_update_data(self) -> dict:
        load_now, rolling_base = self._compute_loads()
        self.load_now_kw = load_now
        self.base_load_kw = await self._async_resolve_base_load(rolling_base)
        payload = self._build_payload()
        if payload.get("soc_pct") is None:
            raise UpdateFailed(f"SoC entity {self._soc} unavailable")
        session = async_get_clientsession(self.hass)
        try:
            async with session.post(self._url, json=payload, timeout=15) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except Exception as exc:  # noqa: BLE001
            raise UpdateFailed(f"brain call failed: {exc}") from exc
        await self._async_refresh_stats()  # cheap, throttled; never fails the update
        return data

    # --- automated daily data pull + EG-absorption stats -----------------
    def has_fetch_credentials(self) -> bool:
        return bool(self._fetch_user and self._fetch_pwd)

    def start_daily_fetch(self):
        """Schedule the daily portal pull; returns an unsubscribe callback.

        Fires once a day at the configured local hour, after the operator has
        settled the prior day's EG allocation. No-op without credentials.
        """
        if not self.has_fetch_credentials():
            return lambda: None
        return async_track_time_change(
            self.hass, self._scheduled_fetch, hour=self._fetch_hour, minute=7, second=0
        )

    @callback
    def _scheduled_fetch(self, _now=None) -> None:
        self.hass.async_create_task(self.async_fetch_now())

    async def async_fetch_now(
        self, since: str | None = None, until: str | None = None
    ) -> dict:
        """Trigger the brain to pull the latest data from the operator's API.

        Sends credentials for this call only; the brain keeps no secrets. With
        no range, the brain picks a rolling window (from just before the last
        settled day) so the gap to today is filled and the unsettled tail is
        re-pulled as the EG split lands. Pass ``since`` for a one-time backfill.
        """
        if not self.has_fetch_credentials():
            raise UpdateFailed("no grid-operator credentials configured")
        body = {
            "provider": self._provider,
            "credentials": {"user": self._fetch_user, "pwd": self._fetch_pwd},
            "train": True,
        }
        if since:
            body["since"] = since
        if until:
            body["until"] = until
        session = async_get_clientsession(self.hass)
        async with session.post(self.base_url + "/fetch", json=body, timeout=300) as resp:
            resp.raise_for_status()
            result = await resp.json()
        store = result.get("store") or {}
        self.last_fetch = {
            "at": dt_util.now().replace(microsecond=0).isoformat(),
            "imported": result.get("imported"),
            "dropped": result.get("dropped"),
            "total": store.get("n"),
            "last_ts": store.get("last_ts"),
        }
        _LOGGER.info(
            "fetch %s: imported=%s total=%s last=%s",
            self._provider, result.get("imported"), store.get("n"), store.get("last_ts"),
        )
        await self._async_refresh_stats(force=True)
        await self.async_request_refresh()
        return result

    async def _async_refresh_stats(self, force: bool = False) -> None:
        now = dt_util.utcnow()
        if (not force and self._stats_at is not None
                and (now - self._stats_at) < timedelta(minutes=STATS_REFRESH_MIN)):
            return
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(self.base_url + "/stats", timeout=15) as resp:
                resp.raise_for_status()
                self.stats = await resp.json()
            self._stats_at = now
        except Exception as exc:  # noqa: BLE001 -- stats are non-essential
            _LOGGER.debug("stats refresh failed: %s", exc)
