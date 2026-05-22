"""Sundance Spa – Sensor Entities."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN, SpaCoordinator


@dataclass(frozen=True)
class SpaSensorDescription:
    key:         str
    name:        str
    icon:        str
    getter:      Callable[[dict, dict | None], Any]
    unit:        str | None       = None
    device_class: str | None      = None
    state_class:  str | None      = None
    category:     EntityCategory | None = None


SENSOR_TYPES: list[SpaSensorDescription] = [
    SpaSensorDescription(
        key="cur_temp", name="Ist-Temperatur",
        icon="mdi:thermometer-water",
        getter=lambda s, _l: s["cur_temp"],
        unit=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SpaSensorDescription(
        key="set_temp", name="Soll-Temperatur",
        icon="mdi:thermometer-chevron-up",
        getter=lambda s, _l: s["set_temp"],
        unit=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SpaSensorDescription(
        key="heat_mode", name="Heizmodus",
        icon="mdi:water-boiler",
        getter=lambda s, _l: s["heat_mode"],
    ),
    SpaSensorDescription(
        key="spa_time", name="Spa-Uhrzeit",
        icon="mdi:clock-outline",
        getter=lambda s, _l: s["time"],
        category=EntityCategory.DIAGNOSTIC,
    ),
    SpaSensorDescription(
        key="display", name="Display-Status",
        icon="mdi:monitor",
        getter=lambda s, _l: "Menü aktiv" if s["in_menu"] else "Normal",
        category=EntityCategory.DIAGNOSTIC,
    ),
    SpaSensorDescription(
        key="light_mode", name="Licht-Modus",
        icon="mdi:lightbulb-variant-outline",
        getter=lambda _s, l: l["mode"] if l else "Unbekannt",
    ),
    SpaSensorDescription(
        key="light_color", name="Licht-Farbe (HEX)",
        icon="mdi:palette",
        getter=lambda _s, l: (
            f"#{l['r']:02X}{l['g']:02X}{l['b']:02X}" if l else None
        ),
        category=EntityCategory.DIAGNOSTIC,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SpaSensor(data["coordinator"], entry, desc)
        for desc in SENSOR_TYPES
    )


class SpaSensor(CoordinatorEntity, SensorEntity):
    """Ein einzelner Spa-Sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SpaCoordinator,
        entry: ConfigEntry,
        desc: SpaSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self._desc = desc
        self._attr_unique_id    = f"{entry.entry_id}_{desc.key}"
        self._attr_name         = desc.name
        self._attr_icon         = desc.icon
        self._attr_native_unit_of_measurement = desc.unit
        self._attr_device_class = desc.device_class
        self._attr_state_class  = desc.state_class
        self._attr_entity_category = desc.category
        self._attr_device_info  = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Sundance Spa",
            manufacturer="Sundance / Balboa",
            model="RS485-TCP",
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        s = self.coordinator.data.get("status")
        l = self.coordinator.data.get("lights")
        if s is None:
            return None
        try:
            return self._desc.getter(s, l)
        except (KeyError, TypeError):
            return None