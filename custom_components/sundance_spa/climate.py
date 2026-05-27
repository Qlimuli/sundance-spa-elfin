"""Sundance Spa – Climate Entity (Thermostat-Karte in HA)."""
from __future__ import annotations

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN, SpaCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SpaClimate(data["coordinator"], entry)])


class SpaClimate(CoordinatorEntity, ClimateEntity):
    """Thermostat-Entität für den Whirlpool (Sundance Cameo 880)."""

    _attr_has_entity_name         = True
    _attr_name                    = "Thermostat"
    _attr_temperature_unit        = UnitOfTemperature.CELSIUS
    # Sundance Cameo 880 Hardware-Limits: 80 °F (26,7 °C) – 104 °F (40 °C)
    _attr_min_temp                = 26.5
    _attr_max_temp                = 40.0
    _attr_target_temperature_step = 0.5
    _attr_hvac_modes              = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features      = ClimateEntityFeature.TARGET_TEMPERATURE

    def __init__(self, coordinator: SpaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry   = entry
        self._attr_unique_id = f"{entry.entry_id}_climate"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Sundance Spa",
            manufacturer="Sundance / Balboa",
            model="Cameo 880 (RS485-TCP)",
        )

    @property
    def _status(self) -> dict | None:
        return self.coordinator.data.get("status") if self.coordinator.data else None

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    # ── HA-Properties ────────────────────────────────────────────

    @property
    def current_temperature(self) -> float | None:
        return self._status["cur_temp"] if self._status else None

    @property
    def target_temperature(self) -> float | None:
        return self._status["set_temp"] if self._status else None

    @property
    def target_temperature_step(self) -> float:
        # Bei °F-Mode des Spas effektiv 0,5 °C – die echte Quantisierung
        # erfolgt im Treiber via _celsius_to_raw().
        if self._status and not self._status.get("is_celsius", False):
            return 0.5
        return 0.5

    @property
    def hvac_mode(self) -> HVACMode:
        if self._status and self._status["heat_active"]:
            return HVACMode.HEAT
        return HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        if self._status and self._status["heat_active"]:
            return HVACAction.HEATING
        return HVACAction.IDLE

    @property
    def preset_mode(self) -> str | None:
        return self._status["heat_mode"] if self._status else None

    @property
    def extra_state_attributes(self) -> dict:
        if not self._status:
            return {}
        return {
            "heat_mode":         self._status["heat_mode"],
            "in_menu":           self._status["in_menu"],
            "display":           self._status.get("display"),
            "display_val":       self._status["display_val"],
            "spa_time":          self._status["time"],
            "raw_d8":            self._status["raw_d8"],
            "is_celsius_mode":   self._status.get("is_celsius", False),
            "assigned_channel": (
                f"0x{self.coordinator.client.assigned_channel:02X}"
                if self.coordinator.client.assigned_channel is not None
                else None
            ),
        }

    # ── HA-Aktionen ──────────────────────────────────────────────

    async def async_set_temperature(self, **kwargs) -> None:
        temp = kwargs.get("temperature")
        if temp is None:
            return
        try:
            await self.coordinator.client.set_temperature(float(temp))
        except UpdateFailed as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        # Heizmodus wird vom Spa selbst gesteuert – kein direkter Button.
        pass
