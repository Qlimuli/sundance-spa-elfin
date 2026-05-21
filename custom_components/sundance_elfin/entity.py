"""Base entity for Sundance Spa."""
from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .spa_client import SpaClient


class SundanceEntity(Entity):
    """Base entity for Sundance Spa."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, spa: SpaClient, entry_id: str, key: str) -> None:
        """Initialize the entity."""
        self._spa = spa
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        # FIX: software_id gehört zu SpaConfig, nicht SpaStatus
        sw_ver = self._spa.config.software_id if self._spa.config.software_id else None
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name=self._spa.model if self._spa.model else "Sundance Spa",
            manufacturer="Sundance Spas",
            model=self._spa.model if self._spa.model else None,
            sw_version=sw_ver,
        )

    @property
    def available(self) -> bool:
        """Return if the entity is available."""
        return self._spa.connected

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to hass."""
        self.async_on_remove(
            self._spa.add_update_callback(self._update_callback)
        )

    @callback
    def _update_callback(self) -> None:
        """Handle updated data."""
        self.async_write_ha_state()
