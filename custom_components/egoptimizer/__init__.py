"""EGOptimizer Home Assistant integration.

Thin HA-side client for the EGOptimizer "brain": gathers live state (battery
SoC, house load, Solcast forecast), POSTs it to the brain's /recommend API, and
exposes the result (feed setpoint, EG budget, trough, reasoning) as entities.

Also provides services to upload a CSV export and to (re)train the model, so you
never have to place files in a folder.
"""

from __future__ import annotations

from pathlib import Path

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, PLATFORMS
from .coordinator import EGOptimizerCoordinator

IMPORT_SCHEMA = vol.Schema(
    {
        vol.Optional("path"): cv.string,
        vol.Optional("content"): cv.string,
        vol.Optional("filename"): cv.string,
        vol.Optional("provider", default="netznoe"): cv.string,
        vol.Optional("train", default=True): cv.boolean,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = EGOptimizerCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    _register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, "import_csv")
            hass.services.async_remove(DOMAIN, "train")
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _any_coordinator(hass: HomeAssistant) -> EGOptimizerCoordinator:
    return next(iter(hass.data[DOMAIN].values()))


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "import_csv"):
        return

    async def _import_csv(call: ServiceCall) -> None:
        coord = _any_coordinator(hass)
        path = call.data.get("path")
        content = call.data.get("content")
        if path:
            data = await hass.async_add_executor_job(Path(path).read_bytes)
            filename = call.data.get("filename") or Path(path).name
        elif content:
            data = content.encode("utf-8")
            filename = call.data.get("filename") or "upload.csv"
        else:
            raise HomeAssistantError("Provide either 'path' or 'content'.")

        url = (
            f"{coord.base_url}/import?filename={filename}"
            f"&provider={call.data['provider']}&train={str(call.data['train']).lower()}"
        )
        session = async_get_clientsession(hass)
        async with session.post(url, data=data, timeout=120) as resp:
            if resp.status != 200:
                raise HomeAssistantError(f"Import failed: {await resp.text()}")
        await coord.async_request_refresh()

    async def _train(call: ServiceCall) -> None:
        coord = _any_coordinator(hass)
        session = async_get_clientsession(hass)
        async with session.post(f"{coord.base_url}/train", timeout=120) as resp:
            if resp.status != 200:
                raise HomeAssistantError(f"Train failed: {await resp.text()}")
        await coord.async_request_refresh()

    hass.services.async_register(DOMAIN, "import_csv", _import_csv, schema=IMPORT_SCHEMA)
    hass.services.async_register(DOMAIN, "train", _train)
