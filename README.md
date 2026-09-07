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

> **Upgrading from a YAML-configured version?** Remove the whole
> `climate: - platform: netatmo_local_presets` block from
> `configuration.yaml` first — this version is set up entirely through the
> UI and no longer reads that config.

1. Copy the `custom_components/netatmo_local_presets` folder into your Home
   Assistant `config/custom_components/` directory.
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration**, search for
   **Netatmo Local Presets**, and pick the local `climate.<something>`
   entity from `homekit_controller` as the source, plus a name.

This creates one device with:

- a `climate` entity: `hvac_modes: [heat, auto, off]`,
  `preset_modes: [schedule, away, frost_guard, boost]`.
- `number` entities: **Away temperature**, **Frost guard temperature**,
  **Boost temperature**, **Day temperature**, **Night temperature**,
  **Boost duration**.
- `time` entities: **Night start**, **Night end**.

Open the device's page (**Settings → Devices & Services → Netatmo Local
Presets → your device**) to adjust any of those numbers/times directly —
no YAML, no restart, no helper entities required. Changes apply
immediately (within a minute for the day/night schedule check).

## Notes

- `schedule` (hvac mode `auto`) automatically pushes the day temperature
  during the day and the night temperature during the night window, and
  re-checks every minute so it reacts to number/time entity changes too.
- Selecting `away`, `frost_guard` or `boost` switches hvac mode to `heat`
  and applies that preset's temperature; selecting hvac mode `auto`
  directly is equivalent to selecting the `schedule` preset.
- Manually adjusting the temperature while a preset is active doesn't
  invent a separate "manual" state — it just changes the setpoint, matching
  the original Netatmo integration.
- Boost automatically reverts to the previous preset after its configured
  duration, even across a Home Assistant restart (it recalculates based on
  restored state).
- You can add the integration multiple times, once per source thermostat.


