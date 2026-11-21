---
title: Set up the integration
summary: Add a heater, a price sensor, presence, windows and other devices to an HVAC Horizon entry, one form section at a time.
section: guides
order: 10
audience: users
status: draft
---

## Open the settings

HVAC Horizon has no **Configure** button. You change an entry with **Reconfigure**.

1. Open **Settings > Devices & services** and select **HVAC Horizon**.
2. On the entry, select the three-dot menu, then **Reconfigure**. The menu is titled **Edit HVAC Horizon**.
3. Select a section, change its fields and select **Submit**. The menu returns.
4. Select **Save and close**. The form reports `Settings updated. Stored samples are kept.`

Nothing is saved until you select **Save and close**. A section you do not open keeps its values. A field you clear in a section is removed. The Lock PIN is the exception: an empty PIN box keeps the stored PIN.

The same sections appear when you add a new entry, after the **Room** step. Submit a section empty to keep its defaults.

## Sections

| Section | What you set there |
| :-- | :-- |
| Room | The AC, its power sensor, the room sensors and the outdoor sensor |
| Comfort and presets | Setpoints for each preset, schedule blocks, the vacation switch |
| Price | The price sensor and the cheap and expensive thresholds |
| Heater | A heater switch, its algorithm and limits, a valve, a boiler, AC heat |
| Occupancy and windows | Presence, motion, window sensors, a cover, a window actuator, the weather entity |
| Air quality and humidity | Humidity, CO2 and PM2.5 sensors, fans, a dehumidifier, a humidifier |
| Devices and power limits | A second AC, the room device list, load shedding, the restart delay |
| Advanced | Control, the lock PIN, the stall window, peer rooms, the investigator |

[[reference/settings]] lists every field of each section with its default and its effect.

## Add a heater

1. Open **Heater** and select the heater's switch in **Heater switch**.
2. Select **Submit**, then **Save and close**.

The room thermostat now lists `heat`. The heater runs when the room is 0.5 K or more below the heat target. In `auto` it also needs outdoors at or below 22 °C. `heat` has no outdoor limit. The heater output is a share of a 10 minute window, so the switch turns on and off inside each window.

| To | Set |
| :-- | :-- |
| Use a switch where on means off | `heater_inverted` |
| Heat with the AC and no heater switch | `ac_heat` |
| Account for radiators that stay warm after they stop | `radiators` |
| Use the TPI algorithm in place of PID | `heater_algorithm` to `tpi` |
| Limit the heater output | `output_min`, `output_max` |

The PID gains come from a relay test when you run one. See `start_autotune` in [[guides/services-and-presets]].

## Add an electricity price

1. Open **Price** and select the price sensor. Its unit is per kWh.
2. Set **Cheap price threshold** and **Expensive price threshold** in the same unit. The defaults are 0.15 and 0.40.

At or below the cheap price the cool threshold moves 1 K down. At or above the expensive price it moves 1 K up. Between the two the shift is linear. The Cost to setpoint sensor needs a `forecast` attribute on the price sensor: a list of `time` and `value` pairs.

## Add presence and motion

1. Open **Occupancy and windows**.
2. Select one or more entities in **Presence sources**. A source in state `home` or `on` means home.
3. Select a motion sensor in **Motion sensor** if the room has one.

| Situation | Cools above | Heats to |
| :-- | :-- | :-- |
| Someone is home, or motion in the room | The preset's setpoint | The preset's heat setpoint |
| Motion idle for `motion_away_min` (30 minutes), someone home | `eco_c`, or `away_c` when unset | `heat_eco_c`, or comfort minus 1 |
| Every presence source away for `away_delay_min` (30 minutes) | `eco_c`, or `away_c` when unset | 16 °C |

Motion in the room counts as occupied whatever the presence sources say.

## Add windows, covers and free cooling

| Device | Field | What HVAC Horizon does |
| :-- | :-- | :-- |
| Window or door sensor | `window` | Pauses cooling and heating when the window is open longer than `window_delay_min` (10 minutes) |
| Cover or blind | `cover` | Closes the cover above 0.5 kW of solar gain. Needs `window_area_m2`. |
| Window actuator | `window_actuator` | Opens the window when outdoors is at least 2 K cooler than the room and at or below 24 °C |
| Whole-house fan | `whole_house_fan` | Runs when the room is above the cool threshold and outdoors is at least 2 K cooler |

The AC does not cool while the entry holds the window open or runs the whole-house fan. When the room is still 2 K above the cool threshold after 30 minutes, the window closes, the fan stops and the AC cools.

With an outdoor humidity sensor in `humidity`, the window and the economizer stay shut when the outdoor dew point is 1 K or more above the room's.

## Add a second AC or other room devices

For two ACs that always run together, set `climate_2` and `power_2` in **Devices and power limits**.

For devices that HVAC Horizon should choose between, fill in **Room devices** (`fleet`). Each device has a kind, a rated power and a rate. See [[reference/settings#room-devices-fleet|Room devices]] for the keys.

```json title="Room devices"
[
  {"device_id": "split", "kind": "cool", "rated_w": 900, "rate_k_per_h_per_kw": 4.0, "entity_id": "climate.lounge_ac"},
  {"device_id": "panel", "kind": "heat", "rated_w": 1500, "rate_k_per_h_per_kw": 2.0, "entity_id": "switch.panel_heater"}
]
```

`dispatch_mode` sets the choice. `fast` runs the device with the largest effect first. `economy` runs the device with the lowest cost per kelvin first. A device with no power sensor counts its `rated_w` while it runs.

## Errors on the form

| Message | Cause | Fix |
| :-- | :-- | :-- |
| `This AC is already set up.` | Another entry uses the same climate entity. | Reconfigure that entry. |
| `Schedule blocks must be a list of [start_h, end_h, preset]. Hours run from 0 to 24 and start_h must differ from end_h. Presets: comfort, eco, boost, away.` | A schedule block is malformed. | Correct the block. See [[guides/services-and-presets#schedule-blocks|Schedule blocks]]. |
| `Room devices are invalid` followed by a reason | A fleet device breaks a rule, such as an unknown key or a repeated `device_id`. | Correct the device named in the reason. |

A section with an error stores nothing from that section.

:::see-also
- [[reference/settings]]: every field with its default and effect.
- [[guides/use-the-room-thermostat]]: modes, targets and presets.
:::
