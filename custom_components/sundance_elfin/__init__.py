"""The Sundance Spa Elfin integration."""
from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DEFAULT_PORT, RECONNECT_INTERVAL
from .spa_client import SpaClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.SWITCH,
    Platform.LIGHT,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
]

type SundanceConfigEntry = ConfigEntry[SpaClient]


async def async_setup_entry(hass: HomeAssistant, entry: SundanceConfigEntry) -> bool:
    """Set up Sundance Spa from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)

    _LOGGER.info("Connecting to Sundance Spa at %s:%s", host, port)

    spa = SpaClient(host, port)

    try:
        if not await spa.connect():
            raise ConfigEntryNotReady(f"Unable to connect to spa at {host}:{port}")

        # Wait for configuration with timeout – continue even if it fails
        try:
            await asyncio.wait_for(spa.async_configuration_loaded(), timeout=30)
            _LOGGER.info("Spa configuration loaded successfully")
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "Timeout waiting for spa configuration – continuing with limited features"
            )

    except Exception as err:
        _LOGGER.error("Error connecting to spa: %s", err)
        await spa.disconnect()
        raise ConfigEntryNotReady from err

    entry.runtime_data = spa

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # BUG FIX #8: Reconnect-Task starten.
    # Ohne diesen Task bleibt die Integration dauerhaft getrennt wenn der
    # EW11 neu startet oder das Netzwerk kurz ausfällt.
    entry.async_create_background_task(
        hass,
        _reconnect_loop(hass, entry, spa),
        name=f"sundance_elfin_reconnect_{entry.entry_id}",
    )

    return True


async def _reconnect_loop(
    hass: HomeAssistant,
    entry: SundanceConfigEntry,
    spa: SpaClient,
) -> None:
    """Keep the spa connection alive by reconnecting when it drops."""
    while True:
        await asyncio.sleep(RECONNECT_INTERVAL)

        if spa.connected:
            continue

        _LOGGER.info("Spa disconnected – attempting reconnect to %s", spa.host)
        try:
            if await spa.connect():
                _LOGGER.info("Reconnected to spa at %s", spa.host)
                try:
                    await asyncio.wait_for(
                        spa.async_configuration_loaded(), timeout=30
                    )
                except asyncio.TimeoutError:
                    _LOGGER.warning("Timeout re-loading configuration after reconnect")
            else:
                _LOGGER.warning(
                    "Reconnect attempt failed – will retry in %ds", RECONNECT_INTERVAL
                )
        except Exception as err:
            _LOGGER.error("Reconnect error: %s", err)


async def async_unload_entry(hass: HomeAssistant, entry: SundanceConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.disconnect()

    return unload_ok
