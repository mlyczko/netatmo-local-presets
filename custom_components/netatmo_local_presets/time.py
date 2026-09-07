"""Time platform for Netatmo Local Presets.

Exposes the night-window boundaries as `time` entities on the device, so
the auto/schedule day-night split can be adjusted from Settings -> Devices
& Services without touching YAML.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time as dt_time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .store import PresetStore


@dataclass(frozen=True)
class TimeDescriptor:
    """Describes one adjustable time backed by a `PresetStore` field."""

    key: str
    name: str
    icon: str


NIGHT_WINDOW_TIMES = [
    TimeDescriptor("night_start", "Night start", "mdi:weather-night"),
    TimeDescriptor("night_end", "Night end", "mdi:weather-sunny"),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the night-window time entities."""
    store: PresetStore = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        PresetTime(entry, store, descriptor) for descriptor in NIGHT_WINDOW_TIMES
    )


class PresetTime(TimeEntity, RestoreEntity):
    """A time entity backed by a field on the shared `PresetStore`."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, store: PresetStore, descriptor: TimeDescriptor) -> None:
        self._store = store
        self._key = descriptor.key
        self._attr_name = descriptor.name
        self._attr_icon = descriptor.icon
        self._attr_unique_id = f"{entry.entry_id}_{descriptor.key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    async def async_added_to_hass(self) -> None:
        """Restore the last known value into the shared store."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            parsed = dt_util.parse_time(last_state.state)
            if parsed is not None:
                setattr(self._store, self._key, parsed)

    @property
    def native_value(self) -> dt_time:
        return getattr(self._store, self._key)

    async def async_set_value(self, value: dt_time) -> None:
        setattr(self._store, self._key, value)
        self._store.notify()
        self.async_write_ha_state()
