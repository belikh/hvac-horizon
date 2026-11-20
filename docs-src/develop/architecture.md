---
title: Architecture
summary: The modules of the HVAC Horizon package, the control tick from input to sensor, and the functions that each step calls.
section: develop
order: 10
audience: developers
status: draft
---

## Module map

Every module is in `custom_components/hvac_horizon/`.

| Module | Job |
| :-- | :-- |
| `__init__.py` | Registers the services, sets up the coordinator and the sensor platform, and unloads the entry. |
| `config_flow.py` | The setup and reconfigure forms. `_schema()` lists every key, selector and default. |
| `const.py` | Config keys, defaults and limits. |
| `coordinator.py` | `HorizonCoordinator`. Samples the room, refits the model, plans the target, drives the devices and publishes the result. |
| `collector.py` | `Entry`, one reading. Sample history, running power, the cycle check and CSV export. |
| `model.py` | `fit()` learns the room coefficients `k`, `c` and `g` from readings. |
| `tpi.py` | `learn_room_rates()` and the online gains `adjust_cool_gain()` and `adjust_heat_gain()`. `tpi_on_percent()` sets the TPI output. |
| `devices.py` | Room model with one coefficient per device. `fit_room()` and `forecast_fit()`. |
| `forecast.py` | `forecast()` gives time, energy and cost to reach a setpoint. `price_at()` reads the price slots. |
| `control.py` | `precool_start_in_min()` picks the cheapest start that reaches the target by the deadline. |
| `decide.py` | `decide()` returns one AC action for a tick. `cool_above_c()` gives the cooling threshold. |
| `features.py` | Pure decision functions: presets, stall rule, inhibit, lock, minimum times, PWM, price shift, valve, shed and PID gains. Importing it loads `__init__.py`. |
| `pid.py` | `Pid`, the heater PID controller. |
| `autotune.py` | `RelayTuner`, the relay test and the Ziegler-Nichols gains. |
| `backfill.py` | `backtest()` replays cool-downs. `accuracy_pairs()` and `median_abs_error()` summarise the runs. |
| `investigator.py` | `investigate()` gives the rule-based answer. `CallBudget` limits model calls per day. |
| `solar_model.py` | Sun position and irradiance on a vertical window, for solar gain. |
| `sensor.py` | `STATUS_KEYS` and the sensor entities that show the result. |
| `accuracy_api.py` | The JSON view and the sidebar panel for the accuracy page. |
| `diagnostics.py` | The diagnostics download. Entity IDs are redacted. |
| `frontend/accuracy.html` | The static accuracy page. It reads the JSON view. |
| `translations/en.json` | Form labels, error texts and repair issue texts. |
| `services.yaml` | Service definitions for the Home Assistant UI. |
| `manifest.json` | Domain, version, dependencies (`http`) and requirements (none). |

## The control tick

`HorizonCoordinator` is a `DataUpdateCoordinator`. Its update interval is `SAMPLE_INTERVAL_S`, 300 seconds. Each tick runs `_async_update_data()`. The steps run in this order.

1. **Refresh the weather.** `_refresh_weather()` runs every 30 minutes. It reads the daily highs from the weather entity.
2. **Read and store.** `_read_inputs(now)` builds an `Entry`. `_take_interval_mean()` supplies the mean power of the interval. The entry is appended and capped at `MAX_ENTRIES`. `_learn_cool_gain()` and `_learn_heat_gain()` update the online gains.
3. **Refit.** Every 12 readings, or when no fit exists, `_refit()` runs `learn_room_rates()`, `fit()` and `backtest()`.
4. **Forecast.** `_forecast(now)` calls `forecast()` and sets `minutes`, `energy_kwh`, `cost`, `status`, `reason` and the accuracy keys.
5. **Inputs.** `_stalled_sensors()` and `_report_stalls()` check the sensors. The tick then sets the day type, the night plan, occupancy, solar gain, window advice, the economizer phase, the boiler demand and the motion preset. It also sets the comfort band, mould, compliance, heatwave, fan speed and drying keys. The price fallback comes from `stale_price_fallback()`.
6. **Window actuator.** `_drive_window_actuator(result)` opens or closes the actuator on the window or economizer advice.
7. **Plan the target.** `_plan_control(now, result)` sets the preset and the target. It uses occupancy (`_occupancy_mode()`), the vacation switch, the price shift (`price_shifted_target()`), the schedule (`scheduled_preset()`) and the timed preset (`_effective_preset()`). It builds `Settings`, the heat target (`heat_setpoint()`), and the thresholds `_cool_above_c`, `_heat_target_c` and `_heat_on_below_c`.
8. **Model plans.** `mpc_plan()` and `mpc_heat_plan()` run when a fit and price slots exist. Their steps go to `mpc_plan` and `mpc_heat_steps`.
9. **Publish cooling and gates.** `_publish_cooling(result)` sets the cooling keys. `_heater_gate_closed(result)` sets `_heater_gated`.
10. **Heater output.** The tick computes the heater output. It uses TPI (`tpi_on_percent()`) or PID (`Pid`, with `_heater_gain_values()`). The output goes to `_pwm_output_pct` and is published as `heater_output_pct`.
11. **Valve.** `trv_offset()` and the peer valve request set the opening. `valve_opening_pct()` converts the room gap to percent.
12. **Cover and load shedding.** Solar gain drives `cover_action()`. `shed_plan()` and `shed_restore()` use the whole-house power.
13. **AC control.** `_async_control(now, result)` runs only with control on. It checks the lock, the inhibit window, the stall and the keep-alive. Then `decide()` picks the action, and `_precool_start()` supplies the pre-cool start from `control.precool_start_in_min()`. The fan, humidifier, dehumidifier, boiler and whole-house fan switches follow. The second AC follows. Then the heat path runs. `minimum_time_ok()` and `_group_start_ok()` hold a mode change. The change goes out through `_write_setpoint()` or `set_hvac_mode`.
14. **Heater switch.** `_async_heater_step(now, result)` runs in a `finally` block, so it runs even when the AC step raises. It applies the gates, the stall fallback (`safety_output()`) and the PWM window (`_pwm_step()`). It then runs `_heating_check()`, and `_drive_heater_switch()` when control and a heater switch are set.
15. **Investigation and valve drive.** `_publish_investigation()` sets the investigation keys. `_drive_valve()` sends the valve opening last.
16. **Publish.** The result dict becomes `coordinator.data`. The sensors in `sensor.py` read it from there.

## Key functions

| Function | File | Role |
| :-- | :-- | :-- |
| `HorizonCoordinator._async_update_data()` | `coordinator.py` | Runs one tick, steps 1 to 16. |
| `HorizonCoordinator._plan_control()` | `coordinator.py` | The preset and the single target for the tick. |
| `HorizonCoordinator._async_control()` | `coordinator.py` | The AC action. Runs with control on. |
| `HorizonCoordinator._async_heater_step()` | `coordinator.py` | The heater gates, PWM and switch. |
| `decide()` | `decide.py` | Priority of window, away, pre-cool and comfort. Pure. |
| `forecast()` | `forecast.py` | Integrates the room temperature to the target. Step is 1 minute. |
| `learn_room_rates()` | `tpi.py` | Fits `k` and `g` from AC-off readings, then `c` from AC-on readings. |
| `fit()` | `model.py` | Least squares fit of `k`, `c` and `g`. |
| `has_enough_cycles()` | `collector.py` | Checks the 3 hour and 3 run rule before a fit. |
| `backtest()` | `backfill.py` | Replays cool-downs with a fit on earlier readings only. |
| `STATUS_KEYS` | `sensor.py` | Maps each published key to its sensor name. |

## Adding a published key

Add the key to the result dict in `coordinator.py`. Add it to `STATUS_KEYS` in `sensor.py`, or return it from `extra_state_attributes`. `tests/test_published_keys.py` fails when a key has neither. See [[develop/test-and-release]].

:::see-also
- [[develop/test-and-release]]: how to run the tests and the release checks.
- [[concepts/design]]: the room model and the assumptions of each forecast.
:::
