---
title: Actions and presets
summary: Set presets by schedule or timer, and call the HVAC Horizon actions for the lock, house mode, auto-tune, export and what-if.
section: guides
order: 30
audience: users
status: draft
---

## Which preset applies

A preset sets the cool threshold and the heat target. HVAC Horizon uses the first of these that applies:

1. A timed preset, while its minutes run. See `start_timed_preset` below.
2. The preset you chose on the room thermostat, until the schedule moves to another block.
3. The schedule block that covers the current local hour.
4. `comfort`.

| Preset | Cools above | Heats to |
| :-- | :-- | :-- |
| `comfort` | `comfort_c` | `comfort_c` |
| `eco` | `eco_c`, or `comfort_c` when unset | `heat_eco_c`, or comfort minus 1 |
| `boost` | `boost_c`, or `comfort_c` when unset | `heat_boost_c`, or comfort plus 1 |
| `away` | `away_c` | 16 °C |

Three things move these values:

- **Price.** With a price sensor, the cool threshold rises above the midpoint of the cheap and the expensive price, up to 1 K at the expensive price. A low price leaves it at your target.
- **Boost with a heater.** When heating is allowed, boost lifts its cool threshold to the heat target plus 0.5 K. The AC then does not cool while the heater heats.
- **Vacation.** While the vacation switch is on, `vacation_c` replaces the comfort target and heating holds 16 °C.

## Schedule blocks

Open **Reconfigure** and select **Schedule blocks**. A block applies a preset between two times of day.

1. Set **New block starts at** and **New block ends at**.
2. Choose the preset: `comfort`, `eco`, `boost` or `away`.
3. Select **Submit**. Turn on **Add another block after this one** first to stay on the form.

To remove blocks, tick them under **Remove these blocks** and select **Submit**.

A block whose end is before its start runs past midnight. Times outside every block use `comfort`. Two blocks cannot share a time of day: the form refuses a new block that overlaps one you have. The entry stores the blocks as `schedule_blocks`, a list of `start_h`, `end_h` and `preset`, which the diagnostics download shows.

| Message | Cause |
| :-- | :-- |
| `Set both the start and the end of the new block.` | One of the two times is empty. |
| `The start and the end of a block must differ.` | Both times are the same. |
| `The new block overlaps the block 22:00 to 06:30: eco.` | The new block shares a time of day with the block named. |

Before a comfort block, the heater starts early when the learned heat-up time needs it. The Heat-up time sensor shows the estimate in minutes.

## Run a preset for a set time

```yaml title="Boost for one hour"
action: hvac_horizon.start_timed_preset
data:
  entry_id: 01M7W2QT14QZYW5QG2879X0FHA
  preset: boost
  minutes: 60
```

`minutes` is 1 to 720. When the time ends, the schedule preset applies again. Find the `entry_id` in the URL of the entry's page, or in its diagnostics download.

## Actions

The actions exist as soon as the integration is installed. An action on an entry that is not loaded fails with `the HVAC Horizon entry <title> is not loaded`. A value an action refuses, such as a setpoint out of range, shows its reason in Home Assistant.

Every action except `set_house_mode` takes `entry_id`. The actions marked response only return data and change nothing. The actions marked admin need an administrator: a call from another user fails with `Unauthorized`.

| Action | Fields | What it does |
| :-- | :-- | :-- |
| `hvac_horizon.start_timed_preset` | `preset`, `minutes` | Runs the preset for `minutes`. |
| `hvac_horizon.set_house_mode` | `mode`: `home` or `away` | Sets the house mode for every entry. `away` makes each room use its away setpoint and heat to 16 °C. |
| `hvac_horizon.set_lock` | `locked`, `pin` | Locks or unlocks control. Admin. |
| `hvac_horizon.start_autotune` | `setpoint` | Starts a relay test of the heater and stores PID gains from it. Admin. |
| `hvac_horizon.cancel_autotune` | none | Ends a running relay test and turns the heater off. |
| `hvac_horizon.respond_suggestion` | `accept` | `true` stores the current heat-gain rate as the baseline. `false` hides the suggestion until the rate changes. Admin. |
| `hvac_horizon.generate_investigation_report` | none | Builds a 24-hour report on the Status sensor. Admin. |
| `hvac_horizon.reset_learning` | none | Clears the stored readings, the room model, the kept models, the backtest and the outdoor bias. Settings stay. Admin. |
| `hvac_horizon.export_samples` | none | Response only. Returns `samples`, the stored readings. Admin. |
| `hvac_horizon.export_csv` | none | Response only. Returns `csv`, the stored readings as CSV text. Admin. |
| `hvac_horizon.what_if` | `room_c`, `target_c`, `outdoor_c`, `power_kw` | Response only. Returns `status`, `minutes`, `energy_kwh`, `cost` and `reason` for the values you give. |

`what_if` accepts 0 to 50 °C for `room_c` and `target_c`, -20 to 60 °C for `outdoor_c`, and 0 to 10 kW for `power_kw`.

## Lock and PIN

The lock stops normal commands, so nobody changes the room by accident.

1. Open **Reconfigure**, select **Advanced** and enter a value in **Lock PIN**. Select **Submit**, then **Save and close**.
2. Call `hvac_horizon.set_lock` with `locked: true`.
3. To unlock, call it with `locked: false` and `pin` set to the PIN.

`set_lock` needs an administrator, to lock and to unlock.

While locked, HVAC Horizon sends no AC setpoint, valve, cover or window command, and holds the heater output at 0. The room thermostat refuses a new target, preset or mode. A stalled sensor, a window open longer than `window_delay_min` or an away house still turns the AC and the room devices off. The lock is stored and stays on after a restart.

| Message | Cause | Fix |
| :-- | :-- | :-- |
| `lock not changed: wrong PIN, or no PIN configured` | The entry has no PIN, or the `pin` in the call is wrong. | Set a PIN in **Advanced**, or send the right one. |
| `HVAC Horizon entry is locked` | You changed the thermostat while the lock is on. | Unlock first. |

## Auto-tune the heater

`start_autotune` switches the heater on and off around a setpoint and derives PID gains from the room's swing.

```yaml title="Start a relay test"
action: hvac_horizon.start_autotune
data:
  entry_id: 01M7W2QT14QZYW5QG2879X0FHA
  setpoint: 22
```

The setpoint is 15 to 26 °C and at least 2 K above the room. The test ends after 120 minutes, when the AC cools, or when the room reaches the heat threshold. The Status sensor's `autotune_status` attribute reads `running`, `done`, `timed_out`, `cancelled` or `reached_cutoff`. The gains apply to any of `heater_kp`, `heater_ki` and `heater_kd` that you left at its default.

:::warning The heater runs during the test
The relay test turns the heater on at full output in cycles. Do not start it while you test the AC.
:::

## House mode

`set_house_mode` stores the mode with each entry. It survives a restart. The default is `home`.

:::see-also
- [[guides/use-the-room-thermostat]]: modes, targets and presets on the thermostat.
- [[reference/settings]]: the fields for presets and setpoints.
:::
