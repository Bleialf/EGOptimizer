"""Coordinator: gather HA state, call the brain, expose the recommendation."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_AGGRESSIVENESS,
    CONF_BRAIN_URL,
    CONF_CAPACITY_KWH,
    CONF_HARD_MIN_ENTITY,
    CONF_LOAD_ENTITY,
    CONF_MODE,
    CONF_SCAN_MINUTES,
    CONF_SOC_ENTITY,
    CONF_SOLCAST_ENTITY,
    CONF_TARGET_MORNING_SOC,
    DEFAULT_AGGRESSIVENESS,
    DEFAULT_MODE,
    DEFAULT_SCAN_MINUTES,
    DEFAULT_TARGET_MORNING_SOC,
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
        self._hard_min = data.get(CONF_HARD_MIN_ENTITY)
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

    def _num(self, entity_id: str | None, scale: float = 1.0) -> float | None:
        if not entity_id:
            return None
        st = self.hass.states.get(entity_id)
        if st is None or st.state in ("unknown", "unavailable", None, ""):
            return None
        try:
            return float(st.state) * scale
        except (ValueError, TypeError):
            return None

    def _build_payload(self) -> dict:
        soc = self._num(self._soc)
        payload: dict = {
            "soc_pct": soc,
            "capacity_kwh": self._capacity,
            "target_morning_soc_pct": self.target_morning_soc,
            "mode": self.mode,
            "exploration_aggressiveness": self.aggressiveness,
        }
        load = self._num(self._load)
        if load is not None:
            payload["load_now_kw"] = load
        hard_min = self._num(self._hard_min)
        if hard_min is not None:
            payload["hard_min_soc_pct"] = hard_min
        if self._solcast:
            st = self.hass.states.get(self._solcast)
            slots = st.attributes.get(SOLCAST_ATTR) if st else None
            if slots:
                payload["pv_forecast"] = slots
        return payload

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
