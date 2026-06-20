"""Coordinator: gather HA state, call the brain, expose the recommendation."""

from __future__ import annotations

import logging
from collections import deque
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_AGGRESSIVENESS,
    CONF_BASE_LOAD_PERCENTILE,
    CONF_BASE_LOAD_WINDOW_MINUTES,
    CONF_BRAIN_URL,
    CONF_CAPACITY_KWH,
    CONF_HARD_MIN_ENTITY,
    CONF_LOAD_AVG_MINUTES,
    CONF_LOAD_ENTITY,
    CONF_MODE,
    CONF_SCAN_MINUTES,
    CONF_SOC_ENTITY,
    CONF_SOLCAST_ENTITY,
    CONF_SOLCAST_TOMORROW_ENTITY,
    CONF_TARGET_MORNING_SOC,
    DEFAULT_AGGRESSIVENESS,
    DEFAULT_BASE_LOAD_PERCENTILE,
    DEFAULT_BASE_LOAD_WINDOW_MINUTES,
    DEFAULT_LOAD_AVG_MINUTES,
    DEFAULT_MODE,
    DEFAULT_SCAN_MINUTES,
    DEFAULT_TARGET_MORNING_SOC,
    LOAD_SAMPLE_SECONDS,
    SOLCAST_ATTR,
)

_LOGGER = logging.getLogger(__name__)


class EGOptimizerCoordinator(DataUpdateCoordinator):
    """Polls the brain's /recommend endpoint with current HA state."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        data = {**entry.data, **entry.options}
        self.base_url = data[CONF_BRAIN_URL].rstrip("/")
        self._url = self.base_url + "/recommend"
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
        maxlen = max(1, round(max(self._avg_min, self._base_min) * 60 / LOAD_SAMPLE_SECONDS))
        self._load_samples: deque[float] = deque(maxlen=maxlen)
        # Last computed values, exposed as sensors.
        self.load_now_kw: float | None = None
        self.base_load_kw: float | None = None
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
            "soc_pct": soc,
            "capacity_kwh": self._capacity,
            "target_morning_soc_pct": self.target_morning_soc,
            "mode": self.mode,
            "exploration_aggressiveness": self.aggressiveness,
        }
        load_now, base_load = self._compute_loads()
        self.load_now_kw, self.base_load_kw = load_now, base_load
        if load_now is not None:
            payload["load_now_kw"] = load_now
        if base_load is not None:
            # The overnight draw the autarky sim uses -> the sustained baseline,
            # not the spiky instantaneous load.
            payload["night_load_kw"] = base_load
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

    async def _async_update_data(self) -> dict:
        payload = self._build_payload()
        if payload.get("soc_pct") is None:
            raise UpdateFailed(f"SoC entity {self._soc} unavailable")
        session = async_get_clientsession(self.hass)
        try:
            async with session.post(self._url, json=payload, timeout=15) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as exc:  # noqa: BLE001
            raise UpdateFailed(f"brain call failed: {exc}") from exc
