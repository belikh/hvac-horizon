---
title: Sensors
summary: Every sensor and attribute an HVAC Horizon entry publishes, with its key, unit and meaning.
section: reference
order: 20
audience: users
status: draft
---

## Entities per entry

Each entry creates one device named `HVAC Horizon` and the entry's title: the area's name, or the AC's when no area is set. The device holds one `climate` entity for the room, 73 sensors and 13 binary sensors. See [[guides/use-the-room-thermostat]] for the climate entity.

A sensor's entity ID is `sensor.hvac_horizon_<title>_<name>`, and a binary sensor's starts with `binary_sensor`. The names are entity translations, so Home Assistant can show them in the user's language once a translation exists.

Numeric sensors have a device class: temperatures, temperature differences, durations, energy and power. Home Assistant shows them in your unit system. The Key column is the suffix of the unique ID and the name of the value in the diagnostics download.

## Primary sensors

These 12 sensors have no entity category. Home Assistant shows them on the device page and in dashboards.

| Key | Name | Unit | Meaning |
| :-- | :-- | :-- | :-- |
| `minutes_to_setpoint` | Minutes to setpoint | min | Forecast minutes for the room to reach the cool threshold at the current running power. In a heating or drying forecast, the minutes to that target. |
| `energy_kwh` | Energy to setpoint | kWh | AC energy for that time. An upper bound for a unit that cycles. |
| `cost` | Cost to setpoint | none | That energy priced slot by slot from the price forecast. Empty with no price sensor. |
| `status` | Status | none | State of the forecast: `learning`, `no input`, `no running power yet`, `at setpoint`, `reachable` or `unreachable`. Carries the attributes below. |
| `forecast_error_min` | Forecast error | min | Median absolute error of the backtest runs, in minutes. |
| `target_c` | Target temperature | °C | With control on, the temperature the AC cools above this tick. With control off, the comfort setpoint. |
| `preset` | Preset | none | The preset in use: `comfort`, `eco`, `boost` or `away`. |
| `cool_on_above_c` | Cool above | °C | The AC cools when the room is above this value. |
| `heat_on_below_c` | Heat below | °C | The heater heats when the room is below this value: the heat target minus 0.5 K. |
| `minutes_low` | Minutes to setpoint, low | min | Low end of the forecast range, from the standard error of the cooling rate. |
| `minutes_high` | Minutes to setpoint, high | min | High end of the forecast range. |
| `forecast_mode` | Forecast mode | none | What the forecast sensors show: `cool`, `heat` or `dry`. |

## Diagnostic sensors

These 21 sensors have the entity category `diagnostic` and are enabled. They show under **Diagnostic** on the device page.

| Key | Name | Unit | Meaning |
| :-- | :-- | :-- | :-- |
| `samples` | Samples collected | none | Stored readings. The store keeps 8640, 30 days at 5 minutes. |
| `day_type` | Day type | none | Day class from the forecast high, or the outdoor reading with no weather entity. |
| `night_plan` | Night plan | none | `pre-cool`, `hold` or none, from the room and the coming night's low. |
| `precool_status` | Pre-cool status | none | Why a pre-cool start exists or not, for example `start in 25 min`, `late: starts now, 30 min to the event` or `no schedule entity`. |
| `daily_briefing` | Daily briefing | none | One line: the day type, the comfort target and the next action. |
| `window_advice` | Window advice | none | `open`, `close` or `hold` for windows, from the room, the outdoor temperature and the outdoor humidity. |
| `ventilation` | Ventilation | none | Why the room fan runs or not, from CO2 and PM2.5. |
| `humidity` | Humidity action | none | `dehumidify`, `humidify` or `within band`, from room humidity. |
| `compliance_pct` | Comfort compliance | % | Share of stored readings inside the comfort band. |
| `occupancy` | Occupancy | none | `home` or `away`, from the presence sources and `away_delay_min`. |
| `learning_suggestion` | Learning suggestion | none | Set when the fitted heat-gain rate drifts from the stored baseline. Answer with `respond_suggestion`. |
| `sensors_stalled` | Stalled sensors | none | The sensors with no report inside the stall window. |
| `mpc_status` | MPC plan effect | none | Whether the cooling plan changed the decision this tick. |
| `heat_source` | Heat source | none | `boiler` at 5 °C outdoors or below, otherwise `ac`. |
| `dispatch` | Room dispatch | none | The fleet devices chosen for the current gap, with each device's rate and power. |
| `pending` | Pending action | none | A start held by the restart delay, for example `start in 2 min: restart delay`, or the gate that holds a wanted heater off, for example `heat held: window open`. |
| `ac_setpoint_sent_c` | Setpoint sent | °C | The setpoint last sent to the AC, after setpoint regulation. |
| `ac_sensor_offset_k` | Sensor offset | K | Room median minus the AC's own temperature reading. |
| `outdoor_source` | Outdoor source | none | `sensor`, or `forecast` while the outdoor sensor is stale and the hourly forecast is under 2 hours old. |

## Sensors disabled by default

These 40 sensors hold internal detail. To use one, open it in **Settings > Devices & services > Entities** and turn on **Enabled**. The coordinator publishes every key whether or not its sensor is enabled.

| Key | Name | Unit | Meaning |
| :-- | :-- | :-- | :-- |
| `ventilation_rate` | Ventilation rate | none | Fan duty from CO2, 0 to 1. |
| `fan_speed` | Fan speed | none | AC fan mode chosen by `auto_fan`. |
| `price_used_per_kwh` | Price used per kWh | none | The price used this tick. The last good price stands in for 60 minutes when the sensor drops. |
| `solar_gain_kw` | Solar gain | kW | Solar gain through the window, measured or modelled. |
| `band_action` | Band action | none | `heat`, `cool` or `idle` against the comfort band. |
| `samples_heat` | AC heat intervals | none | Stored intervals with the AC in heat mode. The cooling fit leaves them out. |
| `samples_dry` | AC dry intervals | none | Stored intervals with the AC in dry mode. The cooling fit leaves them out. |
| `heater_feedback` | Heater feedback | none | Whether the heater switch state matches the command. |
| `valve_opening_pct` | Valve opening | % | Valve opening sent to the TRV. |
| `cover_action` | Cover action | none | `close`, `open` or none for the cover, from solar gain. |
| `second_ac` | Second AC | none | State of the second AC drive. |
| `rooms_max_age_s` | Room reading age | s | Age of the oldest room reading in use. |
| `safety_output_pct` | Safety output | % | Heater output forced by a stalled sensor. |
| `economizer` | Economizer | none | `off`, `open_windows` or `fan_assist`. |
| `motion_preset` | Motion preset | none | `home` or `away` from the motion sensor. |
| `shed` | Loads shed | none | Loads switched off by the power limit this tick. |
| `restored` | Load switched back on | none | Load switched back on this tick. |
| `mpc_plan` | MPC plan steps | none | Cooling plan steps, 15 minutes each for 4 hours. |
| `mpc_heat_steps` | MPC heat plan steps | none | Heat plan steps. |
| `solar_scale` | Solar scale | none | Learned ratio of measured to modelled solar gain. |
| `heater_pid_output` | Heater PID output | % | PID output before limits. |
| `heater_output_pct` | Heater output | % | Heater output after limits and gates. |
| `tpi_output_pct` | TPI output | % | TPI output. |
| `preheat` | Preheat | none | `off`, `waiting`, `active` or `cannot reach comfort` before a comfort block. |
| `heat_up_min` | Heat-up time | min | Learned minutes to heat to comfort. |
| `backtest_runs` | Backtest runs | none | Count of backtest runs. |
| `trv_offset_k` | TRV offset | K | TRV reading minus room reading. |
| `room_fit` | Two-device room fit | none | The two-device room fit. |
| `forecast_days` | Forecast day counts | none | Count of forecast days by day type. |
| `outdoor_forecast_next_c` | Outdoor forecast ahead | none | Outdoor series 1, 3 and 6 hours ahead in °C, with the learned bias. |
| `solar_factor` | Solar cloud factor | none | Share of clear-sky solar gain from the forecast condition: sunny 1.0, partlycloudy 0.7, cloudy 0.4, rainy 0.2. |
| `outdoor_bias_now_k` | Outdoor forecast bias | K | Learned outdoor sensor minus forecast for this hour of day. 0 until 6 pairs exist. |
| `outdoor_bias_pairs` | Outdoor bias pairs | none | Pairs the bias has learned from. `reset_learning` clears them. |
| `forecast_target` | Forecast target | none | Target of a heating forecast in °C, or of a drying forecast in percent. Unknown for a cooling forecast. |
| `fit_corrections` | Fit corrections | none | The kept corrections for sun, outdoor temperature and humidity, with `improvement_pct`. Unknown with none. |
| `fit_window_days` | Fit window | d | Days of readings the fit used: 14 or 30. |
| `kept_models` | Kept models | none | Each model in use that comes from an earlier fit, with its age in days: `fit`, `tpi`, `heat_rate`, `ac_heat`, `dry`, `corrections`. Unknown when every model is from the stored readings. |
| `heat_model` | Heat model | none | The heating model: `source` (`heater` or `ac`), `k_per_h` and `power_kw`. |
| `dry_model` | Dry model | none | The drying model: `pct_per_h` and `samples`. |
| `free_cooling` | Free cooling | none | `idle`, `running` or `gave_up`. |
| `commands_unconfirmed` | Unconfirmed commands | none | Devices whose state differs from the last command on two ticks in a row, and commands a device refused since the last tick. Enabled by default. |
| `watchdog_trips` | Watchdog trips | none | Times the watchdog turned the outputs off since the entry loaded. Enabled by default. |

A list or dict value shows as JSON text cut to 255 characters in the state. The full value is in the `value` attribute. A key the coordinator did not publish this tick reads `unknown`.

## Binary sensors

These 13 values are on or off. Each is a `binary_sensor` entity with the entity category `diagnostic`. Before 0.7.0 they were sensors that read `True` or `False`.

| Key | Name | Device class | On means | Enabled |
| :-- | :-- | :-- | :-- | :-- |
| `drying_needed` | Drying needed | none | Room humidity is above the humidity target (60 % by default) and the room needs no cooling. In `dry` mode the cooling condition does not apply. | yes |
| `mould_risk` | Mould risk | `problem` | The estimated surface humidity is 80 % or more. Every cool threshold is then 1 K higher. | yes |
| `heatwave` | Heatwave | `heat` | `heatwave_warning` is on, or the daily highs reach 30 °C three days running. It changes no device command. | yes |
| `heating_allowed` | Heating allowed | none | Heating may run. Off while outdoors is above 22 °C in the automatic mode or `dry`: the heater, the boiler and AC heat then stay off. On in `heat` mode. | yes |
| `external_change` | External change | none | The AC setpoint differs from the one HVAC Horizon last wrote: someone changed it by hand. | yes |
| `vacation` | Vacation mode | none | The vacation switch is on. | yes |
| `whole_house_fan` | Whole-house fan | `running` | The whole-house fan may run. | yes |
| `heating_failure` | Heating failure | `problem` | The heater ran 60 minutes and the room rose less than 0.5 K. | yes |
| `ac_failure` | AC failure | `problem` | The AC has been in `cool` or `dry` for 20 minutes while the room still needs it, and its power stayed under 200 W for more than half of them. Needs the power sensor. See [[guides/troubleshoot#the-ac-is-on-and-the-compressor-does-not-run|The AC is on and the compressor does not run]]. | yes |
| `outdoor_forecast_used` | Outdoor forecast used | none | A fresh hourly forecast feeds the forecast, the plans, pre-cool and preheat. | yes |
| `heater_pwm_on` | Heater PWM on | `running` | The heater is on inside the current 10 minute window. | no |
| `boiler_demand` | Boiler demand | none | The boiler should run. | no |
| `house_cooling` | House cooling | none | Another entry of the same house cools. | no |

A binary sensor reads `unknown` until its value is computed.

## Attributes

These attributes sit on the entity named in the Entity column. They are read from the coordinator's last result.

| Attribute | Entity | Content |
| :-- | :-- | :-- |
| `reason` | Status | Why the forecast or the fit is in its current state: the fit reason (`fitted`, `not enough data`, `need at least 3 hours of AC on and off time over 3 runs`) or the forecast reason (`reachable`, `not reached within 12 hours`). It does not hold the control decision. The action text is in `investigation` and the Daily briefing. |
| `k_per_h` | Status | Heat-gain rate toward outdoors of the fitted model, in 1/h. |
| `c_k_per_kw` | Status | Cooling effect of the AC, in K per hour per kW. |
| `g_k_per_h` | Status | Constant gain of the fitted model (internal heat), in K per hour. |
| `autotune_status` | Status | State of the last relay test: `running`, `done`, `timed_out`, `cancelled` or `reached_cutoff`. None before any test. |
| `autotune` | Status | Gains kp, ki and kd after a relay test. None before. |
| `shed_skipped` | Status | Set when a shed load entry is malformed. |
| `investigation` | Status | Rule-based answer to why the AC runs or does not, with recent actions. |
| `investigation_ai` | Status | Conversation agent answer, when `ai_conversation` is set. |
| `investigation_ai_mode` | Status | The AC mode that the agent answer explains. None with no answer. |
| `ai_calls_today` | Status | Model calls made today, counted against `ai_budget_per_day`. |
| `investigation_report` | Status | The 24-hour report from `generate_investigation_report`. None until it runs. |
| `investigation_report_source` | Status | `model` when the agent answered, otherwise `rule`. |
| `investigation_report_at` | Status | ISO time the report was stored. |
| `room_rate_coefficients` | Status | k, g from AC-off intervals and c from AC-on intervals. None until fitted. |
| `auto_tpi_gains` | Status | Online cool gain on c, clamped from 0.5 to 1.5. 1.0 until it moves. |
| `accuracy_pairs` | Forecast error | The last 20 backtest runs, each with predicted, measured and error minutes. |
| `range_coverage_pct` | Forecast error | Percentage of the backtest runs whose measured minutes fell between the low and high forecast of that run. |
| `range_runs` | Forecast error | Count of backtest runs behind `range_coverage_pct`. |

:::see-also
- [[guides/read-the-forecast]]: how to read the forecast sensors and the status.
- [[reference/settings]]: the settings that feed each sensor.
:::
