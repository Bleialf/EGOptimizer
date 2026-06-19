"""Exploration mode select: explore (probe to learn) vs locked (feed known)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, MODES
from .entity import EGOptimizerEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback
) -> None:
    add([ModeSelect(hass.data[DOMAIN][entry.entry_id])])


class ModeSelect(EGOptimizerEntity, SelectEntity, RestoreEntity):
    _attr_name = "Learning mode"
    _attr_icon = "mdi:school-outline"
    _attr_options = MODES

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "mode")

    @property
    def current_option(self) -> str:
        return self.coordinator.mode

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is None or last.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return
        if last.state in MODES:
            self.coordinator.mode = last.state

    async def async_select_option(self, option: str) -> None:
        self.coordinator.mode = option
        await self.coordinator.async_request_refresh()
