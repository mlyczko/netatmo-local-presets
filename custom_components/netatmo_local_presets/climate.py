"""Climate platform for Netatmo Local Presets.

Wraps an existing, already-local climate entity (typically the entity
created by Home Assistant's built-in ``homekit_controller`` integration
after pairing your Netatmo Smart Thermostat/Valve as a "HomeKit Device")
and adds Away / Frost Guard / Boost / Schedule presets on top of it.

Every action this entity performs is a normal Home Assistant
``climate.set_temperature`` / ``climate.set_hvac_mode`` service call against
the wrapped entity, so it never talks to Netatmo's servers and needs no
Netatmo account or credentials.

All temperatures, the boost duration and the day/night window are read
live from a shared `PresetStore`, which the companion `number`/`time`
entities (on the same device) read and write - see Settings -> Devices &
Services for that device to adjust them.

Important limitation: Netatmo's actual weekly heating *schedule* is
calculated on the relay/cloud side and is not exposed locally at all. The
"Schedule" preset here (hvac mode "auto") instead follows a simple
day/night temperature split that this integration manages itself.
"""

from __future__ import annotations

from datetime import time, timedelta

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import CONF_SOURCE, DOMAIN
from .store import PresetStore

PRESET_SCHEDULE = "schedule"
PRESET_AWAY = "away"
PRESET_FROST_GUARD = "frost_guard"
PRESET_BOOST = "boost"

PRESET_STORE_FIELD = {
    PRESET_AWAY: "away_temperature",
    PRESET_FROST_GUARD: "frost_guard_temperature",
    PRESET_BOOST: "boost_temperature",
}

ATTR_BOOST_END = "boost_end"

AUTO_CHECK_INTERVAL = timedelta(minutes=1)


def _time_in_window(now: time, start: time, end: time) -> bool:
    """Return whether `now` falls within [start, end), wrapping past midnight."""
    if start == end:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Netatmo Local Presets climate entity from a config entry."""
    store: PresetStore = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            NetatmoLocalPresetClimate(
                entry=entry,
                store=store,
                source_entity_id=entry.data[CONF_SOURCE],
                name=entry.title,
            )
        ]
    )


class NetatmoLocalPresetClimate(ClimateEntity, RestoreEntity):
    """A climate entity that adds Netatmo-style presets to a local thermostat."""

    _attr_should_poll = False
    _attr_temperature_unit = "°C"
    _attr_translation_key = "netatmo_local_presets"
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.AUTO, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
    )
    _attr_preset_modes = [
        PRESET_SCHEDULE,
        PRESET_AWAY,
        PRESET_FROST_GUARD,
        PRESET_BOOST,
    ]

    def __init__(
        self,
        entry: ConfigEntry,
        store: PresetStore,
        source_entity_id: str,
        name: str,
    ) -> None:
        self._store = store
        self._source_entity_id = source_entity_id
        self._attr_name = name
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=name,
            manufacturer="Netatmo Local Presets",
            model="Local preset wrapper",
        )

        self._attr_preset_mode = PRESET_SCHEDULE
        self._attr_current_temperature: float | None = None
        self._attr_target_temperature: float | None = None
        self._attr_hvac_mode = HVACMode.OFF

        self._preset_before_boost = PRESET_SCHEDULE
        self._cancel_boost_timer = None
        self._boost_end = None
        self._cancel_auto_listener = None

    @property
    def extra_state_attributes(self) -> dict:
        """Expose the boost end time so it survives a restart."""
        return {ATTR_BOOST_END: self._boost_end.isoformat() if self._boost_end else None}

    async def async_added_to_hass(self) -> None:
        """Restore preset state and start tracking the source entity."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.attributes.get("preset_mode"):
            self._attr_preset_mode = last_state.attributes["preset_mode"]

        self._sync_from_source(self.hass.states.get(self._source_entity_id))

        if self._attr_preset_mode == PRESET_BOOST and last_state is not None:
            boost_end_raw = last_state.attributes.get(ATTR_BOOST_END)
            boost_end = dt_util.parse_datetime(boost_end_raw) if boost_end_raw else None
            if boost_end is None or boost_end <= dt_util.utcnow():
                self._attr_preset_mode = PRESET_SCHEDULE
            else:
                self._boost_end = boost_end
                self._cancel_boost_timer = async_call_later(
                    self.hass,
                    (boost_end - dt_util.utcnow()).total_seconds(),
                    self._async_boost_finished,
                )

        if self._attr_preset_mode == PRESET_SCHEDULE:
            self._attr_hvac_mode = HVACMode.AUTO
            self._start_auto_scheduler()
            await self._async_apply_auto_temperature()

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source_entity_id], self._handle_source_change
            )
        )
        self.async_on_remove(self._store.add_listener(self._handle_store_change))
        self.async_on_remove(self._stop_auto_scheduler)

    @callback
    def _handle_source_change(self, event: Event[EventStateChangedData]) -> None:
        self._sync_from_source(event.data["new_state"])
        self.async_write_ha_state()

    @callback
    def _handle_store_change(self) -> None:
        """React to a number/time entity (temperature/night window) changing."""
        self.hass.async_create_task(self._async_reapply_current_mode())

    @callback
    def _sync_from_source(self, state) -> None:
        """Mirror current temperature and hvac mode from the source entity."""
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        if state.state == "off":
            self._attr_hvac_mode = HVACMode.OFF
            self._stop_auto_scheduler()
        elif self._attr_hvac_mode != HVACMode.AUTO:
            # Only mirror heat/off from the source when we're not actively
            # running our own day/night schedule on top of it.
            self._attr_hvac_mode = HVACMode.HEAT if state.state == "heat" else HVACMode.OFF

        self._attr_current_temperature = state.attributes.get("current_temperature")

        target = state.attributes.get(ATTR_TEMPERATURE)
        if target is not None:
            self._attr_target_temperature = target

    async def async_set_temperature(self, **kwargs) -> None:
        """Forward a manual temperature change to the source entity."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        await self._async_call_source_set_temperature(temperature)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Handle the user toggling Off / Heat / Auto directly."""
        if hvac_mode == HVACMode.AUTO:
            await self.async_set_preset_mode(PRESET_SCHEDULE)
            return

        self._stop_auto_scheduler()
        await self._async_forward_hvac_mode(hvac_mode)
        self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Apply a Netatmo-style preset using only local climate calls."""
        if preset_mode not in self._attr_preset_modes:
            raise ValueError(f"Unsupported preset: {preset_mode}")

        self._cancel_pending_boost()

        if preset_mode == PRESET_BOOST:
            self._preset_before_boost = (
                self._attr_preset_mode
                if self._attr_preset_mode != PRESET_BOOST
                else PRESET_SCHEDULE
            )
            boost_duration = timedelta(minutes=self._store.boost_duration_minutes)
            self._boost_end = dt_util.utcnow() + boost_duration
            self._cancel_boost_timer = async_call_later(
                self.hass, boost_duration, self._async_boost_finished
            )
        else:
            self._boost_end = None

        self._attr_preset_mode = preset_mode

        if preset_mode == PRESET_SCHEDULE:
            await self._async_forward_hvac_mode(HVACMode.AUTO)
            self._start_auto_scheduler()
            await self._async_apply_auto_temperature()
        else:
            self._stop_auto_scheduler()
            await self._async_forward_hvac_mode(HVACMode.HEAT)
            target_temperature = self._preset_temperature_value(preset_mode)
            if target_temperature is not None:
                await self._async_call_source_set_temperature(target_temperature)

        self.async_write_ha_state()

    async def _async_boost_finished(self, _now) -> None:
        """Revert to the previous preset once the boost duration elapses."""
        self._cancel_boost_timer = None
        await self.async_set_preset_mode(self._preset_before_boost)

    def _cancel_pending_boost(self) -> None:
        if self._cancel_boost_timer is not None:
            self._cancel_boost_timer()
            self._cancel_boost_timer = None

    def _start_auto_scheduler(self) -> None:
        """Begin periodically re-evaluating the day/night setpoint."""
        if self._cancel_auto_listener is None:
            self._cancel_auto_listener = async_track_time_interval(
                self.hass, self._async_auto_tick, AUTO_CHECK_INTERVAL
            )

    def _stop_auto_scheduler(self) -> None:
        if self._cancel_auto_listener is not None:
            self._cancel_auto_listener()
            self._cancel_auto_listener = None

    async def _async_auto_tick(self, _now) -> None:
        await self._async_apply_auto_temperature()

    async def _async_reapply_current_mode(self) -> None:
        """Re-push the current preset/auto temperature after a store change."""
        if self._attr_hvac_mode == HVACMode.AUTO:
            await self._async_apply_auto_temperature()
            return

        target_temperature = self._preset_temperature_value(self._attr_preset_mode)
        if target_temperature is not None and target_temperature != self._attr_target_temperature:
            await self._async_call_source_set_temperature(target_temperature)

    async def _async_apply_auto_temperature(self) -> None:
        """Push the day or night setpoint, whichever currently applies."""
        now = dt_util.as_local(dt_util.utcnow()).time()
        is_night = _time_in_window(now, self._store.night_start, self._store.night_end)
        temperature = self._store.night_temperature if is_night else self._store.day_temperature
        if temperature != self._attr_target_temperature:
            await self._async_call_source_set_temperature(temperature)

    def _preset_temperature_value(self, preset_mode: str) -> float | None:
        field = PRESET_STORE_FIELD.get(preset_mode)
        if field is None:
            return None
        return getattr(self._store, field)

    async def _async_forward_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Forward on/off to the source entity (it has no concept of "auto")."""
        source_mode = HVACMode.OFF if hvac_mode == HVACMode.OFF else HVACMode.HEAT
        await self.hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": self._source_entity_id, "hvac_mode": source_mode},
            blocking=True,
        )
        self._attr_hvac_mode = hvac_mode

    async def _async_call_source_set_temperature(self, temperature: float) -> None:
        await self.hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": self._source_entity_id, ATTR_TEMPERATURE: temperature},
            blocking=True,
        )
