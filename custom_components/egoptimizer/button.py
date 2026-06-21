"""Action buttons for EGOptimizer."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import EGOptimizerEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback
) -> None:
    coord = hass.data[DOMAIN][entry.entry_id]
    buttons = [TrainModelButton(coord), RefreshNowButton(coord)]
    if coord.has_fetch_credentials():
        buttons.append(FetchDataButton(coord))
    add(buttons)


class TrainModelButton(EGOptimizerEntity, ButtonEntity):
    _attr_name = "Train model"
    _attr_icon = "mdi:school"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "train_model")

    async def async_press(self) -> None:
        session = async_get_clientsession(self.hass)
        async with session.post(f"{self.coordinator.base_url}/train", timeout=120) as resp:
            if resp.status != 200:
                raise HomeAssistantError(f"Train failed: {await resp.text()}")
        await self.coordinator.async_request_refresh()


class RefreshNowButton(EGOptimizerEntity, ButtonEntity):
    _attr_name = "Refresh recommendation"
    _attr_icon = "mdi:refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "refresh_now")

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()


class FetchDataButton(EGOptimizerEntity, ButtonEntity):
    """Pull the latest data from the grid operator now (brain logs in)."""

    _attr_name = "Fetch grid data now"
    _attr_icon = "mdi:cloud-download"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "fetch_data")

    async def async_press(self) -> None:
        try:
            await self.coordinator.async_fetch_now()
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(f"Fetch failed: {exc}") from exc
