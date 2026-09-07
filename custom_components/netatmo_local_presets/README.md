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
temp for a set duration, Schedule = your normal comfort setpoint). This
component recreates them as a wrapper `climate` entity sitting on top of
your already-local HomeKit thermostat entity, using only standard
`climate.set_temperature` / `climate.set_hvac_mode` calls. Everything stays
on your LAN.

**What it can't do:** it cannot replay your actual Netatmo weekly schedule
program (that logic lives on the relay/cloud side and isn't reachable
locally) — "Schedule" preset here just resumes one configurable comfort
setpoint.

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
    schedule_temperature: 19
```

4. Restart Home Assistant again. A new `climate` entity will appear with
   `preset_modes: [schedule, away, frost_guard, boost, manual]`.

## Notes

- `manual` is reported automatically whenever the target temperature drifts
  away from the active preset's value (e.g. you changed it from the Home
  app), so the UI never lies about what's actually set.
- Boost automatically reverts to the previous preset after
  `boost_duration`, even across a Home Assistant restart (it recalculates
  based on restored state).
- You can run several instances of this platform, one per `source` thermostat.
