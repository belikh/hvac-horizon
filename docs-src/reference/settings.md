---
title: Settings
summary: Every setting in the HVAC Horizon setup and reconfigure forms, with its stored key, whether it is required, and its default.
section: reference
order: 1
audience: users
status: draft
---

## Settings

The setup and reconfigure forms store these keys in the config entry. A key marked `unset` has no default in the form. The code uses the value in brackets when the key is absent.

Only `climate`, `power`, `room_sensors` and `outdoor` are required. Every other key is optional.

A heater gain from a relay test replaces the unset or default value of `heater_kp`, `heater_ki` or `heater_kd`. A gain you set yourself takes precedence over the tuned value.

| Key | Required | Default | Label |
| :-- | :-- | :-- | :-- |
| `climate` | required | unset | AC climate entity |
| `power` | required | unset | AC power sensor (W) |
| `room_sensors` | required | unset | Room temperature sensors |
| `outdoor` | required | unset | Outdoor temperature sensor |
| `climate_2` | optional | unset | Second AC in the same room (optional) |
| `power_2` | optional | unset | Second AC power sensor, W (optional, enables the two-device fit) |
| `weather` | optional | unset | Weather entity for the forecast (optional) |
| `price` | optional | unset | Electricity price sensor (optional) |
| `humidity` | optional | unset | Outdoor humidity sensor (optional) |
| `room_humidity` | optional | unset | Room humidity sensor, e.g. the bedroom monitor (optional) |
| `energy` | optional | unset | Energy counter (optional, kWh) |
| `cover` | optional | unset | Cover or blind (optional) |
| `window` | optional | unset | Window or door sensor (optional) |
| `co2` | optional | unset | CO2 sensor (optional) |
| `fan` | optional | unset | Room fan switch (optional) |
| `dehumidifier` | optional | unset | Dehumidifier switch (optional) |
| `boiler` | optional | unset | Boiler switch (optional) |
| `humidifier` | optional | unset | Humidifier switch (optional) |
| `pm25` | optional | unset | PM2.5 sensor (optional) |
| `schedule` | optional | unset | Schedule entity (optional) |
| `motion` | optional | unset | Motion sensor (optional) |
| `presence` | optional | unset | Presence sources: any home means home, all away for the away delay means away (optional) |
| `valve` | optional | unset | Valve or TRV opening number (optional) |
| `trv_temperature` | optional | unset | TRV own temperature sensor (optional, needs a valve). A TRV that reads warmer than the room opens the valve more. Each 1 K of offset (TRV minus room) moves the valve opening by 100 percentage points, clamped to 0 to 100. |
| `whole_house_power` | optional | unset | Whole-house power sensor (optional) |
| `vacation` | optional | unset | Vacation switch, on means vacation mode (optional) |
| `vacation_c` | optional | unset (code uses `27.0`) | Vacation setpoint (°C) |
| `heater_switch` | optional | unset | Heater switch (optional) |
| `radiators` | optional | `False` | Heater is radiators or underfloor heat (residual heat after it stops) |
| `heater_kp` | optional | unset (code uses `20.0`) | Heater PID proportional gain |
| `heater_ki` | optional | unset (code uses `60.0`) | Heater PID integral gain (per hour) |
| `heater_kd` | optional | unset (code uses `0.5`) | Heater PID derivative time (hours) |
| `heater_algorithm` | optional | `pid` | Heater algorithm |
| `tpi_coef_int` | optional | `0.6` | TPI coefficient for the room gap (per K, used with TPI) |
| `tpi_coef_ext` | optional | `0.01` | TPI coefficient for the outdoor gap (per K, used with TPI) |
| `tpi_threshold_high` | optional | unset | TPI high threshold, K above target while the room rises (TPI; 0 is off, needs the low threshold) |
| `tpi_threshold_low` | optional | unset | TPI low threshold, K above target while the room falls (TPI; 0 is off, needs the high threshold) |
| `max_on_percent` | optional | unset | TPI maximum on-percent (TPI; unset means no cap) |
| `whole_house_fan` | optional | unset | Whole-house fan switch (optional) |
| `lock_pin` | optional | unset | Lock PIN (optional) |
| `coil_dry` | optional | `False` | Dry the AC coil after cooling |
| `dry_for_humidity` | optional | `False` | Use the AC dry mode when humid (no dehumidifier set; uses a lot of water) |
| `ac_heat` | optional | `False` | Let the AC heat when the heat source is the AC (no heater switch) |
| `mpc_heat_plan` | optional | `False` | Heat plan starts AC heat when its first step says heat (AC heat source, no heater switch, someone home) |
| `night_plan_act` | optional | `False` | Night plan acts on the AC: pre-cool cools to comfort, hold stops a cool start |
| `economizer_fan` | optional | `False` | Economizer fan assist: run the fan switch in the economizer fan_assist phase (home, unlocked, no stalled sensor) |
| `accuracy_panel` | optional | `False` | Show this entry in the HVAC Accuracy sidebar panel (one panel for all entries) |
| `window_actuator` | optional | unset | Window actuator, a cover or switch (optional): opens on natural ventilation or economizer advice |
| `peer_entry` | optional | unset | Peer entry, the entry_id or title of another HVAC Horizon entry (optional): its valve opens when this room is 2 K warmer |
| `peer_setpoint_mode` | optional | `False` | Peer setpoint mode: with a peer entry, a room 2 K warmer lowers the peer cool setpoint by 1 K instead of opening its valve |
| `auto_fan` | optional | `False` | Set the AC fan mode from the room gap while cooling |
| `house_sealed` | optional | `False` | House is sealed (never run the whole-house fan) |
| `price_cheap` | optional | unset (code uses `0.15`) | Cheap price threshold |
| `price_expensive` | optional | unset (code uses `0.4`) | Expensive price threshold |
| `min_on_min` | optional | unset (code uses `5.0`) | Minimum AC on time (min) |
| `min_off_min` | optional | unset (code uses `3.0`) | Minimum AC off time (min) |
| `away_delay_min` | optional | unset (code uses `30.0`) | Minutes without presence before away mode |
| `motion_away_min` | optional | `30.0` | Minutes without motion before the room counts as unoccupied |
| `stall_window_s` | optional | `900.0` | Seconds without a report before a sensor counts as stalled (default 900) |
| `shed_max_w` | optional | unset | Power limit in watts (optional) |
| `shed_loads` | optional | unset | Loads to shed, as entity_id\|watts, lowest priority first (optional) |
| `compressor_group` | optional | unset | Compressor group name (optional). Entries with the same name keep at least 5 minutes between their AC starts. |
| `comfort_c` | optional | `24.0` | Comfort setpoint (°C) |
| `eco_c` | optional | unset | Eco setpoint (°C) |
| `comfort_energy_weight` | optional | unset | Comfort to eco weight, 0 to 1 (optional, 0 is off) |
| `heat_eco_c` | optional | unset | Heat setpoint under eco and when motion is idle (°C, optional). Unset heats to comfort minus 1. |
| `heat_boost_c` | optional | unset | Heat setpoint under boost (°C, optional). Unset heats to comfort plus 1. |
| `boost_c` | optional | `23.0` | Boost setpoint (°C) |
| `away_c` | optional | `27.0` | Away setpoint (°C) |
| `window_delay_min` | optional | `10.0` | Minutes a window may stay open before the AC pauses |
| `window_azimuth` | optional | `180.0` | Window facing direction, degrees from north (optional) |
| `window_area_m2` | optional | unset | Window area, m² (optional, needed for solar gain) |
| `irradiance` | optional | unset | Irradiance sensor on the window, W/m² (optional) |
| `control` | optional | `False` | Let HVAC Horizon control the AC (off = observe only) |
| `heater_inverted` | optional | `False)` | Heater switch is inverted (on means off) |
| `output_min` | optional | unset (code uses `0.0`) | Minimum heater output (%) |
| `output_max` | optional | unset (code uses `100.0`) | Maximum heater output (%) |
| `output_min_entity` | optional | unset | Minimum heater output number entity (optional, overrides the minimum above) |
| `output_max_entity` | optional | unset | Maximum heater output number entity (optional, overrides the maximum above) |
| `schedule_blocks` | optional | `list` | Schedule blocks: a list of {start_h, end_h, preset}, hours 0 to 24, preset comfort, eco, boost or away (optional) |
| `ai_conversation` | optional | unset | AI conversation agent for the investigator (optional) |
| `ai_budget_per_day` | optional | unset | Model calls per day for the investigator (0 or empty is off) |
