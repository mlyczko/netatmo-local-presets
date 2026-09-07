"""Config flow for Netatmo Local Presets."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_NAME
from homeassistant.helpers import selector

from .const import CONF_SOURCE, DOMAIN

DEFAULT_NAME = "Netatmo Local Thermostat"


class NetatmoLocalPresetsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Netatmo Local Presets."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask which local climate entity to wrap and what to call it."""
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_SOURCE])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input[CONF_NAME], data=user_input
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_SOURCE): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="climate")
                ),
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)
