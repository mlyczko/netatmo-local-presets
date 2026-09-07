# Netatmo Local Presets

Adds **Away / Frost Guard / Boost / Schedule** presets on top of a Netatmo
Smart Thermostat / Smart Valve that is bridged **locally** into Home
Assistant — with **no Netatmo account, cloud API, or credentials involved
at any point**.

## Why this exists / what it can't do

Netatmo's thermostat relay (NAPlug) only speaks a proprietary, undocumented
protocol to Netatmo's cloud. There is no published local API for it (this
is confirmed by Home Assistant's own docs, which classify the official
`netatmo` integration as *Cloud Polling* and require internet access). No
public reverse-engineering of that protocol exists either, so a "true" local
client for the relay itself is not something that can honestly be written
right now.

The only thing Netatmo exposes locally is a **HomeKit accessory**, which
only implements Apple's generic `Thermostat` HAP service: on/off + target
temperature. Apple's spec has no concept of vendor presets, which is why
you don't see Away/Frost Guard/Boost there — that's a limitation of what
Netatmo chose to expose over HomeKit, not something Home Assistant can add
by itself.

**What this integration does:** those Netatmo "modes" are really just
target-temperature presets (Frost Guard ≈ 7°C, Away ≈ 12°C, Boost = high
temp for a set duration, Schedule = a day/night comfort setpoint). This
component recreates them as a wrapper `climate` entity sitting on top of
your already-local HomeKit thermostat entity, using only standard
`climate.set_temperature` / `climate.set_hvac_mode` calls. Everything stays
on your LAN.

**What it can't do:** it cannot replay your actual Netatmo weekly schedule
program (that logic lives on the relay/cloud side and isn't reachable
locally) — "Schedule" (hvac mode `auto`) here just switches between a
configurable day and night temperature at configurable times.

## Prerequisites

1. Pair your Netatmo Smart Thermostat/Valve as a **HomeKit Device** (as you
   already found) using the code on the device/relay.
2. In Home Assistant, set up the built-in **HomeKit Controller**
   (`homekit_controller`) integration and add the Netatmo accessory to it.
   This gives you a local `climate.<something>` entity — confirm it works
   (on/off + temperature) before continuing.

## Installation

1. Copy the `custom_components/netatmo_local_presets` folder into your Home
   Assistant `config/custom_components/` directory.
2. Restart Home Assistant.
3. Add a platform entry to `configuration.yaml`:

```yaml
climate:
  - platform: netatmo_local_presets
    source: climate.living_room_thermostat   # the entity from homekit_controller
    name: "Living Room (Netatmo Local)"
    away_temperature: 12
    frost_guard_temperature: 7
    boost_temperature: 30
    boost_duration: "00:30:00"
    day_temperature: 19
    night_temperature: 16
    night_start: "22:00:00"
    night_end: "06:00:00"
```

4. Restart Home Assistant again. A new `climate` entity will appear with
   `hvac_modes: [heat, auto, off]` and `preset_modes: [schedule, away,
   frost_guard, boost]`.

### Adjusting temperatures/times from the UI (optional)

Every temperature and the night window above can instead be pointed at a
Home Assistant helper entity, so you can change it live from the UI (no
YAML edits or restarts). Create `input_number` helpers for temperatures and
`input_datetime` (time-only) helpers for the night window under **Settings
→ Devices & Services → Helpers**, then reference them:

```yaml
climate:
  - platform: netatmo_local_presets
    source: climate.living_room_thermostat
    name: "Living Room (Netatmo Local)"
    away_temperature_entity: input_number.living_room_away_temperature
    frost_guard_temperature_entity: input_number.living_room_frost_guard_temperature
    boost_temperature_entity: input_number.living_room_boost_temperature
    day_temperature_entity: input_number.living_room_day_temperature
    night_temperature_entity: input_number.living_room_night_temperature
    night_start_entity: input_datetime.living_room_night_start
    night_end_entity: input_datetime.living_room_night_end
```

The static `*_temperature`/`night_start`/`night_end` options above still
apply as fallback defaults if a referenced helper is unavailable.

## Notes

- `schedule` (hvac mode `auto`) automatically pushes the day temperature
  during the day and the night temperature during the night window, and
  re-checks every minute so it reacts to helper entity changes too.
- Selecting `away`, `frost_guard` or `boost` switches hvac mode to `heat`
  and applies that preset's fixed temperature; selecting hvac mode `auto`
  directly is equivalent to selecting the `schedule` preset.
- Manually adjusting the temperature while a preset is active doesn't
  invent a separate "manual" state — it just changes the setpoint, matching
  the original Netatmo integration.
- Boost automatically reverts to the previous preset after
  `boost_duration`, even across a Home Assistant restart (it recalculates
  based on restored state).
- You can run several instances of this platform, one per `source` thermostat.

