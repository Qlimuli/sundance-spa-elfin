"""Sundance Spa – Light Entity (RGB-Licht mit Farbmodi)."""
from __future__ import annotations

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN, SpaCoordinator, BTN_LIGHT, BTN_ZIRK, LIGHT_MODE_MAP


# Alle Farb-Effekte die das Spa kennt (aus LIGHT_MODE_MAP)
EFFECT_LIST = [v for v in LIGHT_MODE_MAP.values() if v != "Off"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SpaLight(data["coordinator"], entry)])


class SpaLight(CoordinatorEntity, LightEntity):
    """RGB-Licht-Entität für den Whirlpool."""

    _attr_has_entity_name  = True
    _attr_name             = "Licht"
    _attr_color_mode       = ColorMode.HS
    _attr_supported_color_modes = {ColorMode.HS}
    _attr_supported_features    = LightEntityFeature.EFFECT
    _attr_effect_list      = EFFECT_LIST

    def __init__(self, coordinator: SpaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id  = f"{entry.entry_id}_light"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Sundance Spa",
            manufacturer="Sundance / Balboa",
            model="RS485-TCP",
        )

    @property
    def _ldata(self) -> dict | None:
        return self.coordinator.data.get("lights") if self.coordinator.data else None

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    # ── Zustand ──────────────────────────────────────────────────

    @property
    def is_on(self) -> bool:
        return self._ldata["on"] if self._ldata else False

    @property
    def brightness(self) -> int | None:
        """HA erwartet 0-255."""
        if self._ldata:
            return int(self._ldata["brightness_raw"])
        return None

    @property
    def hs_color(self) -> tuple[float, float] | None:
        return self._ldata["hs_color"] if self._ldata else None

    @property
    def effect(self) -> str | None:
        if self._ldata:
            mode = self._ldata["mode"]
            return mode if mode in EFFECT_LIST else None
        return None

    @property
    def extra_state_attributes(self) -> dict:
        if not self._ldata:
            return {}
        return {
            "mode":      self._ldata["mode"],
            "mode_raw":  self._ldata["mode_raw"],
            "rgb_r":     self._ldata["r"],
            "rgb_g":     self._ldata["g"],
            "rgb_b":     self._ldata["b"],
        }

    # ── Aktionen ─────────────────────────────────────────────────

    async def async_turn_on(self, **kwargs) -> None:
        client = self.coordinator.client

        # 1) Licht einschalten wenn es aus ist
        if not self.is_on:
            await client.send_button(BTN_LIGHT)
            await client.wait_lights(n=3, timeout=5.0)

        # 2) Effekt wechseln (BTN_ZIRK = Farb-Wechsel-Button)
        if ATTR_EFFECT in kwargs:
            desired = kwargs[ATTR_EFFECT]
            # Solange weiterschalten bis Effekt passt (max. 15 Versuche)
            for _ in range(15):
                if self._ldata and self._ldata["mode"] == desired:
                    break
                await client.send_button(BTN_ZIRK)
                await client.wait_lights(n=3, timeout=3.0)

        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        if not self.is_on:
            return
        await self.coordinator.client.send_button(BTN_LIGHT)
        await self.coordinator.client.wait_lights(n=3, timeout=5.0)
        await self.coordinator.async_request_refresh()