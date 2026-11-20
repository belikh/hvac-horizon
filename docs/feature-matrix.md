# Feature matrix against the four competitors

Sources: VT = Versatile Thermostat, RM = RoomMind, CA = ClimateAdvisor, PID = smart_thermostat (ScratMan).

Status values:
- `drives`: changes a device (climate, switch or a setpoint) through the control path.
- `published`: computed each cycle and shown on a status attribute. Nothing acts on it.
- `pure`: a tested function with no caller in the integration.
- `missing`: not built.

One row per feature. "Ours" is the HVAC Horizon file that implements it.

## Control and climate

| Feature | Source | Ours | Status |
| :-- | :-- | :-- | :-- |
| Set HVAC mode and setpoint on a climate entity | VT, RM, CA, PID | coordinator `_async_control` | drives |
| Observe-only mode (no commands sent) | CA | `control` option | drives |
| Proportional or TPI power control | VT, PID | `pid.py` | pure |
| Auto-tuner from a relay test (Ziegler-Nichols) | PID | `autotune.py` | pure |
| Heater switch output with PWM duty cycle | PID, VT | none | missing |
| Output polarity (inverted switch) | PID | none | missing |
| Minimum on and off times | VT | `minimum_time_ok` | drives |
| Keep-alive command resend | VT | `keep_alive_due` | pure |
| Force-off on stall or sensor failure | VT, PID | safety output | published |
| Output clamps from entities | PID | none | missing |
| Boost forces PID auto or suspends it | PID | none | missing |

## Presets and schedules

| Feature | Source | Ours | Status |
| :-- | :-- | :-- | :-- |
| Comfort, eco, boost, away, home, sleep presets | VT, PID | `Preset`, `preset_setpoint` | drives (comfort, eco, boost, away) |
| Preset temperatures from config | VT, PID | eco and boost setpoints | missing (not in config flow) |
| Preset sync (move target when preset changes) | PID | none | missing |
| Timed preset (duration then revert) | VT | `timed_preset_active` | pure |
| Scheduled presets by time of day | RM, CA | `scheduled_preset` | drives (empty default) |
| Schedule entity drives presets | RM | `schedule` entity | published (pre-cool deadline only) |
| Price-shifted target | RM | `price_shifted_target` | drives |
| Price fallback when sensor drops out | RM | `stale_price_fallback` | published |
| Vacation mode holds vacation temperature | RM | `vacation_target` | drives |
| Comfort-energy weight blend | RM | `comfort_energy_setpoint` | pure |
| Outdoor cooling limit | RM | `decide` | drives |
| Heating above outdoor limit blocked | RM | none | missing |

## Occupancy, windows and presence

| Feature | Source | Ours | Status |
| :-- | :-- | :-- | :-- |
| Window or door open pauses control after delay | VT, RM, CA | `decide` | drives |
| Presence sensor to home or away | VT, RM, CA | `_occupancy_mode` | published |
| Presence affects target (away setback) | VT, RM | `decide` away branch | drives |
| Presence from several sources | RM | `presence_mode` | pure |
| Motion sensor presets with delays | VT | none | missing |
| Occupancy sensor driving eco | RM | none | missing |

## Learning and forecasting

| Feature | Source | Ours | Status |
| :-- | :-- | :-- | :-- |
| Learned room model (k, c, g), fit with guard | RM, CA | `model.py`, `devices.py` | published (drives via forecast only) |
| Multi-device room model (cooler and heater) | ours | `devices.fit_room` | pure |
| Time, energy and cost to setpoint | ours | forecast sensors | published |
| Forecast error from backtest | ours | backtest sensor | published (synthetic data) |
| Thermal rate learning (Auto TPI) | VT | `model.py` | published |
| Learning suggestion when gain drifts | CA | `learning_suggestion` | published |
| Model predictive control | RM | none | missing |
| Adaptive preheat or pre-cool start | RM, CA | `precool_start_in_min` (schedule) | drives (schedule only) |
| Heating failure detection | VT | `heating_failure` | published (needs switch) |
| Heater switch feedback | PID | none | missing |

## Weather and environment

| Feature | Source | Ours | Status |
| :-- | :-- | :-- | :-- |
| Weather forecast input | RM, CA | none (outdoor sensor only) | missing |
| Day class from forecast | CA | `classify_day` | published (outdoor sensor) |
| Heatwave streak | CA | `hot_streak` | published (outdoor sensor) |
| Forecast day counts | CA | `weather_day_summary` | pure |
| Daily briefing | CA | `daily_briefing` | published |
| Night plan (pre-cool or hold) | CA | `night_plan` | published |
| Whole-house fan rule | CA | `whole_house_fan_allowed` | pure |
| Whole-house fan control | CA | none | missing |
| Natural ventilation timing | CA | none | missing |
| Economizer (two-phase window cooling) | CA | none | missing |
| Solar gain from irradiance and window area | RM | `sun_heat_gain` | published (optional sensor) |
| Solar sensor learning | RM | none | missing |
| Cover or blind control | RM | `cover_action` | pure |
| Cover hardware | RM | none | missing |

## Air quality, humidity and mould

| Feature | Source | Ours | Status |
| :-- | :-- | :-- | :-- |
| Ventilation on CO2 or PM2.5 (on-off) | RM | `air_quality_ventilation` | published |
| Proportional ventilation rate from CO2 | RM | `ventilation_rate_from_co2` | published |
| Room fan control | RM | none (switch config only) | missing |
| Humidity band | RM | `humidity_action` | published |
| Dehumidify with AC dry mode | RM | `coordinator` | drives |
| Dehumidifier or humidifier switch | RM | `_drive_switch` | drives (if configured) |
| Drying decision (no cooling needed) | CA | `drying_needed` | published |
| Mould risk from surface humidity | RM | `mould_risk`, `surface_humidity` | published |
| Mould prevention raises target | RM | none | missing |
| AC coil drying after heating | RM | none | missing |
| Air quality feeds fan speed | VT | `fan_speed_from_gap` | published |
| Auto fan speed from temperature gap | VT | `fan_speed_from_gap` | published |

## Valves, radiators and multi-device

| Feature | Source | Ours | Status |
| :-- | :-- | :-- | :-- |
| TRV or valve control | VT, RM | none | missing |
| Valve anti-seize exercise | RM | `valve_exercise_due` | pure |
| Modulating valve opening | RM, VT | none | missing |
| TRV offset learning | RM | none | missing |
| Radiator and underfloor residual heat | RM | none | missing |
| Heat source selection (boiler or AC) | RM | none | missing |
| Central boiler activation | VT | none | missing |
| Compressor groups (several AC on one compressor) | RM | none | missing |
| Multi-room coordination | VT, RM | none (one room per entry) | missing |
| Power shedding per device | VT | none | missing |
| Central power limit | VT | none | missing |

## Safety, diagnostics and services

| Feature | Source | Ours | Status |
| :-- | :-- | :-- | :-- |
| Sensor stall detection | VT | `sensor_stalled` | published |
| Safety output on stall | VT | `safety_output` | published |
| Lock with PIN | VT | none | missing |
| Repair for incorrect device state | VT | none | missing |
| External setpoint change detection | RM | none | missing |
| Inhibition (block commands for a period) | VT | none | missing |
| Diagnostics export with redaction | VT, CA | `diagnostics.py` | published |
| Services: reset learning, export samples, what-if forecast | VT, CA | none | missing |
| Recorder history export | VT | none | missing |
| Notifications on events | CA | none | missing |
| Accuracy page (prediction versus actual) | CA | none (forecast error sensor only) | published |
| AI investigator and budget | CA | none | missing (out of scope for software-only) |
| Learning suggestions accept or dismiss | CA | none | missing |
| Repairs for failing sensors | CA | none | missing |

## Totals (computed from the table above)

Count of rows by status across all four competitors: see the lines below, updated when the file changes.
