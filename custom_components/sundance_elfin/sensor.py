"""Sensor platform for Sundance Spa."""
from __future__ import annotations

import logging

from pybalboa import SpaClient

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

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SundanceConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities."""
    spa = entry.runtime_data
    async_add_entities([SundanceTemperatureSensor(spa)])


class SundanceTemperatureSensor(SundanceEntity, SensorEntity):
    """Temperature sensor for spa water."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, spa: SpaClient) -> None:
        """Initialize temperature sensor."""
        super().__init__(spa, "temperature")
        self._attr_name = "Water Temperature"

    @property
    def native_value(self) -> float | None:
        """Return current temperature."""
        return self._spa.temperature
