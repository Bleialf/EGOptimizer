"""Config flow for EGOptimizer."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    FileSelector,
    FileSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    TextSelector,
)

from .const import (
    CONF_BRAIN_URL,
    CONF_CAPACITY_KWH,
    CONF_HARD_MIN_ENTITY,
    CONF_LOAD_AVG_MINUTES,
    CONF_LOAD_ENTITY,
    CONF_RETENTION_DAYS,
    CONF_SCAN_MINUTES,
    CONF_SOC_ENTITY,
    CONF_SOLCAST_ENTITY,
    CONF_SOLCAST_TOMORROW_ENTITY,
    DEFAULT_LOAD_AVG_MINUTES,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_SCAN_MINUTES,
    DOMAIN,
)

_SENSOR = EntitySelector(EntitySelectorConfig(domain=["sensor", "number", "input_number"]))

STEP_USER = vol.Schema(
    {
        vol.Required(CONF_BRAIN_URL, default="http://localhost:8787"): TextSelector(),
        vol.Required(CONF_CAPACITY_KWH, default=10.0): NumberSelector(
            NumberSelectorConfig(min=1, max=200, step=0.1, unit_of_measurement="kWh")
        ),
        vol.Required(CONF_SOC_ENTITY): _SENSOR,
        vol.Optional(CONF_LOAD_ENTITY): _SENSOR,
        vol.Optional(CONF_SOLCAST_ENTITY): _SENSOR,
        vol.Optional(CONF_SOLCAST_TOMORROW_ENTITY): _SENSOR,
        vol.Optional(CONF_HARD_MIN_ENTITY): _SENSOR,
        vol.Optional(CONF_SCAN_MINUTES, default=DEFAULT_SCAN_MINUTES): NumberSelector(
            NumberSelectorConfig(min=1, max=120, step=1, unit_of_measurement="min")
        ),
    }
)


class EGOptimizerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_BRAIN_URL])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="EGOptimizer", data=user_input)
        return self.async_show_form(step_id="user", data_schema=STEP_USER)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "EGOptimizerOptionsFlow":
        return EGOptimizerOptionsFlow()


class EGOptimizerOptionsFlow(OptionsFlow):
    """Everything configurable from the HA UI: upload data, settings, cleanup."""

    def __init__(self) -> None:
        self._uploaded = 0

    # --- helpers ---------------------------------------------------------
    def _merged(self) -> dict:
        return {**self.config_entry.data, **self.config_entry.options}

    def _base_url(self) -> str:
        return self._merged()[CONF_BRAIN_URL].rstrip("/")

    async def _post(self, url: str, data: bytes = b"") -> tuple[bool, str]:
        session = async_get_clientsession(self.hass)
        try:
            async with session.post(url, data=data, timeout=120) as resp:
                text = await resp.text()
                return resp.status == 200, text
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    # --- menu ------------------------------------------------------------
    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["upload_data", "settings", "delete_data"],
        )

    # --- upload CSVs (one at a time, loopable) ---------------------------
    async def async_step_upload_data(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            file_id = user_input["file"]

            def _read() -> tuple[str, bytes]:
                from homeassistant.components.file_upload import process_uploaded_file

                with process_uploaded_file(self.hass, file_id) as path:
                    return path.name, path.read_bytes()

            try:
                name, payload = await self.hass.async_add_executor_job(_read)
            except Exception as exc:  # noqa: BLE001
                errors["base"] = "upload_failed"
                name, payload = "", b""
            if not errors:
                ok, msg = await self._post(
                    f"{self._base_url()}/import?filename={name}&provider=netznoe&train=0",
                    payload,
                )
                if not ok:
                    errors["base"] = "cannot_connect"
                else:
                    self._uploaded += 1
                    if user_input.get("upload_another"):
                        return await self.async_step_upload_data()
                    # done: train once on everything imported
                    await self._post(f"{self._base_url()}/train")
                    return self.async_create_entry(title="", data=dict(self.config_entry.options))

        schema = vol.Schema(
            {
                vol.Required("file"): FileSelector(FileSelectorConfig(accept=".csv")),
                vol.Optional("upload_another", default=False): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="upload_data", data_schema=schema, errors=errors,
            description_placeholders={"count": str(self._uploaded)},
        )

    # --- settings (connection + wiring + retention) ----------------------
    async def async_step_settings(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        cur = self._merged()
        schema = vol.Schema(
            {
                vol.Required(CONF_BRAIN_URL, default=cur.get(CONF_BRAIN_URL)): TextSelector(),
                vol.Required(CONF_CAPACITY_KWH, default=cur.get(CONF_CAPACITY_KWH, 10.0)):
                    NumberSelector(NumberSelectorConfig(min=1, max=200, step=0.1, unit_of_measurement="kWh")),
                vol.Required(CONF_SOC_ENTITY, default=cur.get(CONF_SOC_ENTITY)): _SENSOR,
                vol.Optional(CONF_LOAD_ENTITY, default=cur.get(CONF_LOAD_ENTITY, "")): _SENSOR,
                vol.Optional(CONF_SOLCAST_ENTITY, default=cur.get(CONF_SOLCAST_ENTITY, "")): _SENSOR,
                vol.Optional(CONF_SOLCAST_TOMORROW_ENTITY, default=cur.get(CONF_SOLCAST_TOMORROW_ENTITY, "")): _SENSOR,
                vol.Optional(CONF_HARD_MIN_ENTITY, default=cur.get(CONF_HARD_MIN_ENTITY, "")): _SENSOR,
                vol.Optional(CONF_SCAN_MINUTES, default=cur.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES)):
                    NumberSelector(NumberSelectorConfig(min=1, max=120, step=1, unit_of_measurement="min")),
                vol.Optional(CONF_LOAD_AVG_MINUTES, default=cur.get(CONF_LOAD_AVG_MINUTES, DEFAULT_LOAD_AVG_MINUTES)):
                    NumberSelector(NumberSelectorConfig(min=0, max=60, step=1, unit_of_measurement="min")),
                vol.Optional(CONF_RETENTION_DAYS, default=cur.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS)):
                    NumberSelector(NumberSelectorConfig(min=0, max=3650, step=30, unit_of_measurement="days")),
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)

    # --- delete old data -------------------------------------------------
    async def async_step_delete_data(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get("confirm"):
                keep = int(user_input.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS))
                ok, msg = await self._post(f"{self._base_url()}/purge?keep_days={keep}")
                if not ok:
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_create_entry(title="", data=dict(self.config_entry.options))
            else:
                errors["base"] = "not_confirmed"
        schema = vol.Schema(
            {
                vol.Required(CONF_RETENTION_DAYS,
                             default=self._merged().get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS)):
                    NumberSelector(NumberSelectorConfig(min=0, max=3650, step=30, unit_of_measurement="days")),
                vol.Required("confirm", default=False): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="delete_data", data_schema=schema, errors=errors)
