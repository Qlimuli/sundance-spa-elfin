"""Light platform for Sundance Spa Elfin integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import SundanceElfinClient
from .const import DEFAULT_NAME, DOMAIN, LIGHT_UNIQUE_ID

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Sundance Spa light entity."""
    client: SundanceElfinClient = hass.data[DOMAIN][entry.entry_id]
    
    async_add_entities([SundanceSpaLight(client, entry)])


class SundanceSpaLight(LightEntity):
    """Light entity for controlling spa lighting."""

    _attr_has_entity_name = True
    _attr_name = "Light"
    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}
    _attr_icon = "mdi:hot-tub"

    def __init__(self, client: SundanceElfinClient, entry: ConfigEntry) -> None:
        """Initialize the light entity."""
        self._client = client
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{LIGHT_UNIQUE_ID}"
        self._unregister_callback: callable | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=DEFAULT_NAME,
            manufacturer="Sundance Spas",
            model="Cameo 880",
        )

    @property
    def is_on(self) -> bool:
        """Return true if the light is on."""
        return self._client.state.light_on

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._client.state.connected

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        if not self._client.state.light_on:
            await self._client.toggle_light()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        if self._client.state.light_on:
            await self._client.toggle_light()

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to Home Assistant."""
        self._unregister_callback = self._client.register_callback(
            self._handle_state_update
        )

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity is about to be removed."""
        if self._unregister_callback:
            self._unregister_callback()

    @callback
    def _handle_state_update(self) -> None:
        """Handle updated data from the client."""
        self.async_write_ha_state()
