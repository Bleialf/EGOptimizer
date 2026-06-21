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
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import EGOptimizerEntity


@dataclass(frozen=True, kw_only=True)
class EGSensor(SensorEntityDescription):
    value: Callable[[dict], object] = lambda d: None
    # If set, read from the coordinator object instead of the brain response
    # (used for values the integration computes locally, e.g. the load average).
    coord: Callable[[object], object] | None = None


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


def _decision_info(data: dict) -> dict:
    debug = data.get("debug") or {}
    decision = debug.get("decision")
    if isinstance(decision, dict) and decision:
        return decision
    return {
        "path": _plan_state(data),
        "status": data.get("status"),
        "confidence": data.get("confidence"),
        "note": data.get("rationale"),
    }


# --- EG-absorption stat readers (from coordinator.stats = brain GET /stats) ---
def _stat_pct(block: str, field: str):
    def get(c):
        v = ((getattr(c, "stats", None) or {}).get(block) or {}).get(field)
        return round(v * 100.0, 1) if isinstance(v, (int, float)) else None
    return get


def _stat_kwh(block: str, field: str):
    def get(c):
        v = ((getattr(c, "stats", None) or {}).get(block) or {}).get(field)
        return round(v, 1) if isinstance(v, (int, float)) else None
    return get


def _best_hour(c):
    h = (getattr(c, "stats", None) or {}).get("best_hour")
    return f"{h:02d}:00" if isinstance(h, int) else None


SENSORS: tuple[EGSensor, ...] = (
    EGSensor(key="feed_kw", name="Feed setpoint", icon="mdi:transmission-tower-export",
             native_unit_of_measurement=UnitOfPower.WATT,
             device_class=SensorDeviceClass.POWER,
             # Export convention: negative watts means feeding into the grid.
             value=lambda d: (round(float(d.get("feed_kw")) * -1000.0, 0)
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
    # Locally-computed load figures (so you can see what the brain is fed).
    EGSensor(key="load_now", name="House load (now, avg)", icon="mdi:home-lightning-bolt",
             native_unit_of_measurement=UnitOfPower.KILO_WATT,
             device_class=SensorDeviceClass.POWER,
             coord=lambda c: c.load_now_kw),
    EGSensor(key="base_load", name="Base load (overnight est.)", icon="mdi:home-clock",
             native_unit_of_measurement=UnitOfPower.KILO_WATT,
             device_class=SensorDeviceClass.POWER,
             coord=lambda c: c.base_load_kw),
)


# EG-absorption history (how much the community is actually taking). These read
# the brain's GET /stats (cached on the coordinator), refreshed daily after the
# pull. The headline rate carries the full breakdown as attributes.
EG_SENSORS: tuple[EGSensor, ...] = (
    EGSensor(key="eg_absorption_rate_30d", name="EG absorption rate (30d)",
             icon="mdi:transmission-tower-import", native_unit_of_measurement=PERCENTAGE,
             coord=_stat_pct("recent", "absorption_rate")),
    EGSensor(key="eg_absorption_rate", name="EG absorption rate (all-time)",
             icon="mdi:chart-donut", native_unit_of_measurement=PERCENTAGE,
             coord=_stat_pct("all_time", "absorption_rate")),
    EGSensor(key="eg_absorbed_total", name="EG absorbed total",
             icon="mdi:battery-charging-high",
             native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
             coord=_stat_kwh("all_time", "absorbed_kwh")),
    EGSensor(key="eg_surplus_total", name="EG surplus spilled total",
             icon="mdi:transmission-tower", native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
             coord=_stat_kwh("all_time", "surplus_kwh")),
    EGSensor(key="eg_censored_pct", name="EG saturated (took 100%)",
             icon="mdi:gauge-full", native_unit_of_measurement=PERCENTAGE,
             coord=_stat_pct("all_time", "censored_pct")),
    EGSensor(key="eg_best_hour", name="EG best absorbing hour",
             icon="mdi:clock-star-four-points-outline", coord=_best_hour),
    # Status entity to confirm the daily pull is working.
    EGSensor(key="last_grid_fetch", name="Last grid fetch", icon="mdi:cloud-clock",
             entity_category=EntityCategory.DIAGNOSTIC,
             coord=lambda c: (getattr(c, "last_fetch", None) or {}).get("at") or "never"),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    add(EGOptimizerSensor(coordinator, d) for d in SENSORS + EG_SENSORS)


class EGOptimizerSensor(EGOptimizerEntity, SensorEntity):
    entity_description: EGSensor

    def __init__(self, coordinator, description: EGSensor) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        if self.entity_description.coord is not None:
            return self.entity_description.coord(self.coordinator)
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
                "decision": _decision_info(data),
            }
        if self.entity_description.key == "base_load":
            return {"source": getattr(self.coordinator, "base_load_source", None)}
        if self.entity_description.key == "eg_absorption_rate_30d":
            # Full EG-absorption breakdown hangs off the headline rate sensor.
            stats = getattr(self.coordinator, "stats", None) or {}
            return {
                "all_time": stats.get("all_time"),
                "recent": stats.get("recent"),
                "recent_days": stats.get("recent_days"),
                "best_hour": stats.get("best_hour"),
                "best_hour_rate": stats.get("best_hour_rate"),
                "worst_hour": stats.get("worst_hour"),
                "worst_hour_rate": stats.get("worst_hour_rate"),
                "settled_intervals": stats.get("settled_intervals"),
                "last_settled": stats.get("last_settled_ts"),
            }
        if self.entity_description.key == "last_grid_fetch":
            return getattr(self.coordinator, "last_fetch", None) or None
        return None
