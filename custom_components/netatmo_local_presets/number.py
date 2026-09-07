"""Number platform for Netatmo Local Presets.

Exposes every configurable temperature (and the boost duration) as a
`number` entity on the device, so they can be adjusted from Settings ->
Devices & Services without touching YAML.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .store import PresetStore


@dataclass(frozen=True)
class NumberDescriptor:
    """Describes one adjustable number backed by a `PresetStore` field."""

    key: str
    name: str
    icon: str
    native_min_value: float
    native_max_value: float
    native_step: float
    native_unit_of_measurement: str


TEMPERATURE_NUMBERS = [
    NumberDescriptor(
        "away_temperature", "Away temperature", "mdi:bag-suitcase",
        4, 30, 0.5, UnitOfTemperature.CELSIUS,
    ),
    NumberDescriptor(
        "frost_guard_temperature", "Frost guard temperature", "mdi:snowflake",
        4, 15, 0.5, UnitOfTemperature.CELSIUS,
    ),
    NumberDescriptor(
        "boost_temperature", "Boost temperature", "mdi:rocket-launch",
        15, 30, 0.5, UnitOfTemperature.CELSIUS,
    ),
    NumberDescriptor(
        "day_temperature", "Day temperature", "mdi:weather-sunny",
        4, 30, 0.5, UnitOfTemperature.CELSIUS,
    ),
    NumberDescriptor(
        "night_temperature", "Night temperature", "mdi:weather-night",
        4, 30, 0.5, UnitOfTemperature.CELSIUS,
    ),
    NumberDescriptor(
        "boost_duration_minutes", "Boost duration", "mdi:timer-outline",
        5, 180, 5, UnitOfTime.MINUTES,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the adjustable-temperature number entities."""
    store: PresetStore = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        PresetNumber(entry, store, descriptor) for descriptor in TEMPERATURE_NUMBERS
    )


class PresetNumber(NumberEntity, RestoreEntity):
    """A number entity backed by a field on the shared `PresetStore`."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX

    def __init__(self, entry: ConfigEntry, store: PresetStore, descriptor: NumberDescriptor) -> None:
        self._store = store
        self._key = descriptor.key
        self._attr_name = descriptor.name
        self._attr_icon = descriptor.icon
        self._attr_native_min_value = descriptor.native_min_value
        self._attr_native_max_value = descriptor.native_max_value
        self._attr_native_step = descriptor.native_step
        self._attr_native_unit_of_measurement = descriptor.native_unit_of_measurement
        self._attr_unique_id = f"{entry.entry_id}_{descriptor.key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    async def async_added_to_hass(self) -> None:
        """Restore the last known value into the shared store."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            try:
                setattr(self._store, self._key, float(last_state.state))
            except ValueError:
                pass

    @property
    def native_value(self) -> float:
        return getattr(self._store, self._key)

    async def async_set_native_value(self, value: float) -> None:
        setattr(self._store, self._key, value)
        self._store.notify()
        self.async_write_ha_state()
