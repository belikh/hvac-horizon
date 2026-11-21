---
title: Set up the integration
summary: Add HVAC Horizon with the AC, power, room and outdoor sensors, then turn on control when you are ready.
section: guides
order: 10
audience: users
status: draft
---

[![Add integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=hvac_horizon)

## Before you start

Four fields are required. Have these entities ready.

| Field | Form label | Entity |
| :-- | :-- | :-- |
| `climate` | AC climate entity | A `climate` entity for the AC |
| `power` | AC power sensor (W) | A `sensor` with device class `power`, in watts |
| `room_sensors` | Room temperature sensors | One or more `sensor` entities with device class `temperature` |
| `outdoor` | Outdoor temperature sensor | A `sensor` with device class `temperature` |

## Add the integration

1. Select **Add integration** above. Or open **Settings > Devices & services**, select **Add integration**, and search for **HVAC Horizon**.
2. Fill in the four required fields in the **Room** step and select **Submit**.
3. In the **What next** menu, select **Finish now** to create the entry in observe-only mode. Or open a section from the steps below.
4. After a section, the menu returns. Open another section or select **Finish now**.

The entry takes the friendly name of the climate entity. One climate entity can have one entry. A second entry for the same entity stops with `This AC is already set up.`

The integration takes a sample every 5 minutes.

## Setup steps

The first form has the four required fields only. Every other field sits in one of seven optional sections. A section is one form. Submit it empty to keep the defaults. A section with an error, such as a malformed schedule block, shows the error on that section and stores nothing from it.

| Step | Step ID | Fields |
| :-- | :-- | :-- |
| Room | `room` | `climate`, `power`, `room_sensors`, `outdoor` |
| Comfort and presets | `comfort` | `comfort_c`, `eco_c`, `boost_c`, `away_c`, `heat_eco_c`, `heat_boost_c`, `comfort_energy_weight`, `schedule`, `schedule_blocks`, `vacation`, `vacation_c`, `away_delay_min` |
| Price | `price` | `price`, `price_cheap`, `price_expensive`, `energy` |
| Heater | `heater` | `heater_switch`, `heater_inverted`, `radiators`, `heater_algorithm`, `heater_kp`, `heater_ki`, `heater_kd`, `tpi_coef_int`, `tpi_coef_ext`, `tpi_threshold_high`, `tpi_threshold_low`, `max_on_percent`, `output_min`, `output_max`, `output_min_entity`, `output_max_entity`, `valve`, `trv_temperature`, `boiler`, `ac_heat`, `mpc_heat_plan` |
| Occupancy and windows | `occupancy` | `presence`, `motion`, `motion_away_min`, `window`, `window_delay_min`, `window_actuator`, `cover`, `window_azimuth`, `window_area_m2`, `irradiance`, `weather`, `heatwave_warning` |
| Air quality and humidity | `air_quality` | `humidity`, `room_humidity`, `co2`, `pm25`, `fan`, `auto_fan`, `dehumidifier`, `humidifier`, `coil_dry`, `dry_for_humidity`, `house_sealed`, `economizer_fan`, `whole_house_fan` |
| Devices and power limits | `devices` | `climate_2`, `power_2`, `fleet`, `dispatch_mode`, `whole_house_power`, `shed_max_w`, `shed_loads`, `compressor_group`, `min_on_min`, `min_off_min` |
| Advanced | `advanced` | `control`, `lock_pin`, `stall_window_s`, `night_plan_act`, `accuracy_panel`, `peer_entry`, `peer_setpoint_mode`, `setpoint_regulation`, `ai_conversation`, `ai_budget_per_day` |

The tables below group the same fields by topic.

## Optional fields

Every optional field is off until you set it, unless the table gives a default. Labels are the form labels. The setup steps table above shows which step holds each field.

### Price and energy

| Field | Form label | Default | Effect |
| :-- | :-- | :-- | :-- |
| `price` | Electricity price sensor (optional) | none | Sets the price used for cost. Without it the target does not move with price. |
| `price_cheap` | Cheap price threshold | 0.15 | At or below this price the target moves 1 K down. |
| `price_expensive` | Expensive price threshold | 0.40 | At or above this price the target moves 1 K up. |
| `energy` | Energy counter (optional, kWh) | none | Stored with each reading. |

The price thresholds use the same unit as the price sensor, per kWh. Between the thresholds the shift is linear.

### Humidity and air quality

| Field | Form label | Default | Effect |
| :-- | :-- | :-- | :-- |
| `humidity` | Outdoor humidity sensor (optional) | none | Stored with each reading. |
| `room_humidity` | Room humidity sensor (optional) | none | Keeps room humidity between 40 and 60 %. Above 60 % the dehumidifier runs. Below 40 % the humidifier runs. Sets the drying status. |
| `dehumidifier` | Dehumidifier switch (optional) | none | On while room humidity is above 60 %. |
| `humidifier` | Humidifier switch (optional) | none | On while room humidity is below 40 %. |
| `co2` | CO2 sensor (optional) | none | Above 1000 ppm the room fan runs. |
| `pm25` | PM2.5 sensor (optional) | none | Above 25 µg/m³ the room fan runs. |
| `fan` | Room fan switch (optional) | none | Runs for ventilation. |
| `dry_for_humidity` | Use the AC dry mode when humid | off | Above 60 % room humidity, with no dehumidifier set, the AC runs dry mode. Dry mode uses a lot of water. |

### Presence, motion and windows

| Field | Form label | Default | Effect |
| :-- | :-- | :-- | :-- |
| `presence` | Presence sources | none | A source in state `on` or `home` means home. All sources away for `away_delay_min` means away. |
| `away_delay_min` | Minutes without presence before away mode | 30 | Delay before an empty house counts as away. |
| `motion` | Motion sensor | none | When set, motion decides occupancy. |
| `motion_away_min` | Minutes without motion before the room counts as unoccupied | 30 | Used only with `motion`. Minimum 1. |
| `window` | Window or door sensor | none | A window open longer than `window_delay_min` pauses cooling. The heater is also gated. |
| `window_delay_min` | Minutes a window may stay open before the AC pauses | 10 | |
| `window_azimuth` | Window facing direction, degrees from north | 180 | Direction of the window for solar gain. |
| `window_area_m2` | Window area, m² | none | Needed for solar gain. |
| `irradiance` | Irradiance sensor on the window, W/m² | none | Measured sun on the window for solar gain. |
| `cover` | Cover or blind | none | Closes the cover above 0.5 kW of solar gain. Opens it again below that. |

### Setpoints and presets

| Field | Form label | Default | Effect |
| :-- | :-- | :-- | :-- |
| `comfort_c` | Comfort setpoint (°C) | 24.0 | The normal target. Range 16 to 30. |
| `eco_c` | Eco setpoint (°C) | none | Target for the eco preset. An unoccupied house cools to this value when set. |
| `boost_c` | Boost setpoint (°C) | 23.0 | Target for the boost preset. |
| `away_c` | Away setpoint (°C) | 27.0 | Cooling target for an unoccupied house when `eco_c` is not set. Also the away preset target. |
| `heat_eco_c` | Heat setpoint under eco and when motion is idle (°C) | comfort minus 1 | Heat target for eco. |
| `heat_boost_c` | Heat setpoint under boost (°C) | comfort plus 1 | Heat target for boost. |
| `comfort_energy_weight` | Comfort to eco weight, 0 to 1 | 0 | 0 keeps the comfort target. 1 uses the eco setpoint. Needs `eco_c`. |
| `vacation` | Vacation switch, on means vacation mode | none | While on, cooling holds `vacation_c` and heating holds 16 °C. |
| `vacation_c` | Vacation setpoint (°C) | 27.0 | Cooling target while vacation is on. |
| `schedule_blocks` | Schedule blocks | empty | Presets by hour of day. See [[guides/services-and-presets]]. |
| `schedule` | Schedule entity (optional) | none | Its `next_event` attribute, an ISO time, sets the deadline for pre-cooling. |

### Heater and limits

| Field | Form label | Default | Effect |
| :-- | :-- | :-- | :-- |
| `heater_switch` | Heater switch (optional) | none | Switch for the heater plug. The heater output runs in 10 minute windows. |
| `heater_inverted` | Heater switch is inverted (on means off) | off | Swaps the on and off commands for the heater switch. |
| `heater_algorithm` | Heater algorithm | `pid` | `pid` or `tpi`. |
| `output_min` | Minimum heater output (%) | 0 | Lower limit for the heater output. |
| `output_max` | Maximum heater output (%) | 100 | Upper limit for the heater output. |
| `output_min_entity` | Minimum heater output number entity (optional) | none | When set, its value overrides `output_min`. |
| `output_max_entity` | Maximum heater output number entity (optional) | none | When set, its value overrides `output_max`. |
| `min_on_min` | Minimum AC on time (min), deprecated | 5 | Ignored. A stop is never delayed. |
| `min_off_min` | Restart delay (min) | 3 | Shortest time a device stays off before it starts again. 0 disables the delay. |
| `stall_window_s` | Seconds without a report before a sensor counts as stalled | 900 | Range 300 to 3600 seconds. See [[guides/troubleshoot]]. |
| `lock_pin` | Lock PIN (optional) | none | Needed to lock control. See [[guides/troubleshoot]]. |
| `accuracy_panel` | Show this entry in the HVAC Accuracy sidebar panel | off | Adds this entry to one shared sidebar panel. |

### Other fields

| Field | Form label | Default | Effect |
| :-- | :-- | :-- | :-- |
| `climate_2` | Second AC in the same room (optional) | none | Adds a second cooling device to the room model. |
| `power_2` | Second AC power sensor, W (optional) | none | Power of the second AC. With `climate_2` and this field, the two-device fit runs. |
| `weather` | Weather entity for the forecast (optional) | none | Supplies the daily and hourly forecast. The day type, the night plan, the outdoor temperature series and the outdoor fallback use it. See [[guides/read-the-forecast]]. |
| `heatwave_warning` | Heatwave warning sensor (optional) | none | A binary sensor. While it is on, the heatwave status is on. |
| `whole_house_power` | Whole-house power sensor (optional) | none | Total house power, W. Used with `shed_max_w`. |
| `shed_max_w` | Power limit in watts (optional) | none | House limit, W. Above it, the loads in `shed_loads` switch off. |
| `shed_loads` | Loads to shed, as entity_id\|watts, lowest priority first (optional) | none | One `entity_id\|watts` entry per load. The first entry sheds first. |
| `compressor_group` | Compressor group name (optional) | none | Entries with the same name keep 5 minutes between their AC starts. |
| `valve` | Valve or TRV opening number (optional) | none | Opening in percent. Fully open 1 K below the target, closed at the target. |
| `trv_temperature` | TRV own temperature sensor (optional, needs a valve) | none | The TRV's own reading, compared with the room. Each 1 K of offset moves the opening by 100 points, clamped to 0 to 100. |
| `peer_entry` | Peer entry, the entry_id or title of another HVAC Horizon entry (optional) | none | When this room is 2 K warmer, the peer entry's valve opens. |
| `peer_setpoint_mode` | Peer setpoint mode | off | With a peer entry, a room 2 K warmer lowers the peer's cool setpoint by 1 K instead of opening its valve. |
| `boiler` | Boiler switch (optional) | none | On when the heat source is the boiler, at 5 °C outdoors or below, and the room is below comfort minus 1 K. |
| `window_actuator` | Window actuator, a cover or switch (optional) | none | Opens on natural ventilation or economizer advice. Closes when the advice ends. |
| `economizer_fan` | Economizer fan assist | off | Runs the fan switch in the economizer fan assist phase. Needs home, unlocked and no stalled sensor. |
| `night_plan_act` | Night plan acts on the AC | off | Pre-cool cools to comfort. Hold stops a cool start. |
| `mpc_heat_plan` | Heat plan starts AC heat | off | With the AC as heat source, no heater switch and someone home, the heat plan starts AC heat when its first step says heat. |
| `ac_heat` | Let the AC heat | off | Lets the AC heat when the heat source is the AC and no heater switch is set. |
| `radiators` | Heater is radiators or underfloor heat | off | Counts the residual heat after the heater stops: a 1 K peak that decays over 20 minutes. |
| `heater_kp` | Heater PID proportional gain | 20 | Used at the default. A value you set wins. At the default, the gain from a relay test applies. |
| `heater_ki` | Heater PID integral gain (per hour) | 60 | Same rule as `heater_kp`. |
| `heater_kd` | Heater PID derivative time (hours) | 0.5 | Same rule as `heater_kp`. |
| `tpi_coef_int` | TPI coefficient for the room gap (per K) | 0.6 | TPI gain on the room gap. |
| `tpi_coef_ext` | TPI coefficient for the outdoor gap (per K) | 0.01 | TPI gain on the outdoor gap. |
| `tpi_threshold_high` | TPI high threshold, K above target while the room rises | none | With the low threshold, forces the TPI output to 0. Both must be above 0 to apply. |
| `tpi_threshold_low` | TPI low threshold, K above target while the room falls | none | With the high threshold, forces the TPI output to 0. Both must be above 0 to apply. |
| `max_on_percent` | TPI maximum on-percent | none, no cap | Caps the TPI output, 0 to 100 %. |
| `whole_house_fan` | Whole-house fan switch (optional) | none | On when outdoors are at least 2 K cooler than indoors, the house is not sealed and the AC is off. |
| `house_sealed` | House is sealed | off | Stops the whole-house fan. |
| `coil_dry` | Dry the AC coil after cooling | off | Runs the AC dry mode for 20 minutes after cooling. |
| `auto_fan` | Set the AC fan mode from the room gap | off | Sets the AC fan mode from the gap to the target while cooling. |
| `fleet` | Room devices (list) | empty | The heaters and coolers in the room. HVAC Horizon runs the ones that cover the gap to the target. See [[reference/settings]] for the device keys. |
| `dispatch_mode` | Device choice | `fast` | `fast` runs the device that delivers the most first. `economy` runs the device with the lowest cost per kelvin first. |
| `ai_conversation` | AI conversation agent for the investigator (optional) | none | Writes the investigator answer when the AC mode changes, within the daily budget. |
| `ai_budget_per_day` | Model calls per day for the investigator | none, 0 is off | Daily limit on model calls. |

## Observe only and control

Observe only is the default. The `control` field is off.

With control off, the integration reads sensors, learns and publishes forecasts. It does not set the AC mode or setpoint, and it does not drive the heater switch.

To turn control on:

1. Open the HVAC Horizon entry and start its reconfigure action. The menu is titled **Edit HVAC Horizon**.
2. Select **Advanced** and turn on **Let HVAC Horizon control the AC (off = observe only)**.
3. Select **Submit**, then **Save and close**. The entry reloads.

The form reports `Settings updated. Stored samples are kept.` when the save works.

With control on, the Target temperature and Preset sensors show the planned values. With control off, Target temperature shows the comfort setpoint and Preset shows `comfort`.

## Change settings later

Reconfigure opens a menu with the same sections, plus **Room**, and **Save and close**. Open a section, change it and submit. The menu returns. Nothing is saved until you select **Save and close**.

A section replaces its own fields. A field you clear in a section is removed. Sections you do not open keep their values. The lock PIN is the exception: a blank PIN box keeps the stored PIN.

Stored samples stay. Only the `reset_learning` service clears them. See [[guides/services-and-presets]].

:::see-also
- [[guides/read-the-forecast]]: what the sensors show after setup.
- [[guides/services-and-presets]]: presets, schedule blocks and the services.
:::
