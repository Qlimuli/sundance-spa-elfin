"""Sundance Spa – Light Entity (RGB-Licht mit Farbmodi)."""
from __future__ import annotations
import logging

from homeassistant.components.light import (
    ATTR_EFFECT,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, UpdateFailed

from . import DOMAIN, SpaCoordinator, LIGHT_MODE_MAP

_LOGGER = logging.getLogger(__name__)

EFFECT_LIST = [v for k, v in LIGHT_MODE_MAP.items() if v != "Off"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SpaLight(data["coordinator"], entry)])


class SpaLight(CoordinatorEntity, LightEntity):
    """RGB-Licht-Entität für den Whirlpool."""

    _attr_has_entity_name       = True
    _attr_name                  = "Licht"
    _attr_color_mode            = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_supported_features    = LightEntityFeature.EFFECT
    _attr_effect_list           = EFFECT_LIST

    def __init__(self, coordinator: SpaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id   = f"{entry.entry_id}_light"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Sundance Spa",
            manufacturer="Sundance / Balboa",
            model="RS485-TCP",
        )

    @property
    def _ldata(self) -> dict | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("lights")

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        if not self._ldata:
            return False
        return bool(self._ldata.get("on", False))

    @property
    def brightness(self) -> int | None:
        if not self._ldata:
            return None
        raw = self._ldata.get("brightness_raw", 0)
        return min(255, int(raw * 255 / 100))

    @property
    def effect(self) -> str | None:
        if not self._ldata:
            return None
        mode = self._ldata.get("mode")
        return mode if mode in EFFECT_LIST else None

    @property
    def extra_state_attributes(self) -> dict:
        if not self._ldata:
            return {}
        return {
            "mode":           self._ldata.get("mode"),
            "mode_raw":       self._ldata.get("mode_raw"),
            "rgb_r":          self._ldata.get("r"),
            "rgb_g":          self._ldata.get("g"),
            "rgb_b":          self._ldata.get("b"),
            "brightness_pct": self._ldata.get("brightness"),
        }

    async def async_turn_on(self, **kwargs) -> None:
        effect = kwargs.get(ATTR_EFFECT)
        try:
            if effect:
                await self.coordinator.client.set_light(effect=effect)
            else:
                await self.coordinator.client.set_light(on=True)
        except UpdateFailed as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        try:
            await self.coordinator.client.set_light(on=False)
        except UpdateFailed as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_request_refresh()
