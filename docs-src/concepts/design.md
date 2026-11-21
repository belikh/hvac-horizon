---
title: How it works
summary: The room model, the forecast, the control decision of each tick and the safety rules that sit above it.
section: concepts
order: 10
audience: developers
status: draft
---

## One entry, one room

An entry models one room. Every 5 minutes it runs one tick: read the sensors, store a reading, refit the model when due, forecast, plan the targets and drive the devices. The tick is `HorizonCoordinator._async_update_data()`. See [[develop/architecture]].

## Room model

One equation describes the room:

```text title="Room model"
dT/dt = k (T_out - T) - c P + g
```

| Symbol | Unit | Meaning |
| :-- | :-- | :-- |
| `T`, `T_out` | °C | Room temperature (median of the room sensors) and outdoor temperature |
| `P` | kW | Electrical power of the AC. Below 0.2 kW counts as off. |
| `k` | 1/h | Heat gain toward outdoors |
| `c` | K/h per kW | Cooling effect of the AC |
| `g` | K/h | Constant gain from people and appliances |

`fit()` in `model.py` solves `k`, `c` and `g` by least squares over consecutive readings.

- It drops outliers: residuals beyond 3.5 median absolute deviations, at most 10 % of the readings.
- It rejects a fit whose normal matrix has a condition number above 1000.
- It returns a standard error for each value. The error of `c` gives the low and high minutes and the confidence label.
- `k` and `c` stay at or above zero. When the plain fit gives a negative `k` or `c`, the fit tries `k` at zero, `c` at zero and both at zero, and uses the one with the smallest squared error.

Readings where the AC heats, dries or runs its fan only are left out. A reading is not stored while the outdoor sensor is stalled.

## Forecast

`forecast()` in `forecast.py` steps the model forward one minute at a time with the exact exponential solution of the equation. It returns minutes, energy and cost, or `unreachable` when the room does not reach the target in 12 hours.

- The AC runs at its learned running power for the whole forecast.
- The outdoor temperature follows the hourly weather forecast when a weather entity is set. `outdoor.py` builds the series: it starts at the sensor reading, and the offset to the forecast fades over 3 hours. A learned bias for each hour of the day is added once 6 sensor and forecast pairs exist.
- The cost uses the price slots of the price sensor. A minute with no slot has no cost.

## Targets

`_plan_control()` sets three values on each tick.

| Value | Meaning |
| :-- | :-- |
| Cool threshold | The AC cools when the room is above it. |
| Heat target | The heater aims at it. |
| Heat threshold | The heat target minus 0.5 K. The PID heater and AC heat start below it. |

The preset, your target, the price shift, occupancy, vacation and the mould bump set the cool threshold. The heat target follows the preset and never exceeds the cool threshold.

## Cooling decision

`decide()` in `decide.py` returns one action for the AC. Its order is:

1. A window open longer than its delay: off. In `auto`, with a window actuator or a whole-house fan, outdoors below 16 °C: off, until free cooling has failed for 30 minutes. `cool` mode has no outdoor limit. In `auto` the AC also waits 60 minutes after the heater ran, unless the room is 2 K above the cool threshold.
2. A night pre-cool, when `night_plan_act` is on.
3. An unoccupied room: pre-cool when a schedule event is near, else cool only above the setback.
4. An occupied room: cool above the threshold.

A running AC keeps cooling until the room is at the threshold. An AC that is off starts once the room is 0.5 K above it.

The coordinator then applies the rules that sit above `decide()`: the room thermostat's mode, off from the thermostat, free cooling, the night hold and the cooling plan. Free cooling comes first: while the entry holds the window open or runs the whole-house fan, the AC stays off.

### Setpoint regulation

Many ACs regulate on their own return-air sensor, which can read cooler than the room. HVAC Horizon lowers the setpoint it sends by the gap between the room median and the AC's `current_temperature`. An AC that reports no temperature is regulated from power: after 10 minutes in cool mode under 0.2 kW with the room above the threshold, the setpoint moves down one step per tick.

## Heating

A heater switch runs as a share of a 10 minute window. `pid.py` or `tpi.py` gives the share.

- PID stops 0.5 K below the heat target.
- TPI heats up to the heat target, and up to its thresholds above the target when both are set.

The heater stays off in each of these cases:

- The thermostat's mode does not heat.
- Outdoors is above 22 °C and the mode is `auto` or `dry`. `heat` mode has no outdoor limit.
- A window is open, the house is away or the lock is on.
- A sensor is stalled.
- The AC cools.

## Several devices in one room

With `fleet` set, `choose_devices()` in `fleet.py` picks the devices for the gap to the target. `fast` orders them by effect, `economy` by cost per kelvin. Each device has its own rate in K per hour per kW. The rate you enter is replaced by a fitted rate once the device has run alone for 6 readings. A device with no power sensor counts its rated watts while it runs.

## Safety rules

These hold on every tick, above every other decision.

- Off from the room thermostat goes to every device at once. A stop is never delayed.
- No start command goes out before the first valid room and outdoor readings.
- A room held off sends no start, including after a restart.
- A start after a stop waits for the restart delay. The delay counts from the device's own last change.
- A stalled sensor turns the AC off and sets the heater to 0.
- Unloading the entry turns off every output the entry commanded.

## Storage

The entry stores its readings, the room model, the thermostat's mode and target, the lock, the house mode and the commanded device states with Home Assistant's `Store`, version 4. `_HorizonStore` in `coordinator_common.py` migrates older versions.

## Terms

::glossary

:::see-also
- [[concepts/limits]]: what the model assumes and leaves out.
- [[develop/architecture]]: the modules and the order of the tick.
:::
