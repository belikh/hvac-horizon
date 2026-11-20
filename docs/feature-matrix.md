# Feature matrix against the four competitors

Sources: VT = Versatile Thermostat, RM = RoomMind, CA = ClimateAdvisor, PID = smart_thermostat (ScratMan).

Status values:
- `drives`: a call writes to a device or a setpoint through the control path. Gated by the control option, the lock and the inhibition where they apply.
- `published`: computed each cycle and shown on a sensor or a status attribute. Nothing acts on it.
- `pure`: a tested function with no caller in the integration. The dead-code test rejects these, so no row carries this status.
- `missing`: not built.
- `undecided`: the feature writes no device and has no sensor or attribute, so none of the four values fits. The cell names the mechanism. The footer lists these rows and the question that settles each one.

Each status cell cites the code as `file:line`. Line numbers refer to commit a073df8.

## Control and climate

| Feature | Source | Status |
| :-- | :-- | :-- |
| Set HVAC mode and setpoint on a climate entity | VT, RM, CA, PID | drives (coordinator.py:1233, 1247 `set_hvac_mode` and `set_temperature`; gated by control, lock at 1120, inhibit at 1133) |
| Observe-only mode (no commands) | CA | published (control off: status sensors only; the relay test still switches the heater, coordinator.py:418) |
| Proportional or TPI power control | VT, PID | drives (PID only, no TPI: output set at coordinator.py:956-960, switch driven at 534 with control on; gate at 477) |
| Auto-tuner from a relay test (Ziegler-Nichols) | PID | drives (heater switch during the test, coordinator.py:405-421; skips the control, lock and inhibit gates; gains shown on the status attribute, sensor.py:110, not applied to the PID) |
| Heater switch output with PWM duty cycle | PID, VT | drives (window edges at coordinator.py:492-523, switch command at 534; gated by control) |
| Output polarity (inverted switch) | PID | drives (`apply_polarity` at coordinator.py:539 for PWM and 415 for the relay test) |
| Output clamps from entities | PID | drives (`set_heater_output`, coordinator.py:673-678, clamps to the config minimum and maximum, not to entity states) |
| Boost forces PID auto or suspends it | PID | drives (boost holds output at 100 %: coordinator.py:959 and 964; the heater gate at 941 still applies) |
| Minimum on and off times | VT | drives (first AC only: check at coordinator.py:1211, before the climate write) |
| Keep-alive: resend the last setpoint | VT | drives (`set_temperature` at coordinator.py:1145, after the inhibit check at 1133) |
| Force-off on sensor stall | VT, PID | drives (heater only: stall gate at coordinator.py:485 sets output to 0 at 941; the AC is not stopped) |
| Inhibition: no commands for a period after a manual change | VT | undecided (gate only: armed at coordinator.py:1124-1131, checked at 1133, no device call of its own) |
| Lock with PIN | VT | undecided (gate only: `set_lock` at coordinator.py:375, checked at 1030, 1046, 1058, 1120) |
| Repair for incorrect device state | VT | undecided (repair issue only: coordinator.py:756-766, no sensor) |
| External setpoint change detection | RM | undecided (sensor `external_change` at sensor.py:42, and it arms the inhibit at coordinator.py:1124-1131) |

## Presets and schedules

| Feature | Source | Status |
| :-- | :-- | :-- |
| Comfort, eco, boost, away presets | VT, RM, CA, PID | drives (away uses `away_c`; eco uses the comfort setpoint, decide.py:58-61; boost at coordinator.py:959) |
| Preset temperatures from config | VT, PID | missing (the flow stores `eco_c` and `boost_c`, config_flow.py:185-188; coordinator.py:1102 builds `Settings` without them) |
| Preset sync (target moves when preset changes) | PID | drives (target recomputed each cycle, coordinator.py:1097-1110, written at 1247) |
| Timed preset (duration then revert) | VT | drives (`start_timed_preset`, coordinator.py:365; applied at 1098) |
| Scheduled presets by time of day | RM, CA | drives (`schedule_blocks`, coordinator.py:1097; empty by default) |
| Price-shifted target | RM | drives (`price_shifted_target`, coordinator.py:1083) |
| Price fallback when sensor drops out | RM | published (`price_used_per_kwh`, sensor.py:29; the fallback does not change the target) |
| Vacation mode holds vacation temperature | RM | drives (`vacation_target`, coordinator.py:1080) |
| Comfort-energy weight blend | RM | missing (code removed in a073df8) |
| Outdoor cooling limit | RM | drives (cooling off below 16 C, decide.py:79) |
| Heating above outdoor limit blocked | RM | drives (`heating_allowed` at coordinator.py:885 gates the heater at 477-486 and the boiler at 1190; limit 22 C, features.py:62) |

## Occupancy, windows and presence

| Feature | Source | Status |
| :-- | :-- | :-- |
| Window or door pauses control after a delay | VT, RM, CA | drives (AC off after the delay, decide.py:77; applied at coordinator.py:1172) |
| Presence sensor to home or away | VT, RM, CA | drives (away setback through `someone_home`, coordinator.py:1076 and 1110) |
| Presence from several sources | RM | missing (one presence entity only, coordinator.py:655-662; occupancy is published, sensor.py:30) |
| Motion sensor presets with delays | VT | published (`motion_preset`, sensor.py:46; setpoint not changed, coordinator.py:912-917) |
| Occupancy sensor drives eco | RM | missing (`eco_c` is never passed to `Settings`, coordinator.py:1102; occupancy is published, sensor.py:30) |
| Event notifications for window pauses and lock changes | CA | undecided (persistent notifications only: coordinator.py:1013-1023 and 1160) |

## Learning and forecasting

| Feature | Source | Status |
| :-- | :-- | :-- |
| Learned room model (k, c, g), with fit guard | RM, CA | drives (the fit sets the pre-cool start, coordinator.py:1163 and 1312-1340, decide.py:88; k, c, g shown as status attributes, sensor.py:107-109) |
| Multi-device room model (cooler and heater) | ours | missing (code removed in a073df8) |
| Time, energy and cost to setpoint | ours | published (sensor.py:77-79) |
| Forecast error from backtest | ours | published (sensor.py:82; synthetic data; live error still unknown until real cool-downs are scored) |
| Thermal rate learning (Auto TPI) | VT | missing (no TPI coefficients are learned; the k, c, g model is its own row) |
| Learning suggestion | CA | published (`learning_suggestion`, sensor.py:41) |
| Accept or dismiss learning suggestions | CA | undecided (`respond_suggestion` changes the stored baseline only: coordinator.py:680-687; no device write, no sensor) |
| Model predictive control | RM | published (`mpc_plan`, sensor.py:48; the sensor shows the step count, not the plan, because lists show their length: sensor.py:141) |
| Adaptive preheat or pre-cool start | RM, CA | drives (schedule deadline only: coordinator.py:1312-1340, decide.py:88) |
| Heating failure detection | VT | published (`heating_failure`, sensor.py:54; repair issue at coordinator.py:464-475; the heater is not turned off) |
| Heater switch feedback | PID | published (`heater_feedback`, sensor.py:36; `not_configured` until the first heater command, coordinator.py:1004, features.py:196) |
| Recorder history backfill at startup | ours | undecided (adds readings to the model at startup: coordinator.py:261-288; no device write, no per-cycle output) |
| Recorder history export (CSV) | VT | undecided (service response only: __init__.py:52-54) |
| What-if forecast service | CA | undecided (service response only: coordinator.py:352) |

## Weather and environment

| Feature | Source | Status |
| :-- | :-- | :-- |
| Weather forecast input | RM, CA | published (forecast highs feed `day_type` and `heatwave` only: coordinator.py:578-600, sensor.py:17 and 27) |
| Day class from outdoor reading | CA | published (`day_type`, sensor.py:17) |
| Heatwave streak | CA | published (`heatwave`, sensor.py:27; uses the outdoor history, or the forecast when a weather entity is set) |
| Forecast day counts | CA | missing (code removed in a073df8) |
| Daily briefing | CA | published (`daily_briefing`, sensor.py:19; set only with control on) |
| Night plan (pre-cool or hold) | CA | published (`night_plan`, sensor.py:18) |
| Whole-house fan control | CA | drives (fan switch at coordinator.py:1200, after the lock and inhibit checks) |
| Whole-house fan safety rule | CA | drives (`whole_house_fan_allowed` at coordinator.py:1194-1200, same switch) |
| Natural ventilation timing | CA | published (`window_advice`, sensor.py:20) |
| Economizer (two-phase window cooling) | CA | published (`economizer`, sensor.py:45; windows and fan are not driven) |
| Solar gain from irradiance and window area | RM | published (`solar_gain_kw`, sensor.py:31; needs an irradiance sensor and a window area, coordinator.py:633-640) |
| Solar sensor learning | RM | published (`solar_scale`, sensor.py:49; ratio of modelled to measured gain, coordinator.py:860-864; a value appears once a measured gain of at least 0.05 kW is seen; nothing reads it) |
| Cover or blind control | RM | drives (cover open or close at coordinator.py:978 and 1063, gated by control, lock and inhibit) |

## Air quality, humidity and mould

| Feature | Source | Status |
| :-- | :-- | :-- |
| Ventilation on CO2 or PM2.5 (on-off) | RM | drives (fan switch at coordinator.py:1186, inside the control path) |
| Proportional ventilation rate from CO2 | RM | published (`ventilation_rate`, sensor.py:22; coordinator.py:935) |
| Room fan control | RM | drives (same fan switch, coordinator.py:1186) |
| Humidity band | RM | drives (dehumidifier and humidifier switches, coordinator.py:1187-1188) |
| Dehumidify with the AC's dry mode | RM | drives (`set_hvac_mode` dry when no dehumidifier is set, coordinator.py:1221) |
| Drying decision (no cooling needed) | CA | published (`drying_needed`, sensor.py:24; coordinator.py:928) |
| Mould risk from surface humidity | RM | published (`mould_risk`, sensor.py:25) |
| Mould prevention raises target | RM | drives (`mould_target` at coordinator.py:1092, written at 1247) |
| AC coil drying after heating | RM | drives, opt-in (`coil_dry` key in the stored entry data, not in the config flow; dry mode at coordinator.py:1253-1280) |
| Auto fan speed from temperature gap | VT | published (`fan_speed`, sensor.py:28) |

## Valves, radiators and multi-device

| Feature | Source | Status |
| :-- | :-- | :-- |
| TRV or valve control | VT, RM | drives (`number.set_value` on the valve, coordinator.py:1040; gated by control, lock and inhibit at 1025-1041) |
| Modulating valve opening | RM, VT | drives (same call, coordinator.py:1010 and 1040) |
| Valve anti-seize exercise | RM | missing (code removed in a073df8) |
| TRV offset learning | RM | missing (code removed in a073df8) |
| Radiator and underfloor residual heat | RM | missing (code removed in a073df8) |
| Heat source selection (boiler or AC) | RM | drives (boiler demand from the source, coordinator.py:881-884, boiler switch at 1190; the "ac" source sends no heat command, because no heat mode is written) |
| Central boiler activation | VT | drives (boiler switch at coordinator.py:1190, after the lock and inhibit checks) |
| Compressor groups (several AC on one compressor) | RM | drives (second AC starts staggered by 5 minutes, `stagger_allowed` at coordinator.py:1296; same room only) |
| Second AC under the same room (multi-device control) | ours | drives (coordinator.py:1202 and 1283-1310; minimum on and off times do not apply, the check at 1211 covers the first AC) |
| Multi-room coordination | VT, RM | drives (house-wide home or away from `set_house_mode`, coordinator.py:1076; no other cross-room control) |
| Power shedding per device | VT | drives (switch `turn_off` at coordinator.py:1042-1051; turns loads off only) |
| Central power limit | VT | drives (whole-house power over `CONF_SHED_MAX_W`, coordinator.py:979-995, same path as shedding) |

## Safety, diagnostics and services

| Feature | Source | Status |
| :-- | :-- | :-- |
| Sensor stall detection | VT | published (`sensors_stalled`, sensor.py:43; stall test at features.py:205) |
| Repair issue and notification for stalled sensors | CA | published (stalled list on sensor.py:43; repair and notification at coordinator.py:725-740) |
| Safety output on stall | VT | published (`safety_output_pct`, sensor.py:44; not used to drive a device) |
| Diagnostics export with redaction | VT, CA | undecided (Home Assistant diagnostics download: diagnostics.py:1-25; no device write, no sensor) |
| Services: reset learning, export samples | VT, CA | undecided (service calls: coordinator.py:386-400; no device write, no sensor) |
| Start auto-tune service | PID | drives (`start_autotune`, __init__.py:57-58 and coordinator.py:400-421; heater switch during the test, no control gate) |
| Accuracy page (prediction versus actual) | CA | published (`forecast_error_min`, sensor.py:82; no separate page) |
| Notifications on events | CA | undecided (persistent notifications: coordinator.py:1013-1023; no device write, no sensor) |
| AI investigator and budget | CA | missing (not built; needs an external model service and a spend budget) |

## Unbuilt items

Rows marked `missing` (11):

- Preset temperatures from config
- Presence from several sources
- Occupancy sensor drives eco
- Thermal rate learning (Auto TPI)
- Multi-device room model (code removed in a073df8)
- Forecast day counts (code removed in a073df8)
- Comfort-energy weight blend (code removed in a073df8)
- Valve anti-seize exercise (code removed in a073df8)
- TRV offset learning (code removed in a073df8)
- Radiator and underfloor residual heat (code removed in a073df8)
- AI investigator and budget

Rows per status (90 rows): drives 41, published 26, pure 0, missing 11, undecided 12.

Rows marked `undecided` (12) need a ruling on whether a service response, a repair issue, a notification or a gate counts as drives or published: Inhibition, Lock with PIN, Repair for incorrect device state, External setpoint change detection, Accept or dismiss learning suggestions, Recorder history backfill at startup, Recorder history export (CSV), What-if forecast service, Event notifications for window pauses and lock changes, Diagnostics export with redaction, Services: reset learning and export samples, Notifications on events.

The live forecast error is still unknown until real cool-downs are scored.
