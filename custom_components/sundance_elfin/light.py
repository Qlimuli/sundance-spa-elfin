"""Light platform for Sundance Spa."""
from __future__ import annotations

import logging

from homeassistant.components.light import ColorMode, LightEntity
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
    """Set up light entities."""
    spa = entry.runtime_data
    entities: list[LightEntity] = []

    # Add lights based on configuration
    light_count = spa.config.light_count
    
    if light_count >= 1:
        entities.append(SundanceLight(spa, entry.entry_id, 1))
    if light_count >= 2:
        entities.append(SundanceLight(spa, entry.entry_id, 2))
    
    # Always add at least one light for Sundance spas
    if not entities:
        entities.append(SundanceLight(spa, entry.entry_id, 1))

    async_add_entities(entities)


class SundanceLight(SundanceEntity, LightEntity):
    """Light entity for spa lights."""

    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}

    def __init__(self, spa: SpaClient, entry_id: str, light_num: int) -> None:
        """Initialize light entity."""
        super().__init__(spa, entry_id, f"light_{light_num}")
        self._light_num = light_num
        self._attr_name = f"Light {light_num}" if light_num > 1 else "Light"

    @property
    def is_on(self) -> bool:
        """Return true if light is on."""
        if self._light_num == 1:
            return self._spa.status.light1
        return self._spa.status.light2

    async def async_turn_on(self, **kwargs) -> None:
        """Turn light on."""
        if not self.is_on:
            await self._spa.toggle_light(self._light_num)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn light off."""
        if self.is_on:
            await self._spa.toggle_light(self._light_num)
