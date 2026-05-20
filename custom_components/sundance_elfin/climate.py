"""Climate platform for Sundance Spa."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SundanceConfigEntry
from .entity import SundanceEntity
from .spa_client import SpaClient, HeatMode, HeatState

_LOGGER = logging.getLogger(__name__)

PRESET_READY = "Ready"
PRESET_REST = "Rest"
PRESET_READY_IN_REST = "Ready in Rest"

HEAT_MODE_MAP = {
    HeatMode.READY: PRESET_READY,
    HeatMode.REST: PRESET_REST,
    HeatMode.READY_IN_REST: PRESET_READY_IN_REST,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SundanceConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up climate entity."""
    async_add_entities([SundanceClimate(entry.runtime_data, entry.entry_id)])


class SundanceClimate(SundanceEntity, ClimateEntity):
    """Sundance Spa climate entity."""

    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
    )
    _attr_preset_modes = [PRESET_READY, PRESET_REST, PRESET_READY_IN_REST]
    _attr_translation_key = "spa"

    def __init__(self, spa: SpaClient, entry_id: str) -> None:
        """Initialize climate entity."""
        super().__init__(spa, entry_id, "climate")
        self._attr_name = None  # Use device name

    @property
    def temperature_unit(self) -> str:
        """Return the unit of measurement."""
        if self._spa.temperature_unit_celsius:
            return UnitOfTemperature.CELSIUS
        return UnitOfTemperature.FAHRENHEIT

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        return self._spa.temperature

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        return self._spa.target_temperature

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        return self._spa.temperature_minimum

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        return self._spa.temperature_maximum

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC mode."""
        if self._spa.heat_mode == HeatMode.REST:
            return HVACMode.OFF
        return HVACMode.HEAT

    @property
    def hvac_action(self) -> HVACAction:
        """Return current HVAC action."""
        if self._spa.heat_state == HeatState.HEATING:
            return HVACAction.HEATING
        if self._spa.heat_state == HeatState.HEAT_WAITING:
            return HVACAction.IDLE
        return HVACAction.OFF

    @property
    def preset_mode(self) -> str | None:
        """Return current preset mode."""
        return HEAT_MODE_MAP.get(self._spa.heat_mode)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        await self._spa.set_temperature(temperature)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode."""
        if hvac_mode == HVACMode.OFF:
            await self._spa.set_heat_mode(HeatMode.REST)
        else:
            await self._spa.set_heat_mode(HeatMode.READY)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set preset mode."""
        for mode, name in HEAT_MODE_MAP.items():
            if name == preset_mode:
                await self._spa.set_heat_mode(mode)
                return
