"""Sundance Spa – Config Flow (Einrichtung über HA-UI)."""
from __future__ import annotations

import asyncio
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from . import DOMAIN, SpaClient

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default="192.168.178.54"): str,
        vol.Required(CONF_PORT, default=8899): int,
    }
)


async def _test_connection(hass: HomeAssistant, host: str, port: int) -> str | None:
    """Gibt None zurück wenn OK, sonst einen Fehler-Key."""
    client = SpaClient(host, port)
    try:
        await asyncio.wait_for(client.connect(), timeout=8.0)
        ok = await client.wait_ready(timeout=10.0)
        await client.disconnect()
        return None if ok else "no_data"
    except asyncio.TimeoutError:
        return "timeout"
    except OSError:
        return "cannot_connect"
    except Exception:  # noqa: BLE001
        return "unknown"


class SpaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config Flow: Host + Port eingeben und Verbindung testen."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])

            # Doppelten Eintrag verhindern
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()

            err = await _test_connection(self.hass, host, port)
            if err:
                errors["base"] = err
            else:
                return self.async_create_entry(
                    title=f"Sundance Spa ({host})",
                    data={CONF_HOST: host, CONF_PORT: port},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )