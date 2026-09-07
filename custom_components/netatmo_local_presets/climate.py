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
calculated on the relay/cloud side and is not exposed locally at all, so
"Schedule" here just means "resume the configured comfort setpoint" - it
cannot follow your real Netatmo schedule program.
"""

from __future__ import annotations

from datetime import timedelta
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
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

_LOGGER = logging.getLogger(__name__)

CONF_SOURCE = "source"
CONF_AWAY_TEMPERATURE = "away_temperature"
CONF_FROST_GUARD_TEMPERATURE = "frost_guard_temperature"
CONF_BOOST_TEMPERATURE = "boost_temperature"
CONF_BOOST_DURATION = "boost_duration"
CONF_SCHEDULE_TEMPERATURE = "schedule_temperature"

PRESET_SCHEDULE = "schedule"
PRESET_AWAY = "away"
PRESET_FROST_GUARD = "frost_guard"
PRESET_BOOST = "boost"
PRESET_MANUAL = "manual"

ATTR_BOOST_END = "boost_end"

DEFAULT_NAME = "Netatmo Local Thermostat"
DEFAULT_AWAY_TEMPERATURE = 12.0
DEFAULT_FROST_GUARD_TEMPERATURE = 7.0
DEFAULT_BOOST_TEMPERATURE = 30.0
DEFAULT_BOOST_DURATION = timedelta(minutes=30)
DEFAULT_SCHEDULE_TEMPERATURE = 19.0

PLATFORM_SCHEMA = CLIMATE_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_SOURCE): cv.entity_id,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(
            CONF_AWAY_TEMPERATURE, default=DEFAULT_AWAY_TEMPERATURE
        ): vol.Coerce(float),
        vol.Optional(
            CONF_FROST_GUARD_TEMPERATURE, default=DEFAULT_FROST_GUARD_TEMPERATURE
        ): vol.Coerce(float),
        vol.Optional(
            CONF_BOOST_TEMPERATURE, default=DEFAULT_BOOST_TEMPERATURE
        ): vol.Coerce(float),
        vol.Optional(
            CONF_BOOST_DURATION, default=DEFAULT_BOOST_DURATION
        ): cv.time_period,
        vol.Optional(
            CONF_SCHEDULE_TEMPERATURE, default=DEFAULT_SCHEDULE_TEMPERATURE
        ): vol.Coerce(float),
    }
)


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
                frost_guard_temperature=config[CONF_FROST_GUARD_TEMPERATURE],
                boost_temperature=config[CONF_BOOST_TEMPERATURE],
                boost_duration=config[CONF_BOOST_DURATION],
                schedule_temperature=config[CONF_SCHEDULE_TEMPERATURE],
            )
        ]
    )


class NetatmoLocalPresetClimate(ClimateEntity, RestoreEntity):
    """A climate entity that adds Netatmo-style presets to a local thermostat."""

    _attr_should_poll = False
    _attr_temperature_unit = "°C"
    _attr_translation_key = "netatmo_local_presets"
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
    )
    _attr_preset_modes = [
        PRESET_SCHEDULE,
        PRESET_AWAY,
        PRESET_FROST_GUARD,
        PRESET_BOOST,
        PRESET_MANUAL,
    ]

    def __init__(
        self,
        source_entity_id: str,
        name: str,
        away_temperature: float,
        frost_guard_temperature: float,
        boost_temperature: float,
        boost_duration: timedelta,
        schedule_temperature: float,
    ) -> None:
        self._source_entity_id = source_entity_id
        self._attr_name = name
        self._attr_unique_id = f"netatmo_local_presets_{source_entity_id}"

        self._preset_temperatures = {
            PRESET_AWAY: away_temperature,
            PRESET_FROST_GUARD: frost_guard_temperature,
            PRESET_BOOST: boost_temperature,
            PRESET_SCHEDULE: schedule_temperature,
        }
        self._boost_duration = boost_duration

        self._attr_preset_mode = PRESET_SCHEDULE
        self._attr_current_temperature: float | None = None
        self._attr_target_temperature: float | None = None
        self._attr_hvac_mode = HVACMode.OFF

        self._preset_before_boost = PRESET_SCHEDULE
        self._cancel_boost_timer = None
        self._boost_end = None

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

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source_entity_id], self._handle_source_change
            )
        )

    @callback
    def _handle_source_change(self, event: Event[EventStateChangedData]) -> None:
        self._sync_from_source(event.data["new_state"])
        self.async_write_ha_state()

    @callback
    def _sync_from_source(self, state) -> None:
        """Mirror current/target temperature and hvac mode from the source entity."""
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        self._attr_hvac_mode = HVACMode.HEAT if state.state == "heat" else HVACMode.OFF
        self._attr_current_temperature = state.attributes.get("current_temperature")

        target = state.attributes.get(ATTR_TEMPERATURE)
        if target is not None:
            self._attr_target_temperature = target
            # If someone changed the temperature directly on the source entity
            # (e.g. from the Home app) to something that doesn't match the
            # active preset, fall back to "manual" so the UI isn't misleading.
            expected = self._preset_temperatures.get(self._attr_preset_mode)
            if expected is not None and abs(target - expected) > 0.3:
                self._attr_preset_mode = PRESET_MANUAL

    async def async_set_temperature(self, **kwargs) -> None:
        """Forward a manual temperature change to the source entity."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        self._cancel_pending_boost()
        self._boost_end = None
        self._attr_preset_mode = PRESET_MANUAL
        await self._async_call_source_set_temperature(temperature)
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Forward on/off to the source entity."""
        await self.hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": self._source_entity_id, "hvac_mode": hvac_mode},
            blocking=True,
        )

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

        target_temperature = self._preset_temperatures.get(preset_mode)
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

    async def _async_call_source_set_temperature(self, temperature: float) -> None:
        await self.hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": self._source_entity_id, ATTR_TEMPERATURE: temperature},
            blocking=True,
        )
