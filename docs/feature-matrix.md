# Feature matrix against the four competitors

Sources: VT = Versatile Thermostat, RM = RoomMind, CA = ClimateAdvisor, PID = smart_thermostat (ScratMan).

Checked against commit 89d0e55. Line numbers refer to that commit. Each status comes from the code at that commit.

Status values:
- `drives`: a call writes a device or a setpoint through the control path. The cell cites the call site and the gates.
- `published`: a sensor or status attribute shows the value. Nothing acts on it. The cell cites the sensor.py line.
- `control`: a gate, service, repair issue, persistent notification, event or user action. It acts without writing a device setpoint. The cell cites the call site and the test. `test: none` means no test covers it.
- `pure`: a function with no caller. `tests/test_no_dead_code.py` fails on it. No row carries this status.
- `missing`: not built. No row carries this status.

## Control and climate

| Feature | Source | Status |
| :-- | :-- | :-- |
| Set HVAC mode and setpoint on a climate entity | VT, RM, CA, PID | drives (coordinator.py:1361 and 1368 `set_hvac_mode`; 1372 and 1426-1428 `set_temperature`; gated by control at 1102, lock at 1241, inhibit at 1256) |
| Observe-only mode (no commands) | CA | published (control off: `_async_control` does not run, coordinator.py:1102; the status sensors show the values, sensor.py:19-64; test: test_control_off_sends_no_climate_calls, tests/ha/test_setup.py:184) |
| Proportional power control (PID). No TPI controller | VT, PID | drives (`Pid.update` at coordinator.py:1051-1054, output to the switch at 645; gated by control at 616 and the heater gate at 1030) |
| Auto-tuner from a relay test (Ziegler-Nichols) | PID | drives (the heater follows the test at coordinator.py:626-630 and 645; the gains are set on the PID at 460-462, stored at 463, and read back at 1037-1041; sensor.py:117 shows them; test: test_relay_test_gains_reach_the_heater_pid_and_survive_restart, tests/ha/test_setup.py:1573) |
| Heater switch output with PWM duty cycle | PID, VT | drives (window edges at coordinator.py:558-583, switch command at 602-608 and 645; gated by control at 616 and the heater gate at 568) |
| Output polarity (inverted switch) | PID | drives (`apply_polarity` at coordinator.py:607, used by the PWM path and the relay test) |
| Output clamps from entities | PID | drives (`set_heater_output` at coordinator.py:757-762 clamps to CONF_OUTPUT_MIN and CONF_OUTPUT_MAX; the clamped output reaches the switch at 645) |
| Boost forces heater output to 100 % | PID | drives (`boost_output` at coordinator.py:1054 and 1059 returns 100 in boost, features.py:770-779; the heater gate at 1030 still applies) |
| Minimum on and off times | VT | drives (coordinator.py:1343-1347 before the AC mode write; 1457-1459 before a heat start; the first AC only, because the second AC is driven at 1331 first) |
| Keep-alive: resend the last setpoint | VT | drives (`set_temperature` at coordinator.py:1269-1271, after the inhibit check at 1256-1259) |
| Force-off on sensor stall | VT, PID | drives (AC: stalled action OFF at coordinator.py:1290-1292, the stall bypasses the inhibit at 1257, the OFF write at 1361; heater: stall term in `_heater_gate_closed` at 551 sets the output to 0 at 624-625; tests: test_stalled_sensor_turns_the_ac_off, tests/ha/test_setup.py:1607, and test_stalled_sensor_turns_the_ac_off_inside_the_inhibit_window, tests/ha/test_setup.py:1465) |
| Inhibition: no commands for a period after a manual change | VT | control (gate: armed at coordinator.py:1245-1251, checked at 1256-1259, 1029, 1138, 1163 and 1175; test: test_manual_setpoint_is_overwritten_after_the_inhibit_ends, tests/ha/test_setup.py:1412) |
| Lock with PIN | VT | control (`set_lock` at coordinator.py:410-419; service at __init__.py:60-63; checked at 551, 1136, 1161, 1173 and 1241; test: test_lock_needs_pin_and_blocks_control, tests/ha/test_setup.py:524) |
| Repair for incorrect device state | VT | control (repair issue `mode_mismatch` at coordinator.py:840-849; test: test_mode_mismatch_raises_and_clears_repair_issue, tests/ha/test_setup.py:609) |
| External setpoint change detection | RM | control (detected at coordinator.py:833-838; arms the inhibit gate at 1245-1251; the flag shows on sensor.py:45; test: test_manual_setpoint_is_overwritten_after_the_inhibit_ends, tests/ha/test_setup.py:1412) |

## Presets and schedules

| Feature | Source | Status |
| :-- | :-- | :-- |
| Comfort, eco, boost, away presets | VT, RM, CA, PID | drives (Settings at coordinator.py:1221-1228 feed `decide` at decide.py:51-66; the action is written at 1361-1372. Away uses eco_c when set, else away_c, decide.py:53-54. Eco and boost fall back to comfort when unset, decide.py:60-63) |
| Preset temperatures from config | VT, PID | drives (flow fields eco_c, config_flow.py:200, and boost_c, config_flow.py:206, reach Settings at coordinator.py:1226-1227; eco_c is unset by default, so an empty house uses away_c; test: test_comfort_energy_weight_moves_the_cooling_target, tests/ha/test_setup.py:1726, sets eco_c) |
| Preset sync (target moves when preset changes) | PID | drives (target from `cool_above_c` at coordinator.py:1231, written at 1372) |
| Timed preset (duration then revert) | VT | drives (`start_timed_preset` at coordinator.py:400-404; applied at 1217 through `_effective_preset` at 406-408; service at __init__.py:68-71; test: test_timed_preset_overrides_then_reverts, tests/ha/test_setup.py:633) |
| Scheduled presets by time of day | RM, CA | drives (`scheduled_preset` at coordinator.py:1212-1217; empty by default; test: test_stored_schedule_block_sets_the_preset, tests/ha/test_setup.py:1399) |
| Price-shifted target | RM | drives (`price_shifted_target` at coordinator.py:1202-1207, written at 1372) |
| Price fallback when sensor drops out | RM | published (`stale_price_fallback` at coordinator.py:1018-1023; the target reads the live price at 1197, so the fallback does not change it; sensor.py:32) |
| Vacation mode holds vacation temperature | RM | drives (`vacation_target` at coordinator.py:1198-1202, written at 1372; flow field vacation_c, config_flow.py:170) |
| Comfort-energy weight blend | RM | drives (coordinator.py:1192-1196 moves the comfort target toward eco_c by the weight; flow field comfort_energy_weight at config_flow.py:203; tests: test_comfort_energy_weight_moves_the_cooling_target, tests/ha/test_setup.py:1726, and test_comfort_energy_weight_off_keeps_comfort_target, tests/ha/test_setup.py:1742) |
| Outdoor cooling limit | RM | drives (COOL_MIN_OUTDOOR_C at decide.py:16 and the OFF action at decide.py:79-80, written at coordinator.py:1361) |
| Heating above outdoor limit blocked | RM | drives (`heating_allowed` at coordinator.py:974 from `outdoor_allows` at features.py:62 (22 C); gates the heater at 549 and 1030, the boiler at 1319 and the AC heat at 1444) |

## Occupancy, windows and presence

| Feature | Source | Status |
| :-- | :-- | :-- |
| Window or door pauses control after a delay | VT, RM, CA | drives (decide.py:77-78 turns the AC off after the delay; `window_open_min` at coordinator.py:1277-1282 feeds `RoomState` at 1294-1301; the heater gate at 545-547) |
| Presence sensor to home or away | VT, RM, CA | drives (`someone_home` at coordinator.py:1189-1190 selects the away branch, decide.py:81-87; `set_house_mode` at __init__.py:73-80 through `effective_home`, features.py:800-806) |
| Presence from several sources | RM | drives (`presence_sources` at features.py:280 and `presence_mode` at features.py:289 used by `_occupancy_mode`, coordinator.py:738-746; flow field presence, config_flow.py:159, multiple; tests: test_presence_from_two_sources_uses_any_home_and_the_away_delay, tests/ha/test_setup.py:1958, and test_single_presence_string_still_works, tests/ha/test_setup.py:1989) |
| Motion sensor presets with delays | VT | published (`motion_preset` at coordinator.py:998-1006 shows the preset only; sensor.py:49; the setpoint is not changed) |
| Occupancy sensor drives eco | RM | drives (the away branch uses eco_c when set, decide.py:53-54 and 85; occupancy from coordinator.py:1189-1190 and 1231) |
| Event notifications for window pauses and lock changes | CA | control (persistent notifications: window at coordinator.py:1284-1287, lock at 1100; `_notify_transition` at 1119-1129; test: none) |

## Learning and forecasting

| Feature | Source | Status |
| :-- | :-- | :-- |
| Learned room model (k, c, g), with fit guard | RM, CA | drives (the fit sets the pre-cool start: `_precool_start` at coordinator.py:1526-1554, `precool_start_in_min` at control.py:57, and decide.py:88-89 writes the setpoint at 1361-1372; k, c and g show on sensor.py:114-116) |
| Multi-device room model (two ACs) | ours | published (`fit_room` at devices.py:42 run from coordinator.py:471-487 when a second power sensor is set; the forecast keeps the single-device fit; sensor.py:63 `room_fit`; test: test_second_ac_power_gives_a_two_device_room_fit, tests/ha/test_setup.py:1824) |
| Time, energy and cost to setpoint | ours | published (sensor.py:84-86; `forecast` at coordinator.py:1607-1614) |
| Forecast error from backtest | ours | published (sensor.py:89; `backtest` and `median_abs_error` at coordinator.py:509-510 and backfill.py:105. Tests use synthetic samples. Accuracy on real cool-downs is not yet confirmed.) |
| Thermal rate learning (Auto TPI) | VT | published (`learn_tpi` at tpi.py:31-60 learns k and g from AC-off intervals and c from AC-on intervals; the forecast and `what_if` use it at coordinator.py:1576 and 388; attribute `tpi` on sensor.py:122. The pre-cool start still uses the single fit at 1529, so this row drives nothing; test: test_learned_tpi_coefficients_are_published_and_used, tests/ha/test_setup.py:1926) |
| Learning suggestion | CA | published (`learning_suggestion` at coordinator.py:748-755 and 940-944; sensor.py:44) |
| Accept or dismiss learning suggestions | CA | control (`respond_suggestion` at coordinator.py:764-771; service at __init__.py:65-66, registered at 91; test: test_respond_suggestion_dismiss_records_gain, tests/ha/test_setup.py:652) |
| Model predictive control | RM | published (`mpc_plan` at coordinator.py:975-997; sensor.py:51 shows the full plan as JSON content, with the list in the `value` attribute; no control path reads it) |
| Adaptive preheat or pre-cool start | RM, CA | drives (schedule deadline through `_precool_start`, coordinator.py:1288 and 1526-1554, decide.py:88-89; written at 1361-1372) |
| Heating failure detection | VT | published (`heating_failure` at coordinator.py:512-528 and 642; sensor.py:57. The heater is not turned off by this flag) |
| Repair issue for a heater that does not warm the room | VT | control (repair issue `heating_failure` at coordinator.py:530-541, called at 643; test: test_heating_failure_publishes_and_raises_repair, tests/ha/test_setup.py:1159) |
| Heater switch feedback | PID | published (`switch_feedback` at features.py:191-202, set at coordinator.py:1109-1112; sensor.py:39; `not_configured` until the first heater command; test: test_heater_feedback_is_published, tests/ha/test_setup.py:669) |
| Recorder history backfill at startup | ours | published (`async_backfill_from_recorder` at coordinator.py:294-344, called from __init__.py:99; the added readings show in the `samples` sensor at sensor.py:88 and in the k, c, g attributes; tests: test_recorder_history_becomes_samples_at_startup, tests/ha/test_setup.py:1894, and test_backfill_runs_and_reports_count, tests/ha/test_setup.py:545) |
| Recorder history export (CSV) | VT | control (service at __init__.py:52-55, registered at 88; test: test_export_csv_service_returns_csv_header, tests/ha/test_setup.py:556) |
| What-if forecast service | CA | control (service at __init__.py:47-50, registered at 87; `what_if` at coordinator.py:386-398; test: test_what_if_service_returns_forecast, tests/ha/test_setup.py:595) |

## Weather and environment

| Feature | Source | Status |
| :-- | :-- | :-- |
| Weather forecast input | RM, CA | published (`_refresh_weather` at coordinator.py:661-675; the highs feed `day_type` at 792-793 and `heatwave` at 683; no device write) |
| Day class from outdoor reading | CA | published (`day_type` at coordinator.py:790-795 and 937; sensor.py:20) |
| Heatwave streak | CA | published (`_heatwave` at coordinator.py:677-689 and 1012; sensor.py:30; uses the outdoor history, or the forecast when a weather entity is set) |
| Forecast day counts | CA | published (`weather_day_summary` at features.py:424-429; coordinator.py:938; sensor.py:64; content shown as JSON; test: test_forecast_day_counts_are_published_as_content, tests/ha/test_setup.py:1855) |
| Daily briefing | CA | published (`daily_briefing` at coordinator.py:1305-1310, inside `_async_control`, so set only with control on; sensor.py:22) |
| Night plan (pre-cool or hold) | CA | published (`_night_plan` at coordinator.py:773-780 and 939; sensor.py:21) |
| Whole-house fan control | CA | drives (fan switch at coordinator.py:1329, after the lock check at 1241 and the inhibit check at 1256) |
| Whole-house fan safety rule | CA | drives (`whole_house_fan_allowed` at coordinator.py:1322-1329, features.py:562; same switch at 1329) |
| Natural ventilation timing | CA | published (`_window_advice` at coordinator.py:782-788 and 954; sensor.py:23) |
| Economizer (two-phase window cooling) | CA | published (`economizer_phase` at coordinator.py:957-969; sensor.py:48; windows and the fan are not driven) |
| Solar gain from irradiance and window area | RM | published (`_solar_gain` at coordinator.py:716-722 and 948; needs CONF_IRRADIANCE and CONF_WINDOW_AREA_M2; sensor.py:34) |
| Solar sensor learning | RM | published (`learned_solar_scale` at features.py:782, called at coordinator.py:953; sensor.py:52; a value appears once a measured gain of at least 0.05 kW is seen; no code reads it) |
| Cover or blind control | RM | drives (`_drive_cover` at coordinator.py:1168-1179, called at 1082 when solar gain reaches COVER_SOLAR_THRESHOLD_KW; gated by control at 1171, lock at 1173 and inhibit at 1175) |

## Air quality, humidity and mould

| Feature | Source | Status |
| :-- | :-- | :-- |
| Ventilation on CO2 or PM2.5 (on-off) | RM | drives (`air_quality_ventilation` at features.py:77; fan switch at coordinator.py:1315, inside `_async_control`) |
| Proportional ventilation rate from CO2 | RM | published (`ventilation_rate_from_co2` at features.py:486, set at coordinator.py:1024; sensor.py:25) |
| Room fan control | RM | drives (same fan switch, coordinator.py:1315) |
| Humidity band | RM | drives (`humidity_action` at features.py:92; dehumidifier and humidifier switches at coordinator.py:1316-1317) |
| Dehumidify with the AC's dry mode | RM | drives (`set_hvac_mode` dry at coordinator.py:1355-1357 when no dehumidifier is set and the AC is off; skipped while a sensor is stalled) |
| Drying decision (no cooling needed) | CA | published (`drying_needed` at coordinator.py:1017; sensor.py:27) |
| Mould risk from surface humidity | RM | published (`_mould_risk` at coordinator.py:703-714 and 1010; sensor.py:28) |
| Mould prevention raises target | RM | drives (`mould_target` at coordinator.py:1211, written at 1372; features.py:380) |
| AC coil drying after cooling | RM | drives, opt-in (flow field coil_dry, config_flow.py:180, default off; `_drive_coil_dry` at coordinator.py:1467-1495 runs from 1352 only when the AC is off; dry mode for 20 minutes after cooling stops) |
| Auto fan speed from temperature gap | VT | published (`fan_speed_from_gap` at features.py:452, set at coordinator.py:1013-1015; sensor.py:31) |

## Valves, radiators and multi-device

| Feature | Source | Status |
| :-- | :-- | :-- |
| TRV or valve control | VT, RM | drives (`number.set_value` on the valve at coordinator.py:1154; `_drive_valve` gated by control at 1134, lock at 1136 and inhibit at 1138) |
| Modulating valve opening | RM, VT | drives (`valve_opening_pct` at features.py:615, set at coordinator.py:1073; same write at 1154) |
| Valve anti-seize exercise | RM | drives (coordinator.py:1148-1152: after 7 days still, the valve steps 10 points for one cycle; `valve_exercise_due` at features.py:527; test: test_idle_valve_is_stepped_after_a_week_still, tests/ha/test_setup.py:1755) |
| TRV offset learning | RM | drives (`trv_offset` and `corrected_setpoint` at coordinator.py:1066-1072 change the valve target written at 1154; flow field trv_temperature, config_flow.py:163; result shows on sensor.py:62; test: test_trv_offset_corrects_the_valve_target, tests/ha/test_setup.py:1782) |
| Radiator and underfloor residual heat | RM | drives. Sign under review: the gate reads the room minus the residual rise (`residual_correction`, features.py:838-843, applied at coordinator.py:619-622 behind the radiators flow field, config_flow.py:174), so the heater can run longer. Output at 624-625 and 645. Test: test_radiator_residual_heat_holds_the_heater_gate_open, tests/ha/test_setup.py:1802 |
| Heat source selection (boiler or AC) | RM | drives (boiler demand at coordinator.py:973, boiler switch at 1319; AC heat when the source is `ac` and no heater switch is set: `_ac_heat_wanted` at 1433-1448, `set_hvac_mode` heat at 1460; tests: test_ac_heat_source_sends_heat_mode_below_the_heat_threshold, tests/ha/test_setup.py:1656, and test_ac_does_not_heat_when_a_heater_switch_is_set, tests/ha/test_setup.py:1668) |
| Central boiler activation | VT | drives (boiler switch at coordinator.py:1319, after the lock and inhibit checks at 1241 and 1256) |
| Compressor groups (several AC on one compressor) | RM | drives (`stagger_allowed` at features.py:660, used at coordinator.py:1510-1515; the second AC starts 5 minutes after the first; same room only) |
| Second AC under the same room (multi-device control) | ours | drives (`_drive_second_ac` at coordinator.py:1497-1524, called at 1331; the minimum on and off times at 1343-1347 cover the first AC only; flow field climate_2, config_flow.py:128) |
| Multi-room coordination | VT, RM | drives (house-wide home or away from `set_house_mode`, __init__.py:73-80, through `effective_home` at coordinator.py:1190; no other cross-room control) |
| Power shedding per device | VT | drives (plan at coordinator.py:1083-1099, switch `turn_off` at 1166 in `_drive_shed`, gated by control at 1159, lock at 1161 and inhibit at 1163) |
| Central power limit | VT | drives (CONF_SHED_MAX_W read at coordinator.py:1083-1099; `shed_plan` at features.py:631; same path as shedding) |

## Safety, diagnostics and services

| Feature | Source | Status |
| :-- | :-- | :-- |
| Sensor stall detection | VT | published (`sensors_stalled` at sensor.py:46; `_stalled_sensors` at coordinator.py:851-866 reads `last_reported` at 864, so a repeated value is not a stall; `sensor_stalled` at features.py:205; a missing or unavailable sensor is not a stall, 862-863. If every room sensor is unavailable, there is no stall and the AC keeps its state (coordinator.py:1275-1276; test: test_no_room_data_leaves_the_ac_alone, tests/ha/test_setup.py:351). Earlier code read `last_updated`.) |
| Repair issue and notification for stalled sensors | CA | control (`_report_stalls` at coordinator.py:809-824, called at 935; repair issue and persistent notification; test: test_stalled_sensor_raises_repair_issue, tests/ha/test_setup.py:580) |
| Safety output on stall | VT | published (`safety_output` at features.py:212, set at coordinator.py:1113; sensor.py:47; not used to drive a device) |
| Diagnostics export with redaction | VT, CA | control (user-triggered download at diagnostics.py:16-25; `TO_REDACT` at diagnostics.py:13; test: test_diagnostics_redacts_entity_ids, tests/ha/test_setup.py:498) |
| Services: reset learning, export samples | VT, CA | control (services at __init__.py:41-45, registered at 84-85; `reset_learning` at coordinator.py:421-429; `export_samples` at 431-433; tests: test_reset_learning_service_clears_samples, tests/ha/test_setup.py:511, and test_export_samples_service_returns_readings, tests/ha/test_setup.py:568) |
| Start auto-tune service | PID | drives (service at __init__.py:57-58 calls `start_autotune`, coordinator.py:435-438; the test drives the heater only with control on and no gate closed: `_relay_tick` at 440-452, 626-630; test: test_relay_test_does_not_command_the_heater_behind_a_closed_gate, tests/ha/test_setup.py:1547) |
| Accuracy page (prediction versus actual) | CA | published (`forecast_error_min` at sensor.py:89; no separate page) |
| Notifications on events | CA | control (persistent notifications for stalled sensors at coordinator.py:817-823 and for transitions at 1119-1129; test: none) |
| Rule-based AC investigator | CA | published (`investigate` at investigator.py:21-46, called at coordinator.py:1381-1389; the answer and the last actions show in the `investigation` attribute, sensor.py:119; test: test_investigator_answers_why_the_ac_runs, tests/ha/test_setup.py:2005) |
| AI investigator (model call, daily budget) | CA | control (`conversation.process` service call in `_ask_model` at coordinator.py:1399-1413, made when the mode changes; no device write. Gated by flow field ai_conversation, config_flow.py:233, and by `CallBudget` at investigator.py:49-66 with flow field ai_budget_per_day, config_flow.py:234. Answer at sensor.py:120; count at sensor.py:121; test: test_model_calls_follow_mode_changes_and_stop_at_the_budget, tests/ha/test_setup.py:2020) |

## Counts and open items

Counts by status, from the table above (92 rows):
- drives: 48
- published: 30
- control: 14
- pure: 0
- missing: 0

Rows still missing: 0. No feature in the table is unbuilt.

Open items:
- Residual heat sign. The gate reads the room cooler by the residual rise, so the heater can run longer. Decide the sign. Row: Radiator and underfloor residual heat.
- All rooms unavailable. The AC keeps its state, and no stall is raised. Decide whether the AC should stop. Row: Sensor stall detection.
- Relay test on hardware. The relay test is covered by tests only. It has not run on a heater.
- Notifications have no test. Rows: Event notifications for window pauses and lock changes; Notifications on events.
- Forecast error on real cool-downs is not yet confirmed. Row: Forecast error from backtest.
