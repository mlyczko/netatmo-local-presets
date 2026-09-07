"""Netatmo Local Presets integration.

Adds Away / Frost Guard / Boost / Schedule presets on top of a locally
bridged Netatmo thermostat (e.g. via the homekit_controller integration),
without ever contacting Netatmo's cloud service.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .store import PresetStore

PLATFORMS = ["climate", "number", "time"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Netatmo Local Presets from a config entry."""
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = PresetStore()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded

