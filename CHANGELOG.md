# Changelog

Versions follow `custom_components/hvac_horizon/manifest.json`. Dates are the tag dates.

## Unreleased

- Off is immediate. Turning the room off from the room entity sends off to the first AC, the second AC, the heater switch and every fleet device in the same call. No minimum on time, inhibit window or sensor gate delays it.
- `min_on_min` no longer delays any stop and is ignored. `min_off_min` is now the restart delay: a start waits that long after the device stopped, and 0 disables it.
- A start held by the restart delay is re-checked when the delay ends instead of at the next 5 minute tick. The room entity has the attribute `pending` (for example `start in 2 min: restart delay`) and there is a `Pending action` sensor.
- The room-rate fit now publishes `fit_residual_k_per_h`, the standard errors, `fit_confidence` and the `minutes_low` and `minutes_high` range. They were empty when too few AC-on samples existed for the single fit.
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
- User and developer guides as `docs-src` pages, and a feature matrix against four competitors.
