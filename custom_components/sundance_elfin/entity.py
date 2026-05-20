"""Base entity for Sundance Spa."""
from __future__ import annotations

from pybalboa import SpaClient
from pybalboa.control import EVENT_UPDATE

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN


class SundanceEntity(Entity):
    """Base entity for Sundance Spa."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, spa: SpaClient, key: str) -> None:
        """Initialize the entity."""
        self._spa = spa
        self._attr_unique_id = f"{spa.mac_address}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._spa.mac_address)},
            name=self._spa.model or "Sundance Spa",
            manufacturer="Sundance Spas",
            model=self._spa.model,
            sw_version=self._spa.software_version,
        )

    @property
    def available(self) -> bool:
        """Return if the entity is available."""
        return self._spa.connected

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to hass."""
        self.async_on_remove(self._spa.on(EVENT_UPDATE, self._update_callback))

    @callback
    def _update_callback(self) -> None:
        """Handle updated data."""
        self.async_write_ha_state()
