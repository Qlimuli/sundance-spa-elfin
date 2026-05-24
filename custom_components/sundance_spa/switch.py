"""Sundance Spa – Switch Entities (Pumpen, Zirk, ClearRay, Blower)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import (
    DOMAIN,
    SpaCoordinator,
    BTN_PUMP1,
    BTN_PUMP2,
    BTN_ZIRK,
    BTN_CLEARRAY,
    BTN_BLOWER,
)


@dataclass(frozen=True)
class SpaSwitch:
    key:      str
    name:     str
    icon_on:  str
    icon_off: str
    button:   int
    getter:   Callable[[dict], bool]


SWITCH_TYPES: list[SpaSwitch] = [
    SpaSwitch(
        key="pump1", name="Pumpe 1",
        icon_on="mdi:pump", icon_off="mdi:pump-off",
        button=BTN_PUMP1,
        getter=lambda s: s["pump1"],
    ),
    SpaSwitch(
        key="pump2", name="Pumpe 2",
        icon_on="mdi:pump", icon_off="mdi:pump-off",
        button=BTN_PUMP2,
        getter=lambda s: s["pump2"],
    ),
    SpaSwitch(
        key="circ", name="Auto-Zirkulation",
        icon_on="mdi:rotate-right", icon_off="mdi:rotate-right",
        button=BTN_ZIRK,
        getter=lambda s: s["circ"],
    ),
    SpaSwitch(
        key="clearray", name="Wasserfall-manuell",
        icon_on="mdi:uv-fast", icon_off="mdi:uv-fast",
        button=BTN_CLEARRAY,
        getter=lambda s: s["circ_manual"],
    ),
    SpaSwitch(
        key="blower", name="Blubber / Luftsprudel",
        icon_on="mdi:weather-windy",
        icon_off="mdi:weather-windy-variant",
        button=BTN_BLOWER,
        getter=lambda s: s.get("blower", False),
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SpaSwitch_(data["coordinator"], entry, sw)
        for sw in SWITCH_TYPES
    )


class SpaSwitch_(CoordinatorEntity, SwitchEntity):
    """Ein Toggle-Switch für eine Spa-Funktion."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SpaCoordinator,
        entry: ConfigEntry,
        sw: SpaSwitch,
    ) -> None:
        super().__init__(coordinator)
        self._sw = sw
        self._attr_unique_id   = f"{entry.entry_id}_{sw.key}"
        self._attr_name        = sw.name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Sundance Spa",
            manufacturer="Sundance / Balboa",
            model="RS485-TCP",
        )

    @property
    def _status(self) -> dict | None:
        return self.coordinator.data.get("status") if self.coordinator.data else None

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool | None:
        if self._status is None:
            return None
        return self._sw.getter(self._status)

    @property
    def icon(self) -> str:
        return self._sw.icon_on if self.is_on else self._sw.icon_off

    @property
    def extra_state_attributes(self) -> dict:
        if not self._status:
            return {}
        if self._sw.key == "circ":
            return {
                "circ_running": self._status["circ_running"],
                "circ_manual":  self._status["circ_manual"],
            }
        if self._sw.key == "blower":
            raw = self._status.get("raw", [])
            return {
                "blower_raw_field13": raw[13] if len(raw) > 13 else None,
            }
        return {}

    async def async_turn_on(self, **kwargs) -> None:
        if self.is_on:
            return
        await self.coordinator.client.send_button(self._sw.button)
        await self.coordinator.client.wait_status(n=6, timeout=4.0)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        if not self.is_on:
            return
        await self.coordinator.client.send_button(self._sw.button)
        await self.coordinator.client.wait_status(n=6, timeout=4.0)
        await self.coordinator.async_request_refresh()
