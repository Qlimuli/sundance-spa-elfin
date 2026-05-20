"""Binary sensor platform for Sundance Spa."""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SundanceConfigEntry
from .entity import SundanceEntity
from .spa_client import SpaClient, HeatState

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SundanceConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities."""
    spa = entry.runtime_data
    
    entities = [
        SundanceHeatingSensor(spa, entry.entry_id),
        SundanceCircPumpSensor(spa, entry.entry_id),
        SundanceFilteringSensor(spa, entry.entry_id),
    ]
    
    async_add_entities(entities)


class SundanceHeatingSensor(SundanceEntity, BinarySensorEntity):
    """Binary sensor for heating state."""

    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(self, spa: SpaClient, entry_id: str) -> None:
        """Initialize heating sensor."""
        super().__init__(spa, entry_id, "heating")
        self._attr_name = "Heating"

    @property
    def is_on(self) -> bool:
        """Return true if heating."""
        return self._spa.heat_state == HeatState.HEATING

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        return {
            "heat_state": self._spa.heat_state.name,
            "heat_mode": self._spa.heat_mode.name,
        }


class SundanceCircPumpSensor(SundanceEntity, BinarySensorEntity):
    """Binary sensor for circulation pump."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, spa: SpaClient, entry_id: str) -> None:
        """Initialize circ pump sensor."""
        super().__init__(spa, entry_id, "circ_pump")
        self._attr_name = "Circulation Pump"
        self._attr_icon = "mdi:pump"

    @property
    def is_on(self) -> bool:
        """Return true if circulation pump is running."""
        return self._spa.status.circ_pump


class SundanceFilteringSensor(SundanceEntity, BinarySensorEntity):
    """Binary sensor for filter cycle."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, spa: SpaClient, entry_id: str) -> None:
        """Initialize filter cycle sensor."""
        super().__init__(spa, entry_id, "filtering")
        self._attr_name = "Filtering"
        self._attr_icon = "mdi:filter"

    @property
    def is_on(self) -> bool:
        """Return true if filter cycle is active."""
        return self._spa.status.filter_mode > 0

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        filter_mode = self._spa.status.filter_mode
        mode_names = {0: "Off", 1: "Cycle 1", 2: "Cycle 2", 3: "Cycle 1 & 2"}
        return {
            "filter_mode": mode_names.get(filter_mode, f"Unknown ({filter_mode})")
        }
