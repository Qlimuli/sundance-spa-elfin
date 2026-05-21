"""Config flow for Sundance Spa Elfin integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant

from .const import DEFAULT_PORT, DOMAIN
from .spa_client import SpaClient

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """
    Verbindung zur Spa testen.

    FIX: Timeout war zu kurz und führte zu False-Negative beim Config-Flow.
         Erhöht auf 20s für den ersten Status-Frame.
         Kein ConfigEntryNotReady hier – nur ValueError bei echtem Fehler.
    """
    host = data[CONF_HOST]
    port = data.get(CONF_PORT, DEFAULT_PORT)

    spa = SpaClient(host, port)

    try:
        if not await spa.connect():
            raise ValueError("cannot_connect")

        # FIX: Nur auf ersten geparsten Status warten (max 20s),
        #      keine volle Konfiguration nötig für den Validate-Step
        try:
            await asyncio.wait_for(
                spa._first_status_parsed.wait(),  # noqa: SLF001
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            # FIX: War als Fehler behandelt – jetzt als Warning mit Hinweis
            _LOGGER.warning(
                "Config timeout – spa connected but no status frame received yet. "
                "EW11 may need a moment to start sending. Integration will still be created."
            )
            # Kein raise – Verbindung war erfolgreich, Status kommt vielleicht gleich

        model = spa.model or "Sundance Spa"
        return {"title": f"{model} ({host})"}

    finally:
        await spa.disconnect()


class SundanceConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for Sundance Spa."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Doppelte Einträge verhindern
            await self.async_set_unique_id(
                f"{user_input[CONF_HOST]}:{user_input.get(CONF_PORT, DEFAULT_PORT)}"
            )
            self._abort_if_unique_id_configured()

            try:
                info = await validate_input(self.hass, user_input)
            except ValueError as err:
                error_key = str(err)
                if error_key == "cannot_connect":
                    errors["base"] = "cannot_connect"
                else:
                    errors["base"] = "unknown"
                    _LOGGER.exception("Unexpected error during config flow")
            except Exception:
                _LOGGER.exception("Unexpected error during config flow")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
