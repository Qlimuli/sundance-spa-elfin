"""Light platform for Sundance Spa."""
from __future__ import annotations

import logging

from pybalboa import SpaClient
from pybalboa.control import SpaControl
from pybalboa.enums import OffLowMediumHighState, OffOnState

from homeassistant.components.light import ColorMode, LightEntity
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
    """Set up light entities."""
    spa = entry.runtime_data
    entities: list[SundanceLight] = []

    for control in spa.lights:
        entities.append(SundanceLight(spa, control))

    async_add_entities(entities)


class SundanceLight(SundanceEntity, LightEntity):
    """Light entity for spa lights."""

    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}

    def __init__(self, spa: SpaClient, control: SpaControl) -> None:
        """Initialize light entity."""
        super().__init__(spa, f"light_{control.control_type.name.lower()}")
        self._control = control
        self._attr_name = control.name

    @property
    def is_on(self) -> bool:
        """Return true if light is on."""
        if self._control.options == list(OffOnState):
            return self._control.state == OffOnState.ON
        # For dimmable lights
        return self._control.state != OffLowMediumHighState.OFF

    async def async_turn_on(self, **kwargs) -> None:
        """Turn light on."""
        if self._control.options == list(OffOnState):
            await self._control.set_state(OffOnState.ON)
        else:
            await self._control.set_state(OffLowMediumHighState.HIGH)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn light off."""
        if self._control.options == list(OffOnState):
            await self._control.set_state(OffOnState.OFF)
        else:
            await self._control.set_state(OffLowMediumHighState.OFF)
