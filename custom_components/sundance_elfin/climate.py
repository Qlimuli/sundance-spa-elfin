"""Climate platform for Sundance Spa Elfin integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import SundanceElfinClient
from .const import (
    CLIMATE_UNIQUE_ID,
    DEFAULT_NAME,
    DOMAIN,
    MAX_TEMP,
    MIN_TEMP,
    TEMP_STEP,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Sundance Spa climate entity."""
    client: SundanceElfinClient = hass.data[DOMAIN][entry.entry_id]
    
    async_add_entities([SundanceSpaClimate(client, entry)])


class SundanceSpaClimate(ClimateEntity):
    """Climate entity for controlling spa water temperature."""

    _attr_has_entity_name = True
    _attr_name = "Water Temperature"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_target_temperature_step = TEMP_STEP

    def __init__(self, client: SundanceElfinClient, entry: ConfigEntry) -> None:
        """Initialize the climate entity."""
        self._client = client
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{CLIMATE_UNIQUE_ID}"
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
    def current_temperature(self) -> float | None:
        """Return the current water temperature."""
        return self._client.state.current_temp

    @property
    def target_temperature(self) -> float | None:
        """Return the target water temperature."""
        return self._client.state.target_temp

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current HVAC mode."""
        # Spa is always in heat mode when on
        return HVACMode.HEAT if self._client.state.connected else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the current HVAC action."""
        if not self._client.state.connected:
            return HVACAction.OFF
        return HVACAction.HEATING if self._client.state.is_heating else HVACAction.IDLE

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._client.state.connected

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            await self._client.set_target_temperature(temperature)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode.
        
        Note: Most spas don't support turning off heating entirely via RS485.
        This is here for Home Assistant compatibility but may not have an effect.
        """
        _LOGGER.debug("Set HVAC mode called with %s (may not be supported)", hvac_mode)

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
