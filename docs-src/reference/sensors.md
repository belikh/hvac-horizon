---
title: Sensors
summary: Every sensor and status attribute that HVAC Horizon publishes, with its key and unit.
section: reference
order: 2
audience: users
status: draft
---

## Sensors

Each entry creates the 66 sensors below, plus the room climate entity. See [[guides/services-and-presets]] for the climate entity. The entity ID is `sensor.<name>` for the entry's name. The key is the suffix that the integration uses in the unique ID, and the attribute key where a value is read from the `status` sensor.

| Key | Name | Unit | Group |
| :-- | :-- | :-- | :-- |
| `minutes_to_setpoint` | Minutes to setpoint | min | Primary |
| `energy_kwh` | Energy to setpoint | kWh | Primary |
| `cost` | Cost to setpoint | none | Primary |
| `status` | Status | none | Primary |
| `samples` | Samples collected | none | Diagnostic |
| `forecast_error_min` | Forecast error | min | Primary |
| `day_type` | Day type | none | Diagnostic |
| `night_plan` | Night plan | none | Diagnostic |
| `precool_status` | Pre-cool status | none | Diagnostic |
| `daily_briefing` | Daily briefing | none | Diagnostic |
| `window_advice` | Window advice | none | Diagnostic |
| `ventilation` | Ventilation | none | Diagnostic |
| `ventilation_rate` | Ventilation rate | none | Disabled by default |
| `humidity` | Humidity action | none | Diagnostic |
| `drying_needed` | Drying needed | none | Diagnostic |
| `mould_risk` | Mould risk | none | Diagnostic |
| `compliance_pct` | Comfort compliance | % | Diagnostic |
| `heatwave` | Heatwave | none | Diagnostic |
| `fan_speed` | Fan speed | none | Disabled by default |
| `price_used_per_kwh` | Price used per kWh | none | Disabled by default |
| `occupancy` | Occupancy | none | Diagnostic |
| `solar_gain_kw` | Solar gain | kW | Disabled by default |
| `band_action` | Band action | none | Disabled by default |
| `target_c` | Target temperature | °C | Primary |
| `preset` | Preset | none | Primary |
| `heating_allowed` | Heating allowed | none | Diagnostic |
| `samples_heat` | AC heat intervals | none | Disabled by default |
| `samples_dry` | AC dry intervals | none | Disabled by default |
| `heater_feedback` | Heater feedback | none | Disabled by default |
| `heater_pwm_on` | Heater PWM on | none | Disabled by default |
| `valve_opening_pct` | Valve opening | % | Disabled by default |
| `cover_action` | Cover action | none | Disabled by default |
| `second_ac` | Second AC | none | Disabled by default |
| `learning_suggestion` | Learning suggestion | none | Diagnostic |
| `external_change` | External change | none | Diagnostic |
| `sensors_stalled` | Stalled sensors | none | Diagnostic |
| `safety_output_pct` | Safety output | % | Disabled by default |
| `economizer` | Economizer | none | Disabled by default |
| `motion_preset` | Motion preset | none | Disabled by default |
| `shed` | Loads shed | none | Disabled by default |
| `restored` | Load switched back on | none | Disabled by default |
| `mpc_plan` | MPC plan steps | none | Disabled by default |
| `mpc_status` | MPC plan effect | none | Diagnostic |
| `mpc_heat_steps` | MPC heat plan steps | none | Disabled by default |
| `solar_scale` | Solar scale | none | Disabled by default |
| `vacation` | Vacation mode | none | Diagnostic |
| `heat_source` | Heat source | none | Diagnostic |
| `boiler_demand` | Boiler demand | none | Disabled by default |
| `whole_house_fan` | Whole-house fan | none | Diagnostic |
| `heating_failure` | Heating failure | none | Diagnostic |
| `house_cooling` | House cooling | none | Disabled by default |
| `heater_pid_output` | Heater PID output | % | Disabled by default |
| `heater_output_pct` | Heater output | % | Disabled by default |
| `tpi_output_pct` | TPI output | % | Disabled by default |
| `preheat` | Preheat | none | Disabled by default |
| `heat_up_min` | Heat-up time | min | Disabled by default |
| `cool_on_above_c` | Cool above | °C | Primary |
| `heat_on_below_c` | Heat below | °C | Primary |
| `backtest_runs` | Backtest runs | none | Disabled by default |
| `trv_offset_k` | TRV offset | K | Disabled by default |
| `room_fit` | Two-device room fit | none | Disabled by default |
| `forecast_days` | Forecast day counts | none | Disabled by default |
| `dispatch` | Room dispatch | none | Diagnostic |
| `minutes_low` | Minutes to setpoint, low | min | Primary |
| `minutes_high` | Minutes to setpoint, high | min | Primary |
| `rooms_max_age_s` | Room reading age | s | Disabled by default |

Every row after `forecast_error_min` is one entry of `STATUS_KEYS` in `sensor.py`. A list or dict value shows as JSON text cut to 255 characters in the state, and the full value is in the `value` attribute. A key the coordinator did not publish this tick reads as `unknown`.

## Groups

The Group column shows where Home Assistant lists the sensor.

- Primary: 11 sensors with no entity category, all enabled. They show on the device page and in dashboards by default.
- Diagnostic: 23 sensors with the entity category `diagnostic`, all enabled. They show under **Diagnostic** on the device page. Home Assistant leaves them out of the default dashboard and out of area and voice exposure. Several are the ones to read when something is wrong: `sensors_stalled`, `heating_failure`, `window_advice`, `mould_risk`, `dispatch`, `precool_status` and `mpc_status`.
- Disabled by default: 32 sensors with the entity category `diagnostic`, created disabled. They hold internal detail such as PWM state, PID and TPI outputs, plan steps and fit internals. To use one, open it in **Settings > Devices & services > Entities** and turn on **Enabled**. The entry reloads, and the sensor then reads like any other.

The coordinator publishes every key whether or not its sensor is enabled. The attributes in the Attributes table below sit on the Status and Forecast error sensors, which are primary.

## Control status keys

| Key | Content |
| :-- | :-- |
| `cool_on_above_c` | The room temperature above which the AC cools, in °C. Updated every tick. |
| `heat_on_below_c` | The room temperature below which the heater heats, in °C. It is the heat target minus 0.5 K. Updated every tick. |
| `dispatch` | The devices chosen from `fleet`. A dict with `cool` and `heat` (lists of `device_id`), `power_kw`, `devices` (per device: `rate_k_per_h_per_kw`, `rate_source` as `fitted`, `entered` or `missing`, `power_source` as `sensor` or `rated`, `power_kw`) and, with control on, `waiting` (devices held by a minimum time or the compressor group). Empty lists when no fleet is set. |
| `precool_status` | Text that says why a pre-cool plan exists or not, for example `start in 25 min`, `no schedule entity`, `no room model yet`, `no running power learned yet` or `no deadline: next_event is in the past`. |
| `mpc_status` | Text for the cooling plan: `no plan: normal decision`, or `plan step 0 run` or `plan step 0 idle` followed by `decision unchanged` or `changed the decision to idle`. Set only on a tick where control runs. |
| `samples_heat` | Count of stored 5-minute intervals where the AC was in heat mode. The cooling fit leaves them out. |
| `samples_dry` | Count of stored 5-minute intervals where the AC was in dry mode. The cooling fit leaves them out. |

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

