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
- Power is a proxy for cooling delivered. `P` is the electrical input in kW, and `c` is the effective room cooling per electrical kW, in K/h. The fit folds the unit's efficiency, the outdoor temperature effect on that efficiency and the airflow into one constant `c`.
- `k` and `g` are constants. `k` is the heat gain toward outdoors, and `g` is the steady internal gain from people and appliances.
- Rooms with several room sensors use their median. A sensor that stalls drops out of the median.

## What the model does not include

- No humidity node. The fit ignores latent load. Dry mode and humid days change the room differently from the fit. AC dry intervals and AC heat intervals are kept out of the cooling fit.
- No solar node. Sun on the windows changes the room, but the fit sees it only as part of `g` and `k`. The solar gain sensor and the cover action use a separate clear-sky model and do not feed the fit.
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
- The range between `minutes_low` and `minutes_high` comes from the standard error of `c`. It does not include the errors listed above.
- The fit needs at least 3 hours of AC on and off time over 3 runs. Until then the status reads `learning`.

## Known limits of control

- Setpoint regulation covers the first AC. A second AC and fleet climates get the plain target.
- A PID heater settles up to 0.5 K below the heat target, because it stops one deadband below it.
- In `auto` and `dry` the room thermostat shows one temperature, the cool threshold. It has no separate low and high target.
- Dry mode has fixed humidity limits: on above 60 %, off at 57 %.
- The Heatwave sensor is information only. It changes no device command.
- Two profiles of the simulation suite fail by design and are marked as expected failures: an 8 kW AC overshoots a small room, and the small unit's fitted rate collapses in `economy` mode with two ACs.
- The relay test, the valve and the radiator residual heat have unit and simulation tests only. They have not run on real hardware.

:::see-also
- [[concepts/design]]: the model, the forecast and the control decision.
- [[reference/sensors]]: the fit attributes and the forecast range sensors.
:::
