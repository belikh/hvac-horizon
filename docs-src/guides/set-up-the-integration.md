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

Each field has a help text under it. It says what the field needs, what it changes, its range and its default, and when to leave it alone. A field that depends on another one says so, for example `Needs a heater switch.`

## Sections

| Section | What you set there |
| :-- | :-- |
| Room | The AC and its power sensor or rated power, the heater switch, the room sensors and the outdoor sensor |
| Comfort and presets | Setpoints for each preset, the vacation switch |
| Schedule blocks | Presets by time of day, one block per submit |
| Price | The price sensor and the cheap and expensive thresholds |
| Heater | The heater's power and algorithm, a valve, a boiler, AC heat. The heater switch itself is in Room |
| Occupancy and windows | Presence, motion, window sensors, a cover, a window actuator, the weather entity |
| Air quality and humidity | Humidity, CO2 and PM2.5 sensors, fans, a dehumidifier, a humidifier |
| Devices and power limits | A second AC, the dispatch mode, load shedding |
| Room devices | Extra coolers and heaters the integration chooses between, one device per submit |
| Advanced | Control, the lock PIN, peer rooms, the investigator |
| Expert tuning | PID and TPI gains, output limits, minimum on and off times, the stall window. The defaults suit most rooms |

[[reference/settings]] lists every field of each section with its default and its effect.

## Add a heater

1. Open **Room** and select the heater's switch in **Heater switch**.
2. Select **Submit**, then **Save and close**.

The room thermostat now lists `heat`. The options in the table below are in **Heater**. The heater runs when the room is 0.5 K or more below the heat target. In `auto` it also needs outdoors at or below 22 °C. `heat` has no outdoor limit. The heater output is a share of a 10 minute window, so the switch turns on and off inside each window.

| To | Set |
| :-- | :-- |
| Use a switch where on means off | `heater_inverted` |
| Heat with the AC and no heater switch | `ac_heat` |
| Account for radiators that stay warm after they stop | `radiators` |
| Use the TPI algorithm in place of PID | `heater_algorithm` to `tpi` |
| Limit the heater output | `output_min`, `output_max` in **Expert tuning** |

The PID gains come from a relay test when you run one. See `start_autotune` in [[guides/services-and-presets]].

## Add an electricity price

1. Open **Price** and select the price sensor. Its unit is per kWh.
2. Set **Cheap price** and **Expensive price** in the same unit. The defaults are 0.15 and 0.40.

The price only raises the cool threshold. Up to the midpoint of the cheap and the expensive price the threshold is your target. From there it rises in a line, and at or above the expensive price it is 1 K higher.

::diagram price-shift The cool threshold against the price. It equals the target up to the midpoint of the cheap and the expensive price, 0.275 with the defaults. From there it rises in a line to 1 K above the target at the expensive price, 0.40, and stays there.

The Cost to setpoint sensor needs a `forecast` attribute on the price sensor: a list of `time` and `value` pairs.

## Add presence and motion

1. Open **Occupancy and windows**.
2. Select one or more entities in **Presence sources**. A source in state `home` or `on` means home.
3. Select a motion sensor in **Motion sensor in the room** if the room has one.

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

The AC does not cool while the entry holds the window open or runs the whole-house fan. When the room is still 2 K above the cool threshold after 30 minutes, the window closes, the fan stops and the AC cools. The same happens when the room is above the cool threshold and fell by less than 0.3 K in 30 minutes.

With an outdoor humidity sensor in `humidity`, the window and the economizer stay shut when the outdoor dew point is 1 K or more above the room's.

## Add a second AC or other room devices

For two ACs that always run together, set `climate_2` and `power_2` in **Devices and power limits**.

For devices that HVAC Horizon should choose between, open **Room devices**. Give the new device a name, its kind (`cool` or `heat`), the climate entity or heater switch that drives it and its rated power, then select **Submit**. Turn on **Add another device after this one** to stay on the form. To remove devices, tick them under **Remove these devices**. See [[reference/settings#room-devices-fleet|Room devices]] for each field.

`dispatch_mode` sets the choice. `fast` runs the device with the largest effect first. `economy` runs the device with the lowest cost per kelvin first. A device with no power sensor counts its `rated_w` while it runs.

## Errors on the form

| Message | Cause | Fix |
| :-- | :-- | :-- |
| `This device is already set up.` | Another entry uses the same AC or heater switch. | Reconfigure that entry. |
| `Pick an air conditioner or a heater switch. A room needs one of them.` | The Room step has neither. | Select one of them. |
| `Pick the AC's power sensor, or enter its rated power in watts.` | The AC has neither. | Select the sensor or enter the watts. |
| `The cheap price threshold must be below the expensive price threshold.` | The two prices are equal or swapped. | Lower the cheap price or raise the expensive one. |
| `The new block overlaps the block 22:00 to 06:30: eco.` | A new schedule block shares a time of day with the block named. The schedule uses one block at a time. | Remove that block first, or change the times. |
| `The minimum heater output must not be above the maximum.` | `output_min` is above `output_max` in Expert tuning. | Swap or correct the two values. |
| `Set both TPI thresholds, or leave both empty.` | One TPI threshold is set alone, which has no effect. | Set the other one, or clear both. |
| `Set both the start and the end of the new block.` | A schedule block has one time only. | Set both times, or clear both to add no block. |
| `The start and the end of a block must differ.` | Both times of a schedule block are the same. | Change one of them. |
| `Room devices are invalid` followed by a reason | The new device breaks a rule, such as a repeated name or a cooler on a switch. | Correct the field named in the reason. |

A section with an error stores nothing from that section.

:::see-also
- [[reference/settings]]: every field with its default and effect.
- [[guides/use-the-room-thermostat]]: modes, targets and presets.
:::
