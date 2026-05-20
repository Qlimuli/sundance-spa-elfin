"""Switch platform for Sundance Spa (pumps, blower, etc.)."""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SundanceConfigEntry
from .entity import SundanceEntity
from .spa_client import SpaClient, PumpState

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SundanceConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch entities for pumps and blower."""
    spa = entry.runtime_data
    entities: list[SwitchEntity] = []

    # Add pumps based on configuration
    config = spa.config
    
    if config.pump1_speeds > 0:
        entities.append(SundancePumpSwitch(spa, entry.entry_id, 1))
    if config.pump2_speeds > 0:
        entities.append(SundancePumpSwitch(spa, entry.entry_id, 2))
    if config.pump3_speeds > 0:
        entities.append(SundancePumpSwitch(spa, entry.entry_id, 3))
    
    # Add blower if available
    if config.has_blower:
        entities.append(SundanceBlowerSwitch(spa, entry.entry_id))

    # If no pumps detected in config, add defaults for Sundance Cameo 880
    if not entities:
        _LOGGER.info("No pump config detected, adding default pumps (1, 2)")
        entities.append(SundancePumpSwitch(spa, entry.entry_id, 1))
        entities.append(SundancePumpSwitch(spa, entry.entry_id, 2))

    async_add_entities(entities)


class SundancePumpSwitch(SundanceEntity, SwitchEntity):
    """Switch entity for spa pumps."""

    def __init__(self, spa: SpaClient, entry_id: str, pump_num: int) -> None:
        """Initialize pump switch."""
        super().__init__(spa, entry_id, f"pump_{pump_num}")
        self._pump_num = pump_num
        self._attr_name = f"Pump {pump_num}"
        self._attr_icon = "mdi:pump"

    @property
    def is_on(self) -> bool:
        """Return true if pump is on."""
        status = self._spa.status
        pump_state = getattr(status, f"pump{self._pump_num}", PumpState.OFF)
        return pump_state != PumpState.OFF

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        status = self._spa.status
        pump_state = getattr(status, f"pump{self._pump_num}", PumpState.OFF)
        return {
            "speed": pump_state.name if pump_state else "OFF"
        }

    async def async_turn_on(self, **kwargs) -> None:
        """Turn pump on."""
        # Toggle pump (will cycle through speeds)
        await self._spa.toggle_pump(self._pump_num)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn pump off."""
        # Toggle until off (pumps cycle: off -> low -> high -> off)
        status = self._spa.status
        pump_state = getattr(status, f"pump{self._pump_num}", PumpState.OFF)
        
        # Keep toggling until off
        max_toggles = 3
        for _ in range(max_toggles):
            if pump_state == PumpState.OFF:
                break
            await self._spa.toggle_pump(self._pump_num)
            # Wait for state update
            import asyncio
            await asyncio.sleep(0.5)
            pump_state = getattr(self._spa.status, f"pump{self._pump_num}", PumpState.OFF)


class SundanceBlowerSwitch(SundanceEntity, SwitchEntity):
    """Switch entity for spa blower."""

    def __init__(self, spa: SpaClient, entry_id: str) -> None:
        """Initialize blower switch."""
        super().__init__(spa, entry_id, "blower")
        self._attr_name = "Blower"
        self._attr_icon = "mdi:fan"

    @property
    def is_on(self) -> bool:
        """Return true if blower is on."""
        return self._spa.status.blower > 0

    async def async_turn_on(self, **kwargs) -> None:
        """Turn blower on."""
        if not self.is_on:
            await self._spa.toggle_blower()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn blower off."""
        if self.is_on:
            await self._spa.toggle_blower()
