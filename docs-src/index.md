---
title: HVAC Horizon
summary: A Home Assistant integration that runs the heating and cooling of one room and forecasts the time, energy and cost to reach its target.
section: start
order: 1
audience: users
status: draft
---

## What it does

HVAC Horizon controls the climate of one room in Home Assistant. You give it an air conditioner, a power sensor, room temperature sensors and an outdoor temperature sensor. It adds one thermostat for the room and 75 sensors.

- It learns how fast the room gains heat and how fast the AC cools it, from readings it takes every 5 minutes.
- It forecasts the minutes, the energy and the cost for the room to reach its target.
- With control on, it drives the AC, a heater, and any second AC, fan, valve, cover or window actuator you add.

A new entry starts in observe-only mode. It learns and forecasts, and it sends no commands until you turn control on.

## Where to start

| You want to | Read |
| :-- | :-- |
| Install it and add a room | [[start/install]] |
| Choose the mode, the target and the preset | [[guides/use-the-room-thermostat]] |
| Add a heater, a price sensor or other devices | [[guides/set-up-the-integration]] |
| Understand a forecast or a status | [[guides/read-the-forecast]] |
| Find out why a device is on or off | [[guides/troubleshoot]] |
| Look up one setting or sensor | [[reference/settings]], [[reference/sensors]] |

## Requirements

- Home Assistant 2026.11.2 or later.
- A `climate` entity for the AC, with the modes `off` and `cool`.
- A power sensor in watts that reads the whole AC unit. A wall plug with power metering works.
- At least one room temperature sensor and one outdoor temperature sensor.
