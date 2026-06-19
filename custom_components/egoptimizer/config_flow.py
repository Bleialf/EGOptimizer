"""Config flow for EGOptimizer."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    TextSelector,
)

from .const import (
    CONF_BRAIN_URL,
    CONF_CAPACITY_KWH,
    CONF_HARD_MIN_ENTITY,
    CONF_LOAD_ENTITY,
    CONF_SCAN_MINUTES,
    CONF_SOC_ENTITY,
    CONF_SOLCAST_ENTITY,
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
