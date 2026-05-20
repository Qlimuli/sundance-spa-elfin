"""Switch platform for Sundance Spa Elfin integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import SundanceElfinClient
from .const import DEFAULT_NAME, DOMAIN, PUMP1_UNIQUE_ID, PUMP2_UNIQUE_ID

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Sundance Spa switch entities."""
    client: SundanceElfinClient = hass.data[DOMAIN][entry.entry_id]
    
    async_add_entities([
        SundanceSpaPump(client, entry, pump_number=1),
        SundanceSpaPump(client, entry, pump_number=2),
    ])


class SundanceSpaPump(SwitchEntity):
    """Switch entity for controlling spa pumps."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:pump"

    def __init__(
        self, 
        client: SundanceElfinClient, 
        entry: ConfigEntry,
        pump_number: int,
    ) -> None:
        """Initialize the pump switch entity."""
        self._client = client
        self._entry = entry
        self._pump_number = pump_number
        
        self._attr_name = f"Pump {pump_number}"
        self._attr_unique_id = f"{entry.entry_id}_{PUMP1_UNIQUE_ID if pump_number == 1 else PUMP2_UNIQUE_ID}"
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
        """Return true if the pump is on."""
        if self._pump_number == 1:
            return self._client.state.pump1_on
        return self._client.state.pump2_on

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._client.state.connected

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the pump on."""
        # Pumps cycle through off -> low -> high -> off
        # Keep toggling until we reach a non-off state
        if self._pump_number == 1:
            if not self._client.state.pump1_on:
                await self._client.toggle_pump1()
        else:
            if not self._client.state.pump2_on:
                await self._client.toggle_pump2()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the pump off."""
        # Toggle until pump is off (may take 1-2 toggles depending on current speed)
        if self._pump_number == 1:
            while self._client.state.pump1_on:
                await self._client.toggle_pump1()
        else:
            while self._client.state.pump2_on:
                await self._client.toggle_pump2()

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
