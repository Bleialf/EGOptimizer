"""Target morning SoC -- the user's autarky knob, editable in HA."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import EGOptimizerEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback
) -> None:
    coord = hass.data[DOMAIN][entry.entry_id]
    add([TargetMorningSoc(coord), ExplorationAggressiveness(coord)])


class TargetMorningSoc(EGOptimizerEntity, NumberEntity):
    _attr_name = "Target morning SoC"
    _attr_icon = "mdi:battery-charging-50"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 10
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "target_morning_soc")

    @property
    def native_value(self) -> float:
        return self.coordinator.target_morning_soc

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.target_morning_soc = float(value)
        await self.coordinator.async_request_refresh()


class ExplorationAggressiveness(EGOptimizerEntity, NumberEntity):
    _attr_name = "Exploration aggressiveness"
    _attr_icon = "mdi:flask-outline"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 1.0
    _attr_native_step = 0.05
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "exploration_aggressiveness")

    @property
    def native_value(self) -> float:
        return self.coordinator.aggressiveness

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.aggressiveness = float(value)
        await self.coordinator.async_request_refresh()
