---
title: Services and presets
summary: The presets, the schedule blocks and the services that HVAC Horizon registers, with their fields and results.
section: guides
order: 30
audience: users
status: draft
---

## Presets

A preset sets the cooling target and the heating target. The preset in use is the first match of these:

1. The timed preset, while its duration runs. See `start_timed_preset`.
2. The preset chosen on the room climate entity, until the schedule moves to another block. See below.
3. The schedule block that covers the current local hour.
4. `comfort`, when none applies.

| Preset | Cooling target | Heating target |
| :-- | :-- | :-- |
| `comfort` | `comfort_c` | `comfort_c` |
| `eco` | `eco_c`, or `comfort_c` when unset | `heat_eco_c`, or comfort minus 1 |
| `boost` | `boost_c`, or `comfort_c` when unset | `heat_boost_c`, or comfort plus 1 |
| `away` | `away_c` | 16 °C frost floor |

An unoccupied room cools to `eco_c` when set. Otherwise it cools to `away_c`. It heats to the 16 °C frost floor. With a motion sensor and no motion for `motion_away_min`, it heats to the eco heat setpoint instead.

Boost with a heater and heating allowed raises its cooling target to the heat target plus 0.5 K. The AC then does not cool while the heater heats.

The vacation switch sets its own targets, separate from the presets. While it is on, `vacation_c` replaces the comfort target. Occupancy and the preset still choose the cooling threshold, so an unoccupied house cools to `eco_c` or `away_c`. Heating holds the 16 °C frost floor.

## Schedule blocks

Set `schedule_blocks` in the form. The value is a list of blocks. Each block has three keys.

| Key | Type | Rule |
| :-- | :-- | :-- |
| `start_h` | number | 0 to 24. The block starts at this hour. |
| `end_h` | number | 0 to 24. The block ends before this hour. |
| `preset` | text | `comfort`, `eco`, `boost` or `away`. |

`start_h` must differ from `end_h`. When `end_h` is below `start_h`, the block crosses midnight. With no blocks, the preset is `comfort` all day.

```json title="Schedule blocks"
[
  {"start_h": 6, "end_h": 8, "preset": "boost"},
  {"start_h": 22, "end_h": 6, "preset": "eco"}
]
```

A form error reads `Schedule blocks must be a list of [start_h, end_h, preset]. Hours run from 0 to 24 and start_h must differ from end_h. Presets: comfort, eco, boost, away.`

Before a comfort block, the heater starts early when the learned heat-up time needs it. The Heat-up time sensor shows the estimate in minutes.

## Room climate entity

Each entry has one `climate` entity, named after its device (`HVAC Horizon <climate name>`). It sets targets on HVAC Horizon. It never drives a device itself. The coordinator applies each change on its next tick, through the control, lock, inhibit and minimum-time gates.

| Property | Behaviour |
| :-- | :-- |
| Current temperature | The median of the room temperature sensors. |
| Target temperature | 16 to 32 °C in 0.5 K steps. Setting it stores your target. HVAC Horizon uses it as `comfort_c` from the next tick, and keeps it across restarts. Before you set one, it shows `comfort_c` from the form. |
| Preset | `comfort`, `eco`, `boost` or `away`. It shows the preset in use. Setting one holds it until the schedule moves to another block. A timed preset still wins while it runs. The hold is not kept across a restart. |
| Mode | `off`, `cool`, and `heat` when the entry has `heater_switch` or `ac_heat`. It shows `off` when control is off, when you turned the room off, or when neither the AC nor the heater is running. |

Setting the mode to `off` holds the room off: the AC and the heater switch go off and nothing starts. Setting `cool` or `heat` ends the hold and returns the room to automatic control. It does not force a start: the AC runs when the room needs it.

While the lock is on, setting a target, a preset or a mode fails with `HVAC Horizon entry is locked`.

## Services

Every service except `set_house_mode` takes `entry_id`, the HVAC Horizon entry. Services marked response only return data and set nothing.

| Service | Fields | What it does |
| :-- | :-- | :-- |
| `hvac_horizon.set_lock` | `locked`, `pin` | Locks control, so normal commands stop. Unlocking needs the PIN. |
| `hvac_horizon.set_house_mode` | `mode`: `home` or `away` | Sets the house mode for every entry. `away` makes each room use its away setpoint. |
| `hvac_horizon.start_timed_preset` | `preset`, `minutes` (1 to 720) | Runs the preset for `minutes`, then returns to the schedule preset. |
| `hvac_horizon.start_autotune` | `setpoint` (15 to 26 °C) | Starts a relay test. The heater plug switches on and off. The setpoint must be at least 2 K above the room. The test ends after 120 minutes, or when the AC cools or the room reaches the heat-on threshold. Do not run it while the AC is under test. |
| `hvac_horizon.cancel_autotune` | none | Ends a running relay test and turns the heater off. Does nothing when no test runs. The `autotune_status` attribute then reads `cancelled`. |
| `hvac_horizon.respond_suggestion` | `accept` | `true` stores the current fitted room coefficient `k` as the baseline. `false` hides the suggestion until `k` changes. |
| `hvac_horizon.generate_investigation_report` | none | Builds a 24-hour report. Uses the conversation agent when the daily budget allows. Otherwise stores the rule-based report. |
| `hvac_horizon.reset_learning` | none | Clears the stored readings, the fitted model and the backtest. Settings stay. |
| `hvac_horizon.export_samples` | none | Response only. Returns `samples`, the stored readings. |
| `hvac_horizon.export_csv` | none | Response only. Returns `csv`, the stored readings as CSV text. |
| `hvac_horizon.what_if` | `room_c`, `target_c`, `outdoor_c`, `power_kw` | Response only. Returns `status`, `minutes`, `energy_kwh`, `cost` and `reason`. |

The `what_if` ranges are 0 to 50 °C for `room_c` and `target_c`, -20 to 60 °C for `outdoor_c`, and 0 to 10 kW for `power_kw`.

`start_autotune` fails with an error when the setpoint is outside 15 to 26 °C, when no room sensor has a reading, or when the setpoint is less than 2 K above the room.

### Lock and PIN

The lock needs a PIN in the form. Without one, `set_lock` with `locked: true` fails. A wrong PIN fails the unlock. Both raise this error:

```text title="Error"
lock not changed: wrong PIN, or no PIN configured
```

The lock is stored. It stays on after a reload or a restart of Home Assistant. The PIN stays in the config entry.

While locked, the integration sends no normal commands: no AC setpoint, no valve, cover or window action. The heater output is held at 0. It still turns the AC and the room devices off when a sensor is stalled, a window has been open longer than `window_delay_min`, or the house mode is `away`. The action text then reads `locked: off, <reason>`.

### House mode

`set_house_mode` stores the mode with each entry. It survives a restart. The default is `home`.

:::see-also
- [[guides/set-up-the-integration]]: the form fields for presets and setpoints.
- [[guides/troubleshoot]]: what the lock and the stall stop look like.
:::
