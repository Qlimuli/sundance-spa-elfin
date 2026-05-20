"""Switch platform for Sundance Spa (pumps, blower, etc.)."""
from __future__ import annotations

import logging

from pybalboa import SpaClient
from pybalboa.control import SpaControl
from pybalboa.enums import OffOnState

from homeassistant.components.switch import SwitchEntity
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
    """Set up switch entities for pumps and blower."""
    spa = entry.runtime_data
    entities: list[SundancePumpSwitch] = []

    for control in spa.controls:
        if control.options == list(OffOnState):
            entities.append(SundancePumpSwitch(spa, control))

    async_add_entities(entities)


class SundancePumpSwitch(SundanceEntity, SwitchEntity):
    """Switch entity for spa pumps/blower."""

    def __init__(self, spa: SpaClient, control: SpaControl) -> None:
        """Initialize pump switch."""
        super().__init__(spa, f"switch_{control.control_type.name.lower()}")
        self._control = control
        self._attr_name = control.name
        self._attr_icon = "mdi:pump"

    @property
    def is_on(self) -> bool:
        """Return true if pump is on."""
        return self._control.state == OffOnState.ON

    async def async_turn_on(self, **kwargs) -> None:
        """Turn pump on."""
        await self._control.set_state(OffOnState.ON)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn pump off."""
        await self._control.set_state(OffOnState.OFF)
