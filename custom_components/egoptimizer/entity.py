"""Shared base entity (groups everything under one device)."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EGOptimizerCoordinator


class EGOptimizerEntity(CoordinatorEntity[EGOptimizerCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: EGOptimizerCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name="EGOptimizer",
            manufacturer="EGOptimizer",
            model="EG feed-in optimizer",
        )
