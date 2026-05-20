"""Sensor platform for Sundance Spa."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SundanceConfigEntry
from .entity import SundanceEntity
from .spa_client import SpaClient

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SundanceConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities."""
    spa = entry.runtime_data
    
    entities = [
        SundanceTemperatureSensor(spa, entry.entry_id),
        SundanceTargetTemperatureSensor(spa, entry.entry_id),
        SundanceConnectionSensor(spa, entry.entry_id),
        SundanceTimeSensor(spa, entry.entry_id),
    ]
    
    async_add_entities(entities)


class SundanceTemperatureSensor(SundanceEntity, SensorEntity):
    """Sensor for current water temperature."""
    
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, spa: SpaClient, entry_id: str) -> None:
        """Initialize temperature sensor."""
        super().__init__(spa, entry_id, "temperature")
        self._attr_name = "Water Temperature"

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        if self._spa.temperature_unit_celsius:
            return UnitOfTemperature.CELSIUS
        return UnitOfTemperature.FAHRENHEIT

    @property
    def native_value(self) -> float | None:
        """Return the current temperature."""
        return self._spa.temperature


class SundanceTargetTemperatureSensor(SundanceEntity, SensorEntity):
    """Sensor for target temperature."""
    
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, spa: SpaClient, entry_id: str) -> None:
        """Initialize target temperature sensor."""
        super().__init__(spa, entry_id, "target_temperature")
        self._attr_name = "Target Temperature"

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        if self._spa.temperature_unit_celsius:
            return UnitOfTemperature.CELSIUS
        return UnitOfTemperature.FAHRENHEIT

    @property
    def native_value(self) -> float | None:
        """Return the target temperature."""
        return self._spa.target_temperature


class SundanceConnectionSensor(SundanceEntity, SensorEntity):
    """Sensor for connection status."""
    
    _attr_icon = "mdi:wifi"

    def __init__(self, spa: SpaClient, entry_id: str) -> None:
        """Initialize connection sensor."""
        super().__init__(spa, entry_id, "connection")
        self._attr_name = "Connection Status"

    @property
    def native_value(self) -> str:
        """Return the connection status."""
        return "Connected" if self._spa.connected else "Disconnected"

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        return {
            "host": self._spa.host,
            "model": self._spa.model or "Unknown",
            "software": self._spa.status.software_id or "Unknown",
        }


class SundanceTimeSensor(SundanceEntity, SensorEntity):
    """Sensor for spa time."""
    
    _attr_icon = "mdi:clock"

    def __init__(self, spa: SpaClient, entry_id: str) -> None:
        """Initialize time sensor."""
        super().__init__(spa, entry_id, "spa_time")
        self._attr_name = "Spa Time"

    @property
    def native_value(self) -> str:
        """Return the spa time."""
        status = self._spa.status
        if status.clock_24hr:
            return f"{status.hour:02d}:{status.minute:02d}"
        
        hour_12 = status.hour % 12
        if hour_12 == 0:
            hour_12 = 12
        period = "AM" if status.hour < 12 else "PM"
        return f"{hour_12}:{status.minute:02d} {period}"
