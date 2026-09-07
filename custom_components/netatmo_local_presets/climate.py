"""Climate platform for Netatmo Local Presets.

Wraps an existing, already-local climate entity (typically the entity
created by Home Assistant's built-in ``homekit_controller`` integration
after pairing your Netatmo Smart Thermostat/Valve as a "HomeKit Device")
and adds Away / Frost Guard / Boost / Schedule presets on top of it.

Every action this entity performs is a normal Home Assistant
``climate.set_temperature`` / ``climate.set_hvac_mode`` service call against
the wrapped entity, so it never talks to Netatmo's servers and needs no
Netatmo account or credentials.

Important limitation: Netatmo's actual weekly heating *schedule* is
calculated on the relay/cloud side and is not exposed locally at all. The
"Schedule" preset here (hvac mode "auto") instead follows a simple
day/night temperature split that this integration manages itself.
"""

from __future__ import annotations

from datetime import time, timedelta
import logging

import voluptuous as vol

from homeassistant.util import dt as dt_util

from homeassistant.components.climate import (
    PLATFORM_SCHEMA as CLIMATE_PLATFORM_SCHEMA,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, CONF_NAME, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

_LOGGER = logging.getLogger(__name__)

CONF_SOURCE = "source"
CONF_AWAY_TEMPERATURE = "away_temperature"
CONF_AWAY_TEMPERATURE_ENTITY = "away_temperature_entity"
CONF_FROST_GUARD_TEMPERATURE = "frost_guard_temperature"
CONF_FROST_GUARD_TEMPERATURE_ENTITY = "frost_guard_temperature_entity"
CONF_BOOST_TEMPERATURE = "boost_temperature"
CONF_BOOST_TEMPERATURE_ENTITY = "boost_temperature_entity"
CONF_BOOST_DURATION = "boost_duration"
CONF_DAY_TEMPERATURE = "day_temperature"
CONF_DAY_TEMPERATURE_ENTITY = "day_temperature_entity"
CONF_NIGHT_TEMPERATURE = "night_temperature"
CONF_NIGHT_TEMPERATURE_ENTITY = "night_temperature_entity"
CONF_NIGHT_START = "night_start"
CONF_NIGHT_START_ENTITY = "night_start_entity"
CONF_NIGHT_END = "night_end"
CONF_NIGHT_END_ENTITY = "night_end_entity"

PRESET_SCHEDULE = "schedule"
PRESET_AWAY = "away"
PRESET_FROST_GUARD = "frost_guard"
PRESET_BOOST = "boost"

ATTR_BOOST_END = "boost_end"

DEFAULT_NAME = "Netatmo Local Thermostat"
DEFAULT_AWAY_TEMPERATURE = 12.0
DEFAULT_FROST_GUARD_TEMPERATURE = 7.0
DEFAULT_BOOST_TEMPERATURE = 30.0
DEFAULT_BOOST_DURATION = timedelta(minutes=30)
DEFAULT_DAY_TEMPERATURE = 19.0
DEFAULT_NIGHT_TEMPERATURE = 16.0
DEFAULT_NIGHT_START = time(22, 0)
DEFAULT_NIGHT_END = time(6, 0)

AUTO_CHECK_INTERVAL = timedelta(minutes=1)

PLATFORM_SCHEMA = CLIMATE_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_SOURCE): cv.entity_id,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(
            CONF_AWAY_TEMPERATURE, default=DEFAULT_AWAY_TEMPERATURE
        ): vol.Coerce(float),
        vol.Optional(CONF_AWAY_TEMPERATURE_ENTITY): cv.entity_id,
        vol.Optional(
            CONF_FROST_GUARD_TEMPERATURE, default=DEFAULT_FROST_GUARD_TEMPERATURE
        ): vol.Coerce(float),
        vol.Optional(CONF_FROST_GUARD_TEMPERATURE_ENTITY): cv.entity_id,
        vol.Optional(
            CONF_BOOST_TEMPERATURE, default=DEFAULT_BOOST_TEMPERATURE
        ): vol.Coerce(float),
        vol.Optional(CONF_BOOST_TEMPERATURE_ENTITY): cv.entity_id,
        vol.Optional(
            CONF_BOOST_DURATION, default=DEFAULT_BOOST_DURATION
        ): cv.time_period,
        vol.Optional(
            CONF_DAY_TEMPERATURE, default=DEFAULT_DAY_TEMPERATURE
        ): vol.Coerce(float),
        vol.Optional(CONF_DAY_TEMPERATURE_ENTITY): cv.entity_id,
        vol.Optional(
            CONF_NIGHT_TEMPERATURE, default=DEFAULT_NIGHT_TEMPERATURE
        ): vol.Coerce(float),
        vol.Optional(CONF_NIGHT_TEMPERATURE_ENTITY): cv.entity_id,
        vol.Optional(CONF_NIGHT_START, default=DEFAULT_NIGHT_START): cv.time,
        vol.Optional(CONF_NIGHT_START_ENTITY): cv.entity_id,
        vol.Optional(CONF_NIGHT_END, default=DEFAULT_NIGHT_END): cv.time,
        vol.Optional(CONF_NIGHT_END_ENTITY): cv.entity_id,
    }
)


def _time_in_window(now: time, start: time, end: time) -> bool:
    """Return whether `now` falls within [start, end), wrapping past midnight."""
    if start == end:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the Netatmo Local Presets climate entity from YAML."""
    async_add_entities(
        [
            NetatmoLocalPresetClimate(
                source_entity_id=config[CONF_SOURCE],
                name=config[CONF_NAME],
                away_temperature=config[CONF_AWAY_TEMPERATURE],
                away_temperature_entity=config.get(CONF_AWAY_TEMPERATURE_ENTITY),
                frost_guard_temperature=config[CONF_FROST_GUARD_TEMPERATURE],
                frost_guard_temperature_entity=config.get(
                    CONF_FROST_GUARD_TEMPERATURE_ENTITY
                ),
                boost_temperature=config[CONF_BOOST_TEMPERATURE],
                boost_temperature_entity=config.get(CONF_BOOST_TEMPERATURE_ENTITY),
                boost_duration=config[CONF_BOOST_DURATION],
                day_temperature=config[CONF_DAY_TEMPERATURE],
                day_temperature_entity=config.get(CONF_DAY_TEMPERATURE_ENTITY),
                night_temperature=config[CONF_NIGHT_TEMPERATURE],
                night_temperature_entity=config.get(CONF_NIGHT_TEMPERATURE_ENTITY),
                night_start=config[CONF_NIGHT_START],
                night_start_entity=config.get(CONF_NIGHT_START_ENTITY),
                night_end=config[CONF_NIGHT_END],
                night_end_entity=config.get(CONF_NIGHT_END_ENTITY),
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
        source_entity_id: str,
        name: str,
        away_temperature: float,
        away_temperature_entity: str | None,
        frost_guard_temperature: float,
        frost_guard_temperature_entity: str | None,
        boost_temperature: float,
        boost_temperature_entity: str | None,
        boost_duration: timedelta,
        day_temperature: float,
        day_temperature_entity: str | None,
        night_temperature: float,
        night_temperature_entity: str | None,
        night_start: time,
        night_start_entity: str | None,
        night_end: time,
        night_end_entity: str | None,
    ) -> None:
        self._source_entity_id = source_entity_id
        self._attr_name = name
        self._attr_unique_id = f"netatmo_local_presets_{source_entity_id}"

        # Each "setting" is a (static_default, optional_helper_entity_id) pair.
        # Pointing the *_entity option at an `input_number`/`input_datetime`
        # (or `number`/`time`) helper lets these be adjusted live from the
        # Home Assistant UI, no YAML edits or restarts required.
        self._preset_temperatures = {
            PRESET_AWAY: (away_temperature, away_temperature_entity),
            PRESET_FROST_GUARD: (frost_guard_temperature, frost_guard_temperature_entity),
            PRESET_BOOST: (boost_temperature, boost_temperature_entity),
        }
        self._day_temperature = (day_temperature, day_temperature_entity)
        self._night_temperature = (night_temperature, night_temperature_entity)
        self._night_start = (night_start, night_start_entity)
        self._night_end = (night_end, night_end_entity)
        self._boost_duration = boost_duration

        self._aux_entity_ids = sorted(
            {
                entity_id
                for _, entity_id in (
                    *self._preset_temperatures.values(),
                    self._day_temperature,
                    self._night_temperature,
                    self._night_start,
                    self._night_end,
                )
                if entity_id is not None
            }
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
        if self._aux_entity_ids:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass, self._aux_entity_ids, self._handle_aux_change
                )
            )
        self.async_on_remove(self._stop_auto_scheduler)

    @callback
    def _handle_source_change(self, event: Event[EventStateChangedData]) -> None:
        self._sync_from_source(event.data["new_state"])
        self.async_write_ha_state()

    @callback
    def _handle_aux_change(self, event: Event[EventStateChangedData]) -> None:
        """React to a helper entity (temperature/night window) changing."""
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
            self._boost_end = dt_util.utcnow() + self._boost_duration
            self._cancel_boost_timer = async_call_later(
                self.hass, self._boost_duration, self._async_boost_finished
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
        """Re-push the current preset/auto temperature after a helper changes."""
        if self._attr_hvac_mode == HVACMode.AUTO:
            await self._async_apply_auto_temperature()
            return

        target_temperature = self._preset_temperature_value(self._attr_preset_mode)
        if target_temperature is not None and target_temperature != self._attr_target_temperature:
            await self._async_call_source_set_temperature(target_temperature)

    async def _async_apply_auto_temperature(self) -> None:
        """Push the day or night setpoint, whichever currently applies."""
        night_start = self._resolve_time(*self._night_start)
        night_end = self._resolve_time(*self._night_end)
        now = dt_util.as_local(dt_util.utcnow()).time()
        setting = (
            self._night_temperature
            if _time_in_window(now, night_start, night_end)
            else self._day_temperature
        )
        temperature = self._resolve_temperature(*setting)
        if temperature != self._attr_target_temperature:
            await self._async_call_source_set_temperature(temperature)

    def _preset_temperature_value(self, preset_mode: str) -> float | None:
        setting = self._preset_temperatures.get(preset_mode)
        if setting is None:
            return None
        return self._resolve_temperature(*setting)

    def _resolve_temperature(self, static_value: float, entity_id: str | None) -> float:
        """Return the live value of a helper entity, falling back to the default."""
        if entity_id is not None:
            state = self.hass.states.get(entity_id)
            if state is not None and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                try:
                    return float(state.state)
                except ValueError:
                    _LOGGER.warning("Ignoring non-numeric state of %s", entity_id)
        return static_value

    def _resolve_time(self, static_value: time, entity_id: str | None) -> time:
        """Return the live value of a time helper entity, falling back to the default."""
        if entity_id is not None:
            state = self.hass.states.get(entity_id)
            if state is not None and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                parsed = dt_util.parse_time(state.state)
                if parsed is not None:
                    return parsed
        return static_value

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
