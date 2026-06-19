"""Sensors exposing the brain's recommendation."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfEnergy, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import EGOptimizerEntity


@dataclass(frozen=True, kw_only=True)
class EGSensor(SensorEntityDescription):
    value: Callable[[dict], object] = lambda d: None


SENSORS: tuple[EGSensor, ...] = (
    EGSensor(key="feed_kw", name="Feed setpoint", icon="mdi:transmission-tower-export",
             native_unit_of_measurement=UnitOfPower.WATT,
             device_class=SensorDeviceClass.POWER,
             value=lambda d: (round(float(d.get("feed_kw")) * 1000.0, 0)
                              if d.get("feed_kw") is not None else None)),
    EGSensor(key="status", name="Status", icon="mdi:state-machine",
             value=lambda d: d.get("status")),
    EGSensor(key="confidence", name="Confidence", icon="mdi:head-question-outline",
             value=lambda d: d.get("confidence")),
    EGSensor(key="eg_budget_kwh", name="EG budget tonight", icon="mdi:battery-arrow-up",
             native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
             value=lambda d: d.get("eg_budget_kwh")),
    EGSensor(key="planned_tonight_kwh", name="Planned tonight", icon="mdi:calendar-clock",
             native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
             value=lambda d: d.get("planned_tonight_kwh")),
    EGSensor(key="trough_soc_pct", name="Forecast trough SoC", icon="mdi:battery-low",
             native_unit_of_measurement=PERCENTAGE, value=lambda d: d.get("trough_soc_pct")),
    EGSensor(key="trough_time", name="Trough time", icon="mdi:clock-alert-outline",
             value=lambda d: d.get("trough_time")),
    EGSensor(key="pv_takeover_time", name="PV takeover time", icon="mdi:weather-sunny",
             value=lambda d: d.get("pv_takeover_time")),
    EGSensor(key="rationale", name="Reasoning", icon="mdi:text-long",
             value=lambda d: (d.get("rationale") or "")[:255]),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    add(EGOptimizerSensor(coordinator, d) for d in SENSORS)


class EGOptimizerSensor(EGOptimizerEntity, SensorEntity):
    entity_description: EGSensor

    def __init__(self, coordinator, description: EGSensor) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        return self.entity_description.value(self.coordinator.data or {})

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        if self.entity_description.key == "feed_kw":
            # Everything a dashboard / debugging needs hangs off the headline sensor.
            return {
                "explore": data.get("explore"),
                "mode": data.get("mode"),
                "context_observations": data.get("context_observations"),
                "next_feed_time": data.get("next_feed_time"),
                "feed_plan": data.get("feed_plan"),
                "soc_forecast": data.get("soc_forecast"),
                "debug": data.get("debug"),
            }
        return None
