"""Sensor platform for Sundance Spa Elfin integration."""
from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import SundanceElfinClient
from .const import (
    CONNECTION_SENSOR_UNIQUE_ID,
    DEFAULT_NAME,
    DOMAIN,
    TEMP_SENSOR_UNIQUE_ID,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Sundance Spa sensor entities."""
    client: SundanceElfinClient = hass.data[DOMAIN][entry.entry_id]
    
    async_add_entities([
        SundanceSpaTemperatureSensor(client, entry),
        SundanceSpaConnectionSensor(client, entry),
    ])


class SundanceSpaTemperatureSensor(SensorEntity):
    """Sensor entity for spa water temperature."""

    _attr_has_entity_name = True
    _attr_name = "Water Temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_icon = "mdi:thermometer-water"

    def __init__(self, client: SundanceElfinClient, entry: ConfigEntry) -> None:
        """Initialize the temperature sensor entity."""
        self._client = client
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{TEMP_SENSOR_UNIQUE_ID}"
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
    def native_value(self) -> float | None:
        """Return the current water temperature."""
        return self._client.state.current_temp

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._client.state.connected

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        attrs = {
            "target_temperature": self._client.state.target_temp,
            "is_heating": self._client.state.is_heating,
        }
        if self._client.state.last_update:
            attrs["last_update"] = datetime.fromtimestamp(
                self._client.state.last_update
            ).isoformat()
        return attrs

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


class SundanceSpaConnectionSensor(SensorEntity):
    """Sensor entity for Elfin connection status."""

    _attr_has_entity_name = True
    _attr_name = "Connection Status"
    _attr_icon = "mdi:lan-connect"

    def __init__(self, client: SundanceElfinClient, entry: ConfigEntry) -> None:
        """Initialize the connection sensor entity."""
        self._client = client
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{CONNECTION_SENSOR_UNIQUE_ID}"
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
    def native_value(self) -> str:
        """Return the connection status."""
        return "Connected" if self._client.state.connected else "Disconnected"

    @property
    def available(self) -> bool:
        """Return True - this entity is always available to report status."""
        return True

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        return {
            "host": self._client.host,
            "port": self._client.port,
        }

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
