"""Config flow for Sundance Spa Elfin integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DEFAULT_NAME, DEFAULT_PORT, DOMAIN, CONNECTION_TIMEOUT

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    host = data[CONF_HOST]
    port = data[CONF_PORT]

    try:
        # Try to establish a connection to validate the input
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=CONNECTION_TIMEOUT,
        )
        writer.close()
        await writer.wait_closed()
    except asyncio.TimeoutError as err:
        _LOGGER.error("Timeout connecting to %s:%s", host, port)
        raise CannotConnect from err
    except OSError as err:
        _LOGGER.error("Cannot connect to %s:%s: %s", host, port, err)
        raise CannotConnect from err

    # Return info that you want to store in the config entry.
    return {"title": f"{DEFAULT_NAME} ({host})"}


class SundanceElfinConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Sundance Spa Elfin."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        
        if user_input is not None:
            # Check if already configured with same host
            self._async_abort_entries_match({CONF_HOST: user_input[CONF_HOST]})
            
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""
