---
title: Settings
summary: Every setting of an HVAC Horizon entry, by form section, with its default and what it changes.
section: reference
order: 10
audience: users
status: draft
---

## How settings are stored

Each entry has 88 settings. Two are required: `room_sensors` and `outdoor`. A room also needs `climate` or `heater_switch`, and an AC needs `power` or `ac_rated_w`. The rest are optional, in eight form sections and two list steps. Change them with **Reconfigure** on the entry. See [[guides/set-up-the-integration]].

The Key column is the name in the config entry and in the diagnostics download. The label is the text on the form.

`tests/ha/test_settings_effect.py` runs each setting at two values over a scripted day. It fails when a setting stops changing the device commands or the published data.

## Room

| Key | Form label | Default | Effect |
| :-- | :-- | :-- | :-- |
| `climate` | AC climate entity | unset | The AC that HVAC Horizon reads and, with control on, drives. Leave it empty for a room with heating only. A room needs this or `heater_switch`. |
| `power` | AC power sensor (W) | unset | Power of the whole AC unit in watts. Feeds the room model, the energy forecast and the running check: below 0.2 kW the AC counts as off. An AC needs this or `ac_rated_w`. |
| `ac_rated_w` | AC rated power, W | unset | For an AC with no power sensor. The AC counts as drawing this while its `hvac_action` is `cooling`, `heating` or `drying`. An AC that reports no action counts by its mode. |
| `heater_switch` | Heater switch | unset | Switch for the heater plug. The heater output runs in 10 minute windows. |
| `room_sensors` | Room temperature sensors | required | One or more temperature sensors in the room. The room temperature is their median. A stalled sensor drops out of the median. |
| `outdoor` | Outdoor temperature sensor | required | Outdoor temperature. Feeds the room model, the forecast, the 16 °C cooling limit of `auto` with a window actuator or whole-house fan, and the 22 °C heating limit of `auto` and `dry`. |

## Comfort and presets

| Key | Form label | Default | Effect |
| :-- | :-- | :-- | :-- |
| `schedule` | Schedule entity | unset | Its `next_event` attribute, an ISO time, sets the deadline for pre-cooling. When the room cannot reach the setpoint by the event and the event is within 120 minutes, cooling starts at once. |
| `vacation` | Vacation switch, on means vacation mode | unset | While on, cooling holds `vacation_c` and heating holds 16 °C. |
| `vacation_c` | Vacation setpoint (°C) | `27.0` | Cooling target while vacation is on. |
| `away_delay_min` | Minutes without presence before away mode | `30.0` | Delay before an empty house counts as away. |
| `comfort_c` | Comfort setpoint (°C) | `24.0` | The normal target. Range 16 to 30. |
| `eco_c` | Eco setpoint (°C) | unset | Target for the eco preset. An unoccupied house cools to this value when set. |
| `comfort_energy_weight` | Comfort to eco weight, 0 to 1 (0 is off) | unset | 0 keeps the comfort target. 1 uses the eco setpoint. Needs `eco_c`. |
| `heat_eco_c` | Heat setpoint under eco and when motion is idle | unset | Heat target for eco. |
| `heat_boost_c` | Heat setpoint under boost (°C, optional). Unset heats to comfort plus 1. | unset | Heat target for boost. |
| `boost_c` | Boost setpoint (°C) | `23.0` | Target for the boost preset. |
| `away_c` | Away setpoint (°C) | `27.0` | Cooling target for an unoccupied house when `eco_c` is not set. Also the away preset target. |
| `schedule_blocks` | Schedule blocks (its own step) | `[]` | Presets by time of day. See [[guides/services-and-presets#schedule-blocks|Schedule blocks]]. |

## Price

| Key | Form label | Default | Effect |
| :-- | :-- | :-- | :-- |
| `price` | Electricity price sensor | unset | Sets the price used for cost. Without it the target does not move with price. |
| `energy` | Energy counter (kWh) | unset | Cumulative energy counter. The room model uses it for interval power when the power sensor has no interval mean. |
| `price_cheap` | Cheap price threshold | `0.15` | Sets where the price shift starts: the midpoint of this price and `price_expensive`. Up to the midpoint the cool threshold is your target. |
| `price_expensive` | Expensive price threshold | `0.4` | At or above this price the cool threshold is 1 K above your target. |

## Heater

| Key | Form label | Default | Effect |
| :-- | :-- | :-- | :-- |
| `boiler` | Boiler switch | unset | On when the heat source is the boiler, at 5 °C outdoors or below, and the room is below comfort minus 1 K. |
| `valve` | Valve or TRV opening number | unset | Opening in percent. Fully open 1 K below the target, closed at the target. |
| `trv_temperature` | TRV own temperature sensor | unset | The TRV's own reading, compared with the room. Each 1 K of offset moves the opening by 100 points, clamped to 0 to 100. |
| `radiators` | Heater is radiators or underfloor heat (residual heat after it stops) | `False` | Counts the residual heat after the heater stops: a 1 K peak that decays over 20 minutes. |
| `heater_power_w` | Heater power, W | unset | Rated power of the heater on the heater switch. Gives the heating forecast its energy and cost. |
| `heater_algorithm` | Heater algorithm | `pid` | `pid` or `tpi`. PID stops 0.5 K below the heat target. TPI heats up to the heat target. |
| `ac_heat` | Let the AC heat when the heat source is the AC (no heater switch) | `False` | Lets the AC heat when the heat source is the AC and no heater switch is set. |
| `mpc_heat_plan` | Heat plan starts AC heat when its first step says heat | `False` | With the AC as heat source, no heater switch and someone home, the heat plan starts AC heat when its first step says heat. |
| `heater_inverted` | Heater switch is inverted (on means off) | `False` | Swaps the on and off commands for the heater switch. |

## Occupancy and windows

| Key | Form label | Default | Effect |
| :-- | :-- | :-- | :-- |
| `weather` | Weather entity for the forecast | unset | Supplies the daily and hourly forecast. The day type, the night plan, the outdoor temperature series and the outdoor fallback use it. See [[guides/read-the-forecast]]. |
| `heatwave_warning` | Heatwave warning binary sensor, on means a warning is current | unset | A binary sensor. While it is on, the Heatwave sensor is on. It changes no device command. |
| `cover` | Cover or blind | unset | Closes the cover above 0.5 kW of solar gain. Opens it again below that. |
| `window` | Window or door sensor | unset | A window open longer than `window_delay_min` pauses cooling. The heater is also gated. |
| `motion` | Motion sensor | unset | Motion in the room means occupied. No motion for `motion_away_min` means unoccupied: the room cools to `eco_c` or `away_c` and heats to the eco heat setpoint. |
| `presence` | Presence sources: any home means home, all away for the away delay means away | unset | A source in state `on` or `home` means home. All sources away for `away_delay_min` means away. With a motion sensor, motion in the room counts as occupied whatever presence says. Once motion is idle, an empty house heats to the 16 °C frost floor. |
| `window_actuator` | Window actuator, a cover or switch: opens on natural ventilation or economizer advice | unset | Opens on natural ventilation or economizer advice. Closes when the advice ends. The AC does not cool while the entry holds the window open. When the room is still 2 K above the cool threshold after 30 minutes, the window closes and the AC cools. The same happens when the room is above the cool threshold and fell by less than 0.3 K in 30 minutes. |
| `motion_away_min` | Minutes without motion before the room counts as unoccupied | `30.0` | Used only with `motion`. Minimum 1. |
| `window_delay_min` | Minutes a window may stay open before the AC pauses | `10.0` | Minutes a window may stay open before cooling pauses. 0 to 120. |
| `window_azimuth` | Window facing direction, degrees from north | `180.0` | Direction of the window for solar gain. |
| `window_area_m2` | Window area, m² (needed for solar gain) | unset | Needed for solar gain. |
| `irradiance` | Irradiance sensor on the window, W/m² | unset | Measured sun on the window for solar gain. |

## Air quality and humidity

| Key | Form label | Default | Effect |
| :-- | :-- | :-- | :-- |
| `humidity` | Outdoor humidity sensor | unset | Keeps windows shut to humid air. Window advice, the economizer and the window actuator stay closed when the outdoor dew point is 1 K or more above the room's. With no `room_humidity`, the limit is an outdoor dew point of 20 °C. |
| `room_humidity` | Room humidity sensor, e.g. the bedroom monitor | unset | Keeps room humidity between 40 and 60 %. Above 60 % the dehumidifier runs. Below 40 % the humidifier runs. Sets the drying status. |
| `co2` | CO2 sensor | unset | Above 1000 ppm the room fan runs. |
| `fan` | Room fan switch | unset | Runs for ventilation. |
| `dehumidifier` | Dehumidifier switch | unset | On while room humidity is above 60 %, off at 57 %. In `dry` mode with no humidity sensor it stays on. Off while the room is held off. |
| `humidifier` | Humidifier switch | unset | On while room humidity is below 40 %, off at 43 %. Off while the room is held off. |
| `pm25` | PM2.5 sensor | unset | Above 25 µg/m³ the room fan runs. |
| `whole_house_fan` | Whole-house fan switch | unset | On when the room is above the cool threshold, outdoors is at least 2 K cooler and the house is not sealed. The AC stays off while the fan runs. When the room is still 2 K above the cool threshold after 30 minutes, the fan stops and the AC cools. The same happens when the room is above the cool threshold and fell by less than 0.3 K in 30 minutes. |
| `coil_dry` | Dry the AC coil after cooling | `False` | Runs the AC dry mode for 20 minutes after cooling. |
| `dry_for_humidity` | Use the AC dry mode when humid (no dehumidifier set; uses a lot of water) | `False` | Above 60 % room humidity, with no dehumidifier set, the AC runs dry mode. Dry mode uses a lot of water. |
| `economizer_fan` | Economizer fan assist | `False` | Runs the fan switch in the economizer fan assist phase. Needs home, unlocked and no stalled sensor. |
| `auto_fan` | Set the AC fan mode from the room gap while cooling | `False` | Sets the AC fan mode from the gap to the target while cooling. |
| `house_sealed` | House is sealed (never run the whole-house fan) | `False` | Stops the whole-house fan. |

## Devices and power limits

| Key | Form label | Default | Effect |
| :-- | :-- | :-- | :-- |
| `climate_2` | Second AC in the same room | unset | Adds a second cooling device to the room model. |
| `power_2` | Second AC power sensor, W (enables the two-device fit) | unset | Power of the second AC. With `climate_2` and this field, the two-device fit runs. |
| `whole_house_power` | Whole-house power sensor | unset | Total house power, W. Used with `shed_max_w`. |
| `shed_max_w` | Power limit in watts | unset | House limit, W. Above it, the loads in `shed_loads` switch off. |
| `shed_loads` | Loads to shed, as entity_id\|watts, lowest priority first | unset | One `entity_id\|watts` entry per load. The first entry sheds first. |
| `compressor_group` | Compressor group name. Entries with the same name keep at least 5 minutes between their AC starts. | unset | Entries with the same name keep 5 minutes between their AC starts. |
| `fleet` | Room devices | `[]` | The heaters and coolers in the room. HVAC Horizon runs the ones that cover the gap to the target. See [[reference/settings]] for the device keys. |
| `dispatch_mode` | Device choice: fast (highest effect first) or economy (lowest cost per kelvin) | `fast` | `fast` runs the device that delivers the most first. `economy` runs the device with the lowest cost per kelvin first. |

## Advanced

| Key | Form label | Default | Effect |
| :-- | :-- | :-- | :-- |
| `lock_pin` | Lock PIN | unset | Needed to lock control. See [[guides/troubleshoot]]. |
| `night_plan_act` | Night plan acts on the AC: pre-cool cools to comfort, hold stops a cool start | `False` | Pre-cool cools to comfort. Hold stops a cool start. |
| `accuracy_panel` | Show this entry in the HVAC Accuracy sidebar panel (one panel for all entries) | `False` | Adds this entry to one shared sidebar panel. |
| `peer_entry` | Peer entry, the entry_id or title of another HVAC Horizon entry | unset | When this room is 2 K warmer, the peer entry's valve opens. |
| `peer_setpoint_mode` | Peer setpoint mode | `False` | With a peer entry, a room 2 K warmer lowers the peer's cool setpoint by 1 K instead of opening its valve. |
| `control` | Let HVAC Horizon control the AC (off = observe only) | `False` | Off: observe only. HVAC Horizon learns and forecasts and sends no commands. On: it drives the AC, the heater and the other devices. |
| `ai_conversation` | AI conversation agent for the investigator | unset | Writes the investigator answer when the AC mode changes, within the daily budget. |
| `ai_budget_per_day` | Model calls per day for the investigator (0 or empty is off) | unset | Daily limit on model calls. |

## Expert tuning

Controller gains, output limits and timing. The defaults suit most rooms.

| Key | Form label | Default | Effect |
| :-- | :-- | :-- | :-- |
| `heater_kp` | Heater PID proportional gain | `20.0` | Used at the default. A value you set wins. At the default, the gain from a relay test applies. |
| `heater_ki` | Heater PID integral gain (per hour) | `60.0` | Same rule as `heater_kp`. |
| `heater_kd` | Heater PID derivative time (hours) | `0.5` | Same rule as `heater_kp`. |
| `tpi_coef_int` | TPI coefficient for the room gap (per K, used with TPI) | `0.6` | TPI gain on the room gap. |
| `tpi_coef_ext` | TPI coefficient for the outdoor gap (per K, used with TPI) | `0.01` | TPI gain on the outdoor gap. |
| `tpi_threshold_high` | TPI high threshold, K above target while the room rises | unset | With the low threshold, forces the TPI output to 0 while the room rises and is this far above the target. Both must be above 0 to apply. TPI then heats up to the larger threshold above the target. |
| `tpi_threshold_low` | TPI low threshold, K above target while the room falls | unset | With the high threshold, forces the TPI output to 0 while the room falls and is this far above the target. Both must be above 0 to apply. |
| `max_on_percent` | TPI maximum on-percent (TPI; unset means no cap) | unset | Caps the TPI output, 0 to 100 %. |
| `output_min` | Minimum heater output (%) | `0.0` | Lower limit for the heater output. |
| `output_max` | Maximum heater output (%) | `100.0` | Upper limit for the heater output. |
| `output_min_entity` | Minimum heater output number entity (overrides the minimum above) | unset | When set, its value overrides `output_min`. |
| `output_max_entity` | Maximum heater output number entity (overrides the maximum above) | unset | When set, its value overrides `output_max`. |
| `min_on_min` | Deprecated. Kept for compatibility; a stop is never delayed and the value is ignored | unset | Deprecated and ignored. A stop is never delayed. |
| `min_off_min` | Restart delay (min): a start waits this long after the device stopped. 0 disables it | `3.0` | Restart delay. A start waits this long after the device stopped. 0 disables it. 0 to 120. |
| `stall_window_s` | Seconds without a report before a sensor counts as stalled (default 900) | `900.0` | Range 300 to 3600 seconds. See [[guides/troubleshoot]]. |
| `setpoint_regulation` | Move the AC setpoint by the gap between the room median and the AC's own `current_temperature`, or by measured power when the AC reports none | `True` | Lowers the setpoint sent to the AC by the gap between the room median and the AC's own `current_temperature`, so an AC whose sensor reads cool still runs. An AC with no such attribute is regulated from power. |

## Room devices (`fleet`)

`fleet` is a list of the heaters and coolers in the room. When it is set, HVAC Horizon picks which devices to run for the room's gap to the target. `dispatch_mode` sets the order: `fast` runs the device that delivers the most first, `economy` runs the device with the lowest cost per kelvin first. The `dispatch` sensor shows the choice. See [[reference/sensors]].

The **Room devices** step adds one device per submit. Each device is stored with these keys.

| Key | Required | Default | Meaning |
| :-- | :-- | :-- | :-- |
| `device_id` | required | none | Name of the device. Must be unique in the list. |
| `kind` | required | none | `cool` or `heat`. |
| `rated_w` | required | none | Watts the device draws while it runs. Used when there is no power sensor. |
| `entity_id` | optional | none | The `climate` entity that drives the device. A `heat` device can use a `switch` instead. Without it the device is chosen but not driven. |
| `power_sensor` | optional | none | Power sensor in watts. Its reading replaces `rated_w` while the device runs. |
| `rate_k_per_h_per_kw` | optional | none | Room change in kelvin per hour for each kW the device draws. A device without a rate is not chosen. Once the device has run alone for 6 intervals of 5 minutes, the fitted rate replaces it. |

```json title="Fleet"
[
  {"device_id": "split", "kind": "cool", "rated_w": 900, "rate_k_per_h_per_kw": 4.0, "entity_id": "climate.lounge_ac"},
  {"device_id": "panel", "kind": "heat", "rated_w": 1500, "rate_k_per_h_per_kw": 2.0, "entity_id": "switch.panel_heater"}
]
```

Fleet devices use the same restart delay `min_off_min` as the AC. The form shows `Room devices are invalid` with the reason when the list breaks a rule.

:::see-also
- [[guides/set-up-the-integration]]: how to open each form section.
- [[reference/sensors]]: what the entry publishes.
:::
