"""Binary sensor platform for Sundance Spa."""
from __future__ import annotations

import logging

from pybalboa import SpaClient
from pybalboa.enums import HeatState

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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
    """Set up binary sensor entities."""
    spa = entry.runtime_data
    async_add_entities([
        SundanceHeatingSensor(spa),
        SundanceFilterCycleSensor(spa, 1),
        SundanceFilterCycleSensor(spa, 2),
    ])


class SundanceHeatingSensor(SundanceEntity, BinarySensorEntity):
    """Binary sensor for heating state."""

    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(self, spa: SpaClient) -> None:
        """Initialize heating sensor."""
        super().__init__(spa, "heating")
        self._attr_name = "Heating"

    @property
    def is_on(self) -> bool:
        """Return true if heating."""
        return self._spa.heat_state == HeatState.HEATING


class SundanceFilterCycleSensor(SundanceEntity, BinarySensorEntity):
    """Binary sensor for filter cycle."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, spa: SpaClient, cycle: int) -> None:
        """Initialize filter cycle sensor."""
        super().__init__(spa, f"filter_cycle_{cycle}")
        self._cycle = cycle
        self._attr_name = f"Filter Cycle {cycle}"

    @property
    def is_on(self) -> bool:
        """Return true if filter cycle running."""
        if self._cycle == 1:
            return self._spa.filter_cycle_1_running
        return self._spa.filter_cycle_2_running
