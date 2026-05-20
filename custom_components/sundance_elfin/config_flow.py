"""Config flow for Sundance Spa Elfin integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from pybalboa import SpaClient

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.exceptions import HomeAssistantError

from .const import DEFAULT_PORT, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
    }
)


class SundanceElfinConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Sundance Spa Elfin."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]

            # Check if already configured with same host
            self._async_abort_entries_match({CONF_HOST: host})

            spa = SpaClient(host, port)
            try:
                if not await spa.connect():
                    errors["base"] = "cannot_connect"
                else:
                    await spa.async_configuration_loaded()
                    model = spa.model or "Sundance Spa"
                    await spa.disconnect()
                    return self.async_create_entry(
                        title=f"{model} ({host})",
                        data=user_input,
                    )
            except Exception:
                _LOGGER.exception("Error connecting to spa")
                errors["base"] = "cannot_connect"
            finally:
                await spa.disconnect()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""
