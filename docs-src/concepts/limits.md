---
title: Limits of the model
summary: What the room model assumes, what it leaves out, and which parts of control are untested on hardware.
section: concepts
order: 20
audience: users
status: draft
---

## What the model assumes

The forecast and the fit use one equation for each room:

```text
dT/dt = k (T_out - T) - c P + g
```

- One thermal node per room. The air, walls, furniture and floor share one temperature. A room that cools fast at first and slowly later fits this model less well.
- Power is a proxy for cooling delivered. `P` is the electrical input in kW, and `c` is the effective room cooling per electrical kW, in K/h. The fit folds the unit's efficiency and the airflow into `c`. A correction moves `c` with the outdoor temperature and the room humidity when the readings support it.
- `k` and `g` are constants. `k` is the heat gain toward outdoors, and `g` is the steady internal gain from people and appliances.
- Rooms with several room sensors use their median. A sensor that stalls drops out of the median.

## What the model does not include

- No humidity node. Room humidity enters only as a correction to `c`, and only with a room humidity sensor. AC dry intervals and AC heat intervals are kept out of the cooling fit and have their own models.
- No solar node. Solar gain enters as a correction to `g`, and only with a window area set. It has no lag: sun that heats a wall for hours is not modelled.
- A correction is kept only when it lowers the error on the newest 30 % of the readings by 3 %. A room with under 12 hours of readings that carry a term has no correction for it.
- The heating model has one rate per device, constant at every outdoor temperature. A heat pump whose output falls on a cold day heats slower than the forecast.
- The drying model is one rate in percent per hour. It does not depend on the humidity or the temperature.
- No door or window opening, no occupancy change and no other heat source inside the fit. These show as noise, and the outlier filter drops the worst of them.
- No second room. Each entry models one room. Two rooms that exchange heat are two separate fits.

## What the forecast assumes

`forecast()` steps the model forward in 1-minute steps.

- The AC runs at a constant power for the whole forecast. A unit that cycles uses less energy, so the energy figure is an upper bound.
- Without a weather entity, the outdoor temperature stays at its current value. A room that is cooling toward evening is forecast as if the outdoor temperature did not fall.
- With a weather entity, the outdoor temperature follows the hourly forecast, corrected by the learned difference between the outdoor sensor and the forecast. The forecast is then as good as the weather forecast for the site.
- The price slots set the cost. A minute with no slot has no cost.
- The forecast stops after 12 hours and reports `not reached within 12 hours` when the room settles above the target.

## What this means for a reading

- The forecast is most reliable on a day like the days in the stored samples. A heatwave or a cold snap lies outside the fitted range.
- The range between `minutes_low` and `minutes_high` comes from the standard error of `c`. It does not include the errors listed above. The `range_coverage_pct` attribute of the Forecast error sensor shows how often past cool-downs fell inside it.
- The fit needs at least 3 hours of AC on and off time over 3 runs. Until then the status reads `learning`.

## Known limits of control

- Setpoint regulation covers the first AC. A second AC and fleet climates get the plain target.
- A PID heater settles up to 0.5 K below the heat target, because it stops one deadband below it.
- In `dry` the room thermostat shows one temperature, the cool target.
- The room thermostat has no `fan_only` mode and no humidity target. Its fan and swing modes are those of the first AC.
- Dry mode has fixed humidity limits: on above 60 %, off at 57 %.
- The Heatwave sensor is information only. It changes no device command.
- Two profiles of the simulation suite fail by design and are marked as expected failures: an 8 kW AC overshoots a small room, and the small unit's fitted rate collapses in `economy` mode with two ACs.
- The watchdog and the tick run in the same Home Assistant process. A process that hangs or loses power runs neither. A device that must fail off needs its own thermostat or timer.
- `commands_unconfirmed` reports a device that did not follow a command. It does not act on it, apart from the command that each tick sends again.

## What has run on real devices

| Device or function | Unit tests | Simulated room | Real device |
| :-- | :-- | :-- | :-- |
| AC cooling through a climate entity, with a power sensor | yes | yes | one installation |
| Heater switch, PID and TPI | yes | yes | no |
| AC heat mode and dry mode | yes | yes | no |
| Second AC and fleet devices | yes | yes | no |
| Valve and TRV offset | yes | yes | no |
| Boiler, fan, humidifier, dehumidifier, cover | yes | no | no |
| Window actuator and whole-house fan | yes | yes | no |
| Relay test | yes | yes | no |
| Load shedding | yes | no | no |

A row with `no` under Real device is untested on hardware. Start it in observe-only and watch the first runs.

:::see-also
- [[concepts/design]]: the model, the forecast and the control decision.
- [[reference/sensors]]: the fit attributes and the forecast range sensors.
:::
