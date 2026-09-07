"""Shared, live-adjustable settings for one Netatmo Local Presets device.

The climate entity reads these values directly (and subscribes to changes);
the number/time entities are thin UI wrappers that read and write them. This
is what lets every temperature and the night window be adjusted from
Settings -> Devices & Services with no YAML or helper entities involved.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import time

DEFAULT_AWAY_TEMPERATURE = 12.0
DEFAULT_FROST_GUARD_TEMPERATURE = 7.0
DEFAULT_BOOST_TEMPERATURE = 30.0
DEFAULT_BOOST_DURATION_MINUTES = 30.0
DEFAULT_DAY_TEMPERATURE = 19.0
DEFAULT_NIGHT_TEMPERATURE = 16.0
DEFAULT_NIGHT_START = time(22, 0)
DEFAULT_NIGHT_END = time(6, 0)


@dataclass
class PresetStore:
    """Adjustable values for one Netatmo Local Presets instance."""

    away_temperature: float = DEFAULT_AWAY_TEMPERATURE
    frost_guard_temperature: float = DEFAULT_FROST_GUARD_TEMPERATURE
    boost_temperature: float = DEFAULT_BOOST_TEMPERATURE
    boost_duration_minutes: float = DEFAULT_BOOST_DURATION_MINUTES
    day_temperature: float = DEFAULT_DAY_TEMPERATURE
    night_temperature: float = DEFAULT_NIGHT_TEMPERATURE
    night_start: time = DEFAULT_NIGHT_START
    night_end: time = DEFAULT_NIGHT_END
    _listeners: list[Callable[[], None]] = field(default_factory=list, repr=False)

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a callback invoked whenever a value changes."""
        self._listeners.append(listener)

        def remove() -> None:
            self._listeners.remove(listener)

        return remove

    def notify(self) -> None:
        """Tell listeners (the climate entity) that a value changed."""
        for listener in list(self._listeners):
            listener()
