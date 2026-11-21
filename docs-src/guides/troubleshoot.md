---
title: Troubleshoot
summary: What the common states of HVAC Horizon mean, what stops the AC, and how to clear each repair issue.
section: guides
order: 40
audience: users
status: draft
---

## Sensor stalled

A sensor counts as stalled when its `last_reported` time is older than the stall window. The default window is 900 seconds. Set it with `stall_window_s`. The form accepts 300 to 3600 seconds. `unavailable` and `unknown` do not count as stalled. They count as no reading.

The check reads `last_reported`, not `last_updated`. Home Assistant keeps `last_updated` when a sensor repeats its value. A steady reading would look stalled.

What the stall stops:

| Condition | AC and fleet devices | Heater output |
| :-- | :-- | :-- |
| The outdoor sensor is stalled | Off | 0 % |
| Every room sensor is stalled | Off | 0 % |
| One room sensor is stalled and another is fresh | Runs on the fresh median | Unchanged |
| No room has a valid reading for the stall window | Off | 0 % |

A stall stops the AC even inside the inhibit window. It also stops the window actuator from opening. It does not stop the valve, the boiler or the cover: they keep following the last readings.

When sensors stall, HVAC Horizon raises the repair issue `HVAC Horizon sensors not updating`. It also creates a persistent notification. The issue lists the stalled sensors. It clears when they report again.

Check each listed sensor in **Developer tools > States**. Fix the sensor or its integration.

## Inhibited after a manual change

The action reads `inhibited after a manual change` when a manual change is active. A manual change is a device setpoint that differs from the last value HVAC Horizon wrote, by more than 0.05 K.

After a manual change, HVAC Horizon sends no commands for 30 minutes. Keep-alive writes pause too. Each new manual value starts a new 30-minute window. The window ends on its own.

Three commands still go out during the window: a stall stop for the AC, every heater turn-off, and a window close. 

## Minimum on and off times

The AC mode does not change until the current state has lasted long enough.

- A stop is never held. Turning the room off from the room entity sends off to every device at once.
- A start after a stop waits for the restart delay `min_off_min` (default 3 minutes, 0 disables it), counted from the last change. The room entity shows the wait in the attribute `pending`, and the integration re-checks when the delay ends.
- `min_on_min` stays in the options for compatibility and is ignored.
- Devices in `fleet` use the same two times. A stall stop and the safety stops ignore them.

Change them in the **Devices and power limits** section of reconfigure. Each accepts 0 to 120 minutes.

## Lock with a PIN

1. Open reconfigure and select **Advanced**. Enter a value in **Lock PIN**. The field is a password box. Select **Save and close** afterwards.
2. Call `hvac_horizon.set_lock` with `locked: true`. Locking needs no PIN in the call, but fails when no PIN is configured.
3. Call `hvac_horizon.set_lock` with `locked: false` and `pin` set to the PIN. Unlocking needs the same PIN.

The lock is stored, so it stays on after a restart. While locked, the AC action reads `locked` and the room climate entity refuses new targets, presets and modes. A stalled sensor, a window open longer than `window_delay_min` or an away house still turns the AC off. The action then reads `locked: off, sensor stalled`, `locked: off, window open` or `locked: off, house away`.

See [[guides/services-and-presets]] for the error text on a wrong PIN.

## Reasons for the AC action

The control step writes one action text per tick. The `investigation` attribute of the Status sensor lists the recent actions. The Daily briefing sensor shows the action in its `Next` part. Ticks that return early from the control step do not set the Daily briefing. This covers `locked` and `inhibited after a manual change`.

| Text | Cause |
| :-- | :-- |
| `off: window open N min` | The window has been open for at least `window_delay_min`. |
| `off: outdoor N C below cooling limit` | Outdoor temperature is below 16 °C. |
| `cool: waiting for restart delay` or `heat: waiting for restart delay` | The start is held by the restart delay `min_off_min`. The attribute `pending` on the room entity says how long. |
| `cool: waiting for compressor group` | Another entry in the same `compressor_group` started the AC less than 5 minutes ago. |
| `off: sensor stalled` | A stall is blocking the AC. |
| `off: no room reading` | No room has a valid reading for the stall window. |
| `locked` | Control is locked. |
| `locked: off, <reason>` | Control is locked and a stall, an open window or an away house turned the AC off. |
| `off: user turned the room off` | You set the room climate entity to `off`. |
| `off: night hold: AC does not start` | `night_plan_act` is on and the night plan is `hold`. |

## Repair issues

HVAC Horizon raises three repair issues. None has a fix flow. Each clears when its cause ends.

| Issue | Key | Raised when | Check |
| :-- | :-- | :-- | :-- |
| `HVAC Horizon sensors not updating` | `sensors_stalled` | A room or outdoor sensor is stalled. | The sensors listed in the issue. |
| `HVAC Horizon heater is not warming the room` | `heating_failure` | The heater has been on for 60 minutes, and the room rose less than 0.5 °C. | The heater, its plug and its switch. |
| `HVAC Horizon mode does not match` | `mode_mismatch` | The AC mode differs from the last mode written, and the setpoint did not change. | The AC unit and any other automation that changes its mode. |

:::see-also
- [[guides/read-the-forecast]]: what the Status sensor shows while the model learns.
:::
