# Changelog

Versions follow `custom_components/hvac_horizon/manifest.json`. Dates are the tag dates.

## Unreleased

This release changes entity IDs and one mode name. Read the first three items before you upgrade.

- **Mode name.** In a room that can heat and cool, the automatic mode is now `heat_cool`, with its low and high
  target. Home Assistant defines `heat_cool` for a target range and `auto` for a device with one target that follows
  its own logic. A room that only cools keeps `auto`. An automation or script that sets `auto` on a room that can
  heat must set `heat_cool`. The stored mode carries over.
- **Binary sensors.** Twelve values that are on or off are now `binary_sensor` entities: Drying needed, Mould risk,
  Heatwave, Heating allowed, Heater PWM on, External change, Vacation mode, Boiler demand, Whole-house fan, Heating
  failure, House cooling and Outdoor forecast used. Before, they were sensors that read `True` or `False`. Their
  entity IDs change from `sensor.` to `binary_sensor.`, and the old entities are removed at the first start.
- **Energy to setpoint** has the energy device class and no state class, so Home Assistant records no new
  long-term statistics for it. It is a forecast, and the energy class allows statistics for meter totals only.
- Sensors have device classes: temperature, temperature difference, duration, energy and power. Home Assistant shows
  them in your unit system.
- Entity names are entity translations in `translations/en.json`. Entity IDs of the remaining sensors do not change.
- Actions are registered when the integration loads, so they exist before an entry does. An action on an entry that
  is not loaded, a wrong PIN and a value out of range raise `ServiceValidationError`, and Home Assistant shows the
  reason. Before, they raised a plain error that the UI reported as unknown.
- The room's device carries its area as the suggested area, which Home Assistant applies when it creates the device.
  Before, setup wrote the area whenever the device had none, which undid a user removing it.
- `icons.json` gives the actions and the main entities an icon. The entity platforms set `PARALLEL_UPDATES`.
- The docs and the forms say action where they said service, as Home Assistant does since 2024.8.
- Reconfigure suggests entities added to the room's area since setup, and drops an area that was deleted. The Area
  step also suggests the area's CO2 and PM2.5 sensors.

- Setup starts with an Area step. Pick the Home Assistant area of the room, and the next forms suggest the AC, its
  power sensor, the temperature sensors and the humidity, motion and window sensors found in it. The entry is named
  after the area and its thermostat and sensors are placed in it. The step is optional, and existing entries can be
  given an area in the Room step of Reconfigure. The new setting is `area`.
- The device lookup uses `async_get_device_by_identifier`, which replaces a deprecated Home Assistant call.

## 0.6.3 (2026-11-22)

- The room thermostat has a `fan_only` mode when the first AC has one. The AC runs its fan, and nothing cools or
  heats. `hvac_action` reads `fan`.
- The room thermostat has a humidity target when the entry has a room humidity sensor and a device that dries. The
  dehumidifier and the AC's dry mode run above it and go off 3 points below it. The range is 35 to 75 %, the default
  is 60 % and the value is stored. Before, the limit was fixed at 60 %.

- A command that a device refuses no longer ends the tick. The tick goes on to the other devices, the failure is
  logged once and listed by the Unconfirmed commands sensor, and the next tick sends the command again. Before, an AC
  whose integration raised made every tick fail, and the watchdog's stop then raised at the same AC before it
  reached the other outputs.
- An AC that lists no `heat` mode is never sent `heat`, and one with no `dry` mode is never sent `dry`. Before,
  `ac_heat` or `mpc_heat_plan` on a cool-only AC sent `heat` and the tick failed.
- Every form field has a help text under it: what the field needs, what it changes, its range and default, and when
  to leave it alone. Labels are a few words in plain language. Before, the forms had labels only, and some labels
  were whole sentences.
- The forms refuse three more mistakes: a schedule block that overlaps another one, a minimum heater output above the
  maximum, and one TPI threshold without the other. Before, an overlapping block was stored and the first block
  silently won.
- The forms refuse a room thermostat of HVAC Horizon as the AC or the second AC of a room.
- A forecast with no price for part of its time gives no cost and says so in the Status sensor's `reason`. Before,
  the cost was empty with no explanation.
- Tests: the fake AC and switch are real Home Assistant entities (`tests/fakes.py`), so Home Assistant refuses a mode
  or a setpoint the device does not have. The simulated AC has heat, dry and fan modes, its own sensor and a humidity
  node. New closed-loop runs cover a heat pump, dry mode, fan mode, an AC with rated power, an AC whose sensor reads
  low, a cool-only AC and three device faults. See `docs/develop/test-framework.md`.

## 0.6.2 (2026-11-22)

- A target set on the room thermostat applies at once. The room returns to the `comfort` preset: a timed preset ends
  and a schedule block with another preset is set aside until the next block. Before, a target set under `eco`,
  `boost` or `away` was stored and the thermostat kept showing the preset's setpoint.
- In `auto`, a room that can heat has a low and a high target on the thermostat. It heats to the low one and cools
  above the high one. Setting one temperature sets both.
- The thermostat lists the first AC's fan modes and swing modes and passes a change to the AC. It shows the room
  humidity when a room humidity sensor is set.
- A room with heating only: the AC is optional when a heater switch is set. The thermostat lists `off` and `heat`,
  and the room learns its heat loss and the heater's rate from the readings. The heater switch moved to the Room step.
- An AC with no power sensor: the new setting `ac_rated_w` stands in for it. The AC counts as drawing that power
  while its `hvac_action` says it runs.
- Readings taken while the heater ran no longer count as passive drift in the room fit. Before, the heater's heat was
  fitted as the room's own gain.
- Schedule blocks and room devices each have a form step: set the fields of one item and submit, or tick items to
  remove. Before, both were lists typed as JSON.
- The new Expert tuning section holds the PID and TPI gains, the output limits, the minimum on and off times, the
  stall window and setpoint regulation. They left the Heater, Devices and Advanced sections.
- `docs/feature-matrix.md` is generated from the settings audit, and a test fails when it is stale. It lists each
  setting with its form step and what the scripted-day test proves about it.
- The start page says which rooms the integration fits.

## 0.6.1 (2026-11-22)

- The price shift only raises the cool threshold. Before, a price at or below `price_cheap` moved it 1 K down, so a
  target of 24 °C cooled the room to 23 °C. Now the threshold is your target up to the midpoint of `price_cheap` and
  `price_expensive`, and rises to 1 K above it at `price_expensive`.
- The room thermostat shows the cool target you set. Before, it showed the threshold after the price shift. The Cool
  above and Target temperature sensors show the threshold.

## 0.6.0 (2026-11-22)

- The forecast sensors cover heating and drying. While the mode heats and the room is below the heat target, Minutes,
  Energy and Cost to setpoint count to the heat target. In `dry` mode they count to 57 % room humidity. The new
  Forecast mode sensor reads `cool`, `heat` or `dry`. Before, the forecast covered cooling only and read
  `at setpoint` in a cold room.
- New setting `heater_power_w`: the rated power of the heater on the heater switch. It gives the heating forecast its
  energy and cost.
- The cooling forecast is corrected for solar gain, outdoor temperature and room humidity. A correction is kept when
  it lowers the error on the newest 30 % of the readings by 3 %. The Fit corrections sensor lists the kept ones.
- The fit uses the readings of the last 14 days when they alone give a fit. The Fit window sensor shows 14 or 30.
- A watchdog turns every output off when no control tick has finished for 16 minutes. It runs on its own timer. The
  Watchdog trips sensor counts these. Before, a tick that kept failing left a cooling AC on.
- The Unconfirmed commands sensor lists each device whose state differs from the last command on two ticks in a row.
- Free cooling has one state: `idle`, `running` or `gave_up`, shown by the Free cooling sensor. One function holds
  every change between them.
- The pure code moved into two packages: `room_model/` for readings, fits and forecasts, and `engine/` for control
  decisions. A test keeps Home Assistant imports out of both.
- The last good model of each kind is stored and serves when the readings no longer give one. Before, an AC or heater
  that stayed off for 30 days lost its model, and the forecast read `learning` until it had run again. The new Kept
  models sensor gives the age of each model in use that comes from the store.
- The limits page lists which devices have run on real hardware.

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
