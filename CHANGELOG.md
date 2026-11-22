# Changelog

Versions follow `custom_components/hvac_horizon/manifest.json`. Dates are the tag dates.

## 0.5.6 (2026-11-22)

- A damaged field in the stored data takes its default. Before, a text or a list where a number belongs in one of
  7 stored fields kept the entry from loading. A test now loads every stored field, and every key inside it, with 9
  wrong values, and checks that a room held off sends no start.
- The room fit with `k` and `c` at or above zero is the best such fit. Before, a negative coefficient was dropped one
  at a time, and on some data that gave a fit with a larger error than another allowed one. The two-device room fit
  follows the same rule.

## 0.5.5 (2026-11-22)

- Free cooling is given up when it cools too little. With the window open or the whole-house fan on, a room above the
  cool threshold that fell by less than 0.3 K in 30 minutes gets the AC. Before, a room held up to 2 K above the
  threshold by an open window stayed there.
- The outdoor forecast bias is 0 until 6 sensor and forecast pairs exist. Before, the first pair shifted the forecast
  by up to 0.5 K.
- The Forecast error sensor has two new attributes. `range_coverage_pct` is the percentage of backtest runs whose
  measured minutes fell inside the forecast range of that run, and `range_runs` is the count of those runs. The
  accuracy panel shows both.
- The manifest declares the category `climate.thermostats_heating`.
- `tests/ha/test_basics.py` runs 12 seeded random days with noisy room sensors, a restart and a target change.

## 0.5.4 (2026-11-22)

- `heat` mode heats on a warm day. The 22 °C outdoor heating limit applies in `auto` and `dry` only. Before, `heat` with
  the room below the heat threshold and outdoors above 22 °C left the heater off and gave no reason.
- `cool` mode cools on a cold day. The 16 °C outdoor cooling limit applies only in `auto` on an entry with a window
  actuator or a whole-house fan, and ends once free cooling has failed.
- Free cooling gets 30 minutes before it is given up. Before, a window opened into a room more than 2 K above the cool
  threshold closed again on the same tick.
- In `auto` the AC waits 60 minutes after the heater ran, unless the room is 2 K above the cool threshold. Without
  the wait the AC cooled away the heater's overshoot on a cold night.
- `tests/ha/test_basics.py` tests the thermostat from the outside: 72 cases set a mode and a target on the room
  thermostat, run a simulated room minute by minute and check the heater switch, the AC and the thermostat state.
- The thermostat's `pending` attribute names the gate that holds a wanted heater off, for example
  `heat held: window open`.

## 0.5.3 (2026-11-21)

- `set_lock` needs an administrator.

## 0.5.2 (2026-11-21)

HACS pre-submission audit:

- The accuracy panel is now a web component that calls the API through Home Assistant. The old page read the browser's sign-in token from local storage.
- `reset_learning`, `export_samples`, `export_csv`, `start_autotune`, `respond_suggestion` and `generate_investigation_report` need an administrator.
- The diagnostics download also removes entity IDs inside text values and dict keys, and the keys `heatwave_warning`, `compressor_group`, `sensors_stalled`, `shed`, `restored`, `dispatch` and the investigation texts.
- Removing an entry deletes its stored readings. Unloading cancels the heater's window timers.
- Brand icon added in `brand/`. README has a privacy section.

Documentation rewritten for the room thermostat and the current control rules. New page `guides/use-the-room-thermostat`. The settings and sensor references are built from the code: 86 settings by form section with their effects, and 75 sensors by group with their meanings.

Faults found by an audit of all 86 settings (`tests/ha/test_settings_effect.py` runs each setting at two values over a scripted day and compares device commands and published data).

- The whole-house fan ran whenever outdoors was 2 K cooler, including a 19 °C room with the heater on. It now runs only while the room is above the cool threshold. It cools ahead of the AC, and the AC takes over when the room climbs 2 K above the threshold.
- The AC kept cooling after this entry opened the window actuator. The AC now stays off while the entry holds the window open, with the same 2 K give-up rule.
- `humidity` (outdoor humidity) was stored with each sample and read by nothing. Window advice, the economizer and the window actuator now stay shut when outdoor air has a dew point 1 K or more above the room's (or at 20 °C and above with no room humidity sensor).
- `presence` did nothing in an entry that also had `motion`. An empty house now heats to the frost floor once motion is idle. Motion in the room still counts as occupied.
- A pre-cool with no plan that met the schedule event did nothing, so an unoccupied room stayed at its setback until the event. It now starts at once when the event is within 120 minutes (`precool_status` reads `late: starts now`).
- `tpi_threshold_high` and `tpi_threshold_low` could not act, because every heater stopped 0.5 K below the heat target. With `heater_algorithm` `tpi` the heater now runs up to the target, and up to the larger threshold above it when both are set. PID is unchanged.

## 0.5.1 (2026-11-21)

- A dry start waits for the restart delay `min_off_min`. Dry mode pauses the AC 1 K below the heat threshold and resumes at the threshold.
- The humidifier switch stays off while the room is held off.
- New `dry` mode on the room entity. The AC runs in dry mode and never cools, and the heater may run at the same time to hold the heat target. The action reads `drying`.
- The dehumidifier switch stays off while the room is held off.
- Fix: the room entity's mode now decides which devices run. In 0.5.0 the mode was only stored, so the AC cooled a warm room in `heat` mode. `cool` runs cooling devices only, `heat` runs heating devices only.
- New `auto` mode runs both, and is the default. A mode stored by 0.5.0 or earlier loads as `auto`, which is how those versions behaved. Store version 4.
- The mode also gates the boiler, the valve, the whole-house fan, the economizer fan and the window actuator. Ventilation for CO2 and PM2.5 runs in every mode.
- A room whose only heat source is a heating fleet device now offers `heat`.
- `climate.turn_on` returns to the mode chosen before off. Before, Home Assistant's default picked `heat` when the room had a heater.
- The room entity's `hvac_action` follows the AC, its power sensor, the heater switch and the fleet devices as they change. Before, it updated only on the 5 minute tick, so it could read idle while the compressor ran.

## 0.5.0 (2026-11-21)

- Learning samples are not recorded while the outdoor sensor is stalled or unavailable, so the fit only sees measured outdoor temperatures.
- Weather entity data is used beyond the daily highs. The hourly forecast (fetched with the daily one, at most every 30 minutes, last good result kept) gives an outdoor temperature series that starts at the sensor reading. The time-to-setpoint forecast, `mpc_plan`, `mpc_heat_plan`, pre-cool and preheat use it. The daily `templow` sets the night plan. New optional field `heatwave_warning` (binary sensor) forces `heatwave` on. Without an irradiance sensor, the forecast condition scales the clear-sky solar estimate (`solar_factor`). A stalled or unavailable outdoor sensor falls back to the hourly forecast when it is under 2 hours old (`outdoor_source`). A per-hour-of-day bias between the outdoor sensor and the forecast is learned, stored, applied to the series and the fallback, and cleared by `reset_learning`. New sensors: `outdoor_forecast_used`, `outdoor_source`, `outdoor_forecast_next_c`, `outdoor_bias_now_k`, `outdoor_bias_pairs`, `solar_factor`.
- Tests run on Home Assistant 2026.11.2 with `pytest-homeassistant`, Home Assistant's own test package. The old `pytest-homeassistant-custom-component` harness is retired and does not load on 2026.11. The minimum Home Assistant version in `hacs.json` is now 2026.11.2.
- Off is immediate. Turning the room off from the room entity sends off to the first AC, the second AC, the heater switch and every fleet device in the same call. No minimum on time, inhibit window or sensor gate delays it.
- `min_on_min` no longer delays any stop and is ignored. `min_off_min` is now the restart delay: a start waits that long after the device stopped, and 0 disables it.
- A start held by the restart delay is re-checked when the delay ends instead of at the next 5 minute tick. The room entity has the attribute `pending` (for example `start in 2 min: restart delay`) and there is a `Pending action` sensor.
- The room-rate fit now publishes `fit_residual_k_per_h`, the standard errors, `fit_confidence` and the `minutes_low` and `minutes_high` range. They were empty when too few AC-on samples existed for the single fit.
- Setpoint regulation (option `setpoint_regulation`, default on): while the decision is cool, the setpoint sent is the cool target minus (room median minus the AC's own `current_temperature`) when that is positive, rounded to the device step and clamped to `min_temp` and `max_temp`. AC heat uses the mirror. An AC with no `current_temperature` attribute is regulated from power: after 10 minutes in the mode with the room beyond its threshold and power under 0.2 kW, the setpoint moves one step per tick, and it moves back when the compressor runs or the room reaches the threshold. New diagnostic sensors `ac_setpoint_sent_c` and `ac_sensor_offset_k`.
- The room entity `hvac_mode` is the user's intent: off while the hold is set or control is off, otherwise the mode the user last chose (cool by default, stored with the hold as `user_mode`). `hvac_action` is cooling or heating only while power is at or above 0.2 kW, and idle while the AC runs the fan only.
- The room climate entity uses `native_current_temperature`, `native_target_temperature` and `_attr_native_temperature_unit` on Home Assistant versions that have them (2026.11 and later), and the old names on earlier versions, so 2026.11 logs no deprecation warning.
- A room held off stays off after a restart: the boiler stays off and the valve closes while the hold is set, and no tick runs before the stored hold is read.

## 0.4.0 (2026-11-21)

Changes since 0.3.0.

- A sensor stall turns the first and second AC off without waiting for the minimum on time, as it already did for fleet devices.
- Safety stop and unload turn off every output the entry commanded on: AC in dry mode, second AC, fan, humidifier, dehumidifier, boiler, whole-house fan and valve.
- A failed update tick no longer swallows `KeyboardInterrupt`, `SystemExit` or `CancelledError`.
- Minimum on and off times count from the device's own `last_changed`, so a restart or a manual change does not clear them.
- The commanded device state is kept in the store across restarts.
- A shed load is not switched back on when the user changed it after the shed.
- New sensor `rooms_max_age_s` shows the age of the oldest room reading in use.
- The fit rejects outliers beyond 3.5 scaled MADs and reports residual, standard errors and a confidence level. `minutes_low` and `minutes_high` give a forecast range.
- The room climate entity shows the effective target and reports `hvac_action` from the real devices. With control off it sets the attribute `control` to `observe only`.
- Room fan, humidity outputs, whole-house fan and window advice use deadbands. AC dry mode stays on while the humidity decision says dehumidify.
- Sensors outside the 11 primary ones are diagnostic. 32 of them are disabled by default (internal detail such as PWM state, PID and TPI outputs, plan steps and fit internals).
- Setup runs in steps: a room step, a "what next" menu and eight optional sections. Finishing after the room step creates an observe-only entry. Reconfigure opens the same sections from a menu.
- Every service has an entry in `translations/en.json`. The manifest keys are in hassfest order.
- GitHub Actions workflow `ci.yml` runs the tests, hassfest and HACS validation.
- Added `SECURITY.md` and this file. New docs page `concepts/limits`.

## 0.3.0 (2026-11-21)

- Added a climate entity per room.
- Rooms can hold several devices. The dispatch drives fleet devices and calibrates each one.
- The heater safety step runs on every tick. A stalled sensor stops the AC while control is locked.
- Added a 0.5 K deadband to the AC cool decision.
- The lock, house mode, inhibit window, timed preset, shed loads and window flag survive a restart.
- The window opens only when the room needs cooling.
- Relay auto-tune has a 120 minute limit, a cancel service, cutoffs and setpoint checks.
- Diagnostics redact the lock PIN and every configured entity id.
- Pre-cool before arrival parses a datetime `next_event` and pre-cools while away.
- The MPC plan idles cool within the deadband and reports its effect.
- The target temperature sensor shows the cool threshold the control uses.
- The config flow validates price thresholds and fleet keys.
- The mould cool-threshold raise applies to every preset.
- The AC and heater are turned off when Home Assistant stops.
- AC heat and dry intervals stay out of the cooling fit.
- README, docs pages and the feature matrix match the code.

## 0.2.0 (2026-11-20)

- First tagged release. The forecast fits a first-order room model (k, c, g) from stored readings and publishes minutes, energy and cost to the setpoint.
- Control is opt-in: the `control` field is off by default and the entry observes only.
- Heater path: PWM window, PID and TPI algorithms, output limits, inverted switch, relay auto-tune, TRV offset and valve control, heating failure detection.
- Presets: comfort, eco, boost and away, schedule blocks, timed presets, vacation, motion and presence with an away delay.
- Safety: lock with PIN, inhibit window after a manual setpoint change, stall detection for room and outdoor sensors, minimum on and off times.
- Humidity and air quality: AC dry mode, humidifier and dehumidifier switches, CO2 ventilation rate, PM2.5, mould risk, window advice and actuator.
- Price: cheap and expensive thresholds, price-shifted target, last good price kept for one hour.
- Energy: whole-house power limit with load shedding, compressor group start gaps, economizer fan, house-wide home and away mode.
- Four-hour MPC plan and night plan from the learned model and prices.
- Services: set_lock, set_house_mode, export_csv, export_samples, reset_learning, what_if, start_timed_preset, respond_suggestion, start_autotune, generate_investigation_report.
- Backfill of the model from recorder history at startup.
- Rule-based AC investigator with an optional conversation agent and a daily call budget.
- Accuracy sidebar panel behind the `accuracy_panel` field.
- Diagnostics export with redaction.
- User and developer guides as `docs-src` pages, and a feature matrix.
