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


def _plan_state(data: dict) -> str:
    decision = (data.get("debug") or {}).get("decision") or {}
    path = decision.get("path")
    if path == "learned_plan":
        return "learned"
    if path == "fallback_no_model":
        return "flat_no_model"
    if path == "budget_blocked":
        return "no_budget"
    return data.get("status") or "unknown"


def _plan_preview(data: dict, limit: int = 4) -> str:
    plan = data.get("feed_plan") or []
    parts: list[str] = []
    for p in plan:
        try:
            feed = float(p.get("feed_kwh", 0.0))
        except (TypeError, ValueError):
            continue
        if feed <= 0:
            continue
        t = str(p.get("time") or "")
        hhmm = t[11:16] if len(t) >= 16 else str(p.get("hour", "--")) + ":00"
        parts.append(f"{hhmm}={feed:.2f}kWh")
        if len(parts) >= limit:
            break
    return ", ".join(parts) if parts else "none"


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
    EGSensor(key="current_plan", name="Current plan", icon="mdi:timeline-clock-outline",
             value=lambda d: _plan_state(d)),
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
        if self.entity_description.key == "current_plan":
            return {
                "next_feed_time": data.get("next_feed_time"),
                "planned_tonight_kwh": data.get("planned_tonight_kwh"),
                "plan_preview": _plan_preview(data),
                "feed_plan": data.get("feed_plan"),
                "decision": (data.get("debug") or {}).get("decision"),
            }
        return None
