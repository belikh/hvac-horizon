# Feature matrix against the four competitors

Sources: VT = Versatile Thermostat, RM = RoomMind, CA = ClimateAdvisor, PID = smart_thermostat (ScratMan).

Status values:
- `drives`: changes a device or a setpoint through the control path (gated by control on, lock and inhibit).
- `published`: computed each cycle and shown on a status attribute. Nothing acts on it.
- `pure`: a tested function with no caller in the integration.
- `missing`: not built.

This file reflects the code on `main` at the time of the last update. Update it when a row changes.

## Control and climate

| Feature | Source | Status |
| :-- | :-- | :-- |
| Set HVAC mode and setpoint on a climate entity | VT, RM, CA, PID | drives |
| Observe-only mode (no commands) | CA | drives |
| Proportional or TPI power control | VT, PID | pure (PID class exists; not in the control path) |
| Auto-tuner from a relay test (Ziegler-Nichols) | PID | drives the heater during a test only, not run on hardware |
| Heater switch output with PWM duty cycle | PID, VT | published (pattern only; heater not driven) |
| Output polarity (inverted switch) | PID | missing |
| Output clamps from entities | PID | missing |
| Boost forces PID auto or suspends it | PID | missing |
| Minimum on and off times | VT | drives |
| Keep-alive: resend the last setpoint | VT | drives |
| Force-off on sensor stall | VT, PID | published (safety output; not yet forced off) |
| Inhibition: no commands for a period after a manual change | VT | drives |
| Lock with PIN | VT | drives |
| Repair for incorrect device state | VT | published (repair issue) |
| External setpoint change detection | RM | published (external change) |

## Presets and schedules

| Feature | Source | Status |
| :-- | :-- | :-- |
| Comfort, eco, boost, away presets | VT, RM, CA, PID | drives |
| Preset temperatures from config | VT, PID | drives (eco and boost setpoints) |
| Preset sync (target moves when preset changes) | PID | drives (target recomputed each cycle) |
| Timed preset (duration then revert) | VT | drives (start_timed_preset service) |
| Scheduled presets by time of day | RM, CA | drives (schedule_blocks, empty by default) |
| Price-shifted target | RM | drives |
| Price fallback when sensor drops out | RM | published |
| Vacation mode holds vacation temperature | RM | drives |
| Comfort-energy weight blend | RM | pure |
| Outdoor cooling limit | RM | drives |
| Heating above outdoor limit blocked | RM | drives (boiler demand gated above 22 C outdoors) |

## Occupancy, windows and presence

| Feature | Source | Status |
| :-- | :-- | :-- |
| Window or door pauses control after a delay | VT, RM, CA | drives |
| Presence sensor to home or away | VT, RM, CA | drives (away setback) |
| Presence from several sources | RM | pure (occupancy published) |
| Motion sensor presets with delays | VT | published (motion preset with a delay; setpoint not yet changed by it) |
| Occupancy sensor drives eco | RM | missing |
| Event notifications for window pauses and lock changes | CA | drives |

## Learning and forecasting

| Feature | Source | Status |
| :-- | :-- | :-- |
| Learned room model (k, c, g), with fit guard | RM, CA | drives (forecast and status) |
| Multi-device room model (cooler and heater) | ours | pure (`devices.fit_room`; not wired) |
| Time, energy and cost to setpoint | ours | published |
| Forecast error from backtest | ours | published (synthetic data; live error still unknown) |
| Thermal rate learning (Auto TPI) | VT | published (model) |
| Learning suggestion | CA | published |
| Accept or dismiss learning suggestions | CA | drives (respond_suggestion service) |
| Model predictive control | RM | published (four-hour on and off plan from the learned model and prices; not driving the AC) |
| Adaptive preheat or pre-cool start | RM, CA | drives (schedule deadline only) |
| Heating failure detection | VT | published (needs heater switch) |
| Heater switch feedback | PID | published (not_configured unless auto-tuning) |
| Recorder history backfill at startup | ours | drives (startup backfill) |
| Recorder history export (CSV) | VT | drives (export_csv service) |
| What-if forecast service | CA | drives |

## Weather and environment

| Feature | Source | Status |
| :-- | :-- | :-- |
| Weather forecast input | RM, CA | drives (day class and heatwave use the forecast when a weather entity is set) |
| Day class from outdoor reading | CA | published |
| Heatwave streak | CA | published (recent readings, not a forecast) |
| Forecast day counts | CA | pure |
| Daily briefing | CA | published |
| Night plan (pre-cool or hold) | CA | published |
| Whole-house fan control | CA | drives (switch, if configured) |
| Whole-house fan safety rule | CA | drives (through the fan switch) |
| Natural ventilation timing | CA | published (window advice; windows not driven) |
| Economizer (two-phase window cooling) | CA | published (open-windows and fan-assist advice; windows and fan not driven) |
| Solar gain from irradiance and window area | RM | published |
| Solar sensor learning | RM | missing |
| Cover or blind control | RM | drives (cover entity, if configured) |

## Air quality, humidity and mould

| Feature | Source | Status |
| :-- | :-- | :-- |
| Ventilation on CO2 or PM2.5 (on-off) | RM | drives (fan switch, if configured) |
| Proportional ventilation rate from CO2 | RM | published |
| Room fan control | RM | drives (fan switch, if configured) |
| Humidity band | RM | drives (dehumidifier and humidifier switches) |
| Dehumidify with the AC's dry mode | RM | drives |
| Drying decision (no cooling needed) | CA | published |
| Mould risk from surface humidity | RM | published |
| Mould prevention raises target | RM | drives (control target raised by 1 C while mould risk is present) |
| AC coil drying after heating | RM | missing |
| Auto fan speed from temperature gap | VT | published |

## Valves, radiators and multi-device

| Feature | Source | Status |
| :-- | :-- | :-- |
| TRV or valve control | VT, RM | drives (valve entity, if configured) |
| Modulating valve opening | RM, VT | drives (valve entity, if configured) |
| Valve anti-seize exercise | RM | pure |
| TRV offset learning | RM | missing |
| Radiator and underfloor residual heat | RM | missing |
| Heat source selection (boiler or AC) | RM | drives (outdoor temperature picks the source; the boiler switch follows demand) |
| Central boiler activation | VT | drives (boiler switch, if configured) |
| Compressor groups (several AC on one compressor) | RM | drives (starts of two ACs in one room are staggered by 5 minutes; not across rooms) |
| Second AC under the same room (multi-device control) | ours | drives (same cooling decision; not yet held by the minimum on and off times) |
| Multi-room coordination | VT, RM | missing (one room per entry) |
| Power shedding per device | VT | drives (lowest-priority loads switched off over the limit; not turned back on yet) |
| Central power limit | VT | drives (whole-house limit, same path as per-device shedding) |

## Safety, diagnostics and services

| Feature | Source | Status |
| :-- | :-- | :-- |
| Sensor stall detection | VT | published |
| Repair issue and notification for stalled sensors | CA | drives |
| Safety output on stall | VT | published |
| Diagnostics export with redaction | VT, CA | drives (HA diagnostics) |
| Services: reset learning, export samples | VT, CA | drives |
| Start auto-tune service | PID | drives (heater, during a test) |
| Accuracy page (prediction versus actual) | CA | published (forecast error sensor only) |
| Notifications on events | CA | drives |
| AI investigator and budget | CA | missing (out of scope for software-only) |

## Unbuilt items (count)

Rows marked `missing` above, excluding the AI investigator which is out of scope:
heater polarity, output clamps, boost PID, motion presets, occupancy eco, solar sensor learning, mould target, coil drying, TRV offset learning, residual heat, compressor groups, multi-room.

After the weather, shedding, economizer, heat-source, boiler, second-AC, compressor-staggering and MPC rows were updated, 12 rows remain. The live forecast error is still `unknown` until real cool-downs are scored.
