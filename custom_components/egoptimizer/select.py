"""Exploration mode select: explore (probe to learn) vs locked (feed known)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MODES
from .entity import EGOptimizerEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback
) -> None:
    add([ModeSelect(hass.data[DOMAIN][entry.entry_id])])


class ModeSelect(EGOptimizerEntity, SelectEntity):
    _attr_name = "Learning mode"
    _attr_icon = "mdi:school-outline"
    _attr_options = MODES

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "mode")

    @property
    def current_option(self) -> str:
        return self.coordinator.mode

    async def async_select_option(self, option: str) -> None:
        self.coordinator.mode = option
        await self.coordinator.async_request_refresh()
