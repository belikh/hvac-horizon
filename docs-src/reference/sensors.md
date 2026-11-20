---
title: Sensors
summary: Every sensor and status attribute that HVAC Horizon publishes, with its key and unit.
section: reference
order: 2
audience: users
status: draft
---

## Sensors

Each entry creates the sensors below. The entity ID is `sensor.<name>` for the entry's name. The key is the suffix that the integration uses in the unique ID, and the attribute key where a value is read from the `status` sensor.

| Key | Name | Unit |
| :-- | :-- | :-- |
| `minutes_to_setpoint` | Minutes to setpoint | min |
| `energy_kwh` | Energy to setpoint | kWh |
| `cost` | Cost to setpoint | none |
| `status` | Status | none |
| `samples` | Samples collected | none |
| `forecast_error_min` | Forecast error | min |
| `day_type` | Day type | none |
| `night_plan` | Night plan | none |
| `daily_briefing` | Daily briefing | none |
| `window_advice` | Window advice | none |
| `ventilation` | Ventilation | none |
| `ventilation_rate` | Ventilation rate | none |
| `humidity` | Humidity action | none |
| `drying_needed` | Drying needed | none |
| `mould_risk` | Mould risk | none |
| `compliance_pct` | Comfort compliance | % |
| `heatwave` | Heatwave | none |
| `fan_speed` | Fan speed | none |
| `price_used_per_kwh` | Price used per kWh | none |
| `occupancy` | Occupancy | none |
| `solar_gain_kw` | Solar gain | kW |
| `band_action` | Band action | none |
| `target_c` | Target temperature | °C |
| `preset` | Preset | none |
| `heating_allowed` | Heating allowed | none |
| `heater_feedback` | Heater feedback | none |
| `heater_pwm_on` | Heater PWM on | none |
| `valve_opening_pct` | Valve opening | % |
| `cover_action` | Cover action | none |
| `second_ac` | Second AC | none |
| `learning_suggestion` | Learning suggestion | none |
| `external_change` | External change | none |
| `sensors_stalled` | Stalled sensors | none |
| `safety_output_pct` | Safety output | % |
| `economizer` | Economizer | none |
| `motion_preset` | Motion preset | none |
| `shed` | Loads shed | none |
| `restored` | Load switched back on | none |
| `mpc_plan` | MPC plan steps | none |
| `mpc_heat_steps` | MPC heat plan steps | none |
| `solar_scale` | Solar scale | none |
| `vacation` | Vacation mode | none |
| `heat_source` | Heat source | none |
| `boiler_demand` | Boiler demand | none |
| `whole_house_fan` | Whole-house fan | none |
| `heating_failure` | Heating failure | none |
| `house_cooling` | House cooling | none |
| `heater_pid_output` | Heater PID output | % |
| `heater_output_pct` | Heater output | % |
| `tpi_output_pct` | TPI output | % |
| `preheat` | Preheat | none |
| `heat_up_min` | Heat-up time | min |
| `cool_on_above_c` | Cool above | °C |
| `heat_on_below_c` | Heat below | °C |
| `backtest_runs` | Backtest runs | none |
| `trv_offset_k` | TRV offset | K |
| `room_fit` | Two-device room fit | none |
| `forecast_days` | Forecast day counts | none |

The `status` sensor carries the status values as attributes. A list or dict shows its content in `value`, cut to 255 characters. Use the attribute key from this table to read the value.

## Attributes

These attributes sit on the entity named in the Entity column. They are read from the coordinator's last result.

| Attribute | Entity | Content |
| :-- | :-- | :-- |
| `reason` | Status | The rule that holds the current decision. |
| `k_per_h` | Status | Room cooling rate from the AC-off fit, in K per hour. |
| `c_k_per_kw` | Status | Cooling effect of the AC, in K per hour per kW. |
| `g_k_per_h` | Status | Outdoor exchange rate, in K per hour. |
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

