"""Forecast time and cost to reach a setpoint from a fitted room model.

Assumptions, stated so the output can be checked:
- The AC runs at a constant power, `running_power_kw`, for the whole forecast.
  Real units cycle, so the forecast is an upper bound on energy when the
  thermostat cycles and a lower bound on time when it modulates.
- Outdoor temperature stays at its current value, unless an outdoor series is given. The series then sets it per minute.
- The price is the value of the slot that contains each minute.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace

from .model import Fit

STEP_MIN = 1.0  # step length, minutes; each step is solved exactly, so this sets only the time resolution
HORIZON_MIN = 12 * 60.0  # default: give up after 12 hours
MIN_RANGE_C = 1e-6  # K/h per kW; floor for c minus one standard error
SMALL_KDT = 1e-9  # below this k * dt the exact step uses its series limit


@dataclass(frozen=True)
class PriceSlot:
    """A price interval, in minutes from now. `price_per_kwh` is in the currency of the tariff."""

    start_min: float
    duration_min: float
    price_per_kwh: float


@dataclass(frozen=True)
class Forecast:
    reachable: bool
    minutes: float | None  # time to reach the setpoint, None when unreachable
    energy_kwh: float | None  # AC energy used until the setpoint, None when unreachable
    cost: float | None  # energy priced slot by slot, None when a slot is missing or unreachable
    equilibrium_c: float  # temperature the room settles at under this power
    reason: str  # short text for the sensor attribute


def _equilibrium_c(fit: Fit, outdoor_c: float, running_power_kw: float) -> float:
    """Temperature where the room stops changing under this power.

    With k > 0 this is outdoor + (g - c P) / k. With k = 0 the room keeps moving:
    +inf when the net rate is still a heating rate, -inf when it cools without limit.
    """
    net = fit.g - fit.c * running_power_kw
    if fit.k <= 0:
        return math.inf if net >= 0 else -math.inf
    return outdoor_c + net / fit.k


def _step_temperature(fit: Fit, temp_c: float, outdoor_c: float, running_power_kw: float, dt_h: float) -> float:
    """Room temperature after `dt_h` hours. Exact for constant coefficients, outdoor temperature and power.

    T(t+dt) = T_eq + (T - T_eq) * exp(-k dt) with T_eq = T_out + (g - c P) / k. It is evaluated as
    T + (k (T_out - T) + g - c P) * phi with phi = (1 - exp(-k dt)) / k, which is the same value and
    stays finite as k goes to 0, where phi becomes dt and the update is linear.
    """
    rate = fit.k * (outdoor_c - temp_c) + fit.g - fit.c * running_power_kw
    kdt = fit.k * dt_h
    phi = dt_h if kdt < SMALL_KDT else -math.expm1(-kdt) / fit.k
    return temp_c + rate * phi


def _lowest_outdoor(series: Callable[[float], float], horizon_min: float, every_min: float = 30.0) -> float:
    """The lowest series value over the first `horizon_min` minutes, sampled every `every_min`."""
    return min(series(i * every_min) for i in range(int(horizon_min // every_min) + 1))


def price_at(slots: list[PriceSlot], minute: float) -> float | None:
    for slot in slots:
        if slot.start_min <= minute < slot.start_min + slot.duration_min:
            return slot.price_per_kwh
    return None


def forecast(
    fit: Fit,
    temp_c: float,
    target_c: float,
    outdoor_c: float,
    running_power_kw: float,
    prices: list[PriceSlot],
    horizon_min: float = HORIZON_MIN,
    outdoor_series: Callable[[float], float] | None = None,
    fit_at: Callable[[float, float], Fit] | None = None,
) -> Forecast:
    """Return the time, energy and cost to cool from `temp_c` down to `target_c`.

    `outdoor_series` maps minutes from now to the outdoor temperature. Each step uses the value at its midpoint.
    Without it, `outdoor_c` holds for the whole run. With it, the run is unreachable only when the room cannot
    reach the setpoint at the lowest outdoor value of the horizon.

    `fit_at` maps minutes from now and the outdoor temperature to the fit for that step. It carries the corrections
    for sun, outdoor temperature and humidity. Without it `fit` holds for the whole run.
    """
    varying = fit_at
    if varying is not None:
        fit = varying(0.0, outdoor_c)
    if running_power_kw <= 0 or fit.c <= 0:
        return Forecast(False, None, None, None, math.nan, "no cooling effect learned")

    if temp_c <= target_c:
        return Forecast(True, 0.0, 0.0, 0.0, math.nan, "already at or below setpoint")

    t_eq = _equilibrium_c(fit, outdoor_c, running_power_kw)
    best_eq = t_eq if outdoor_series is None else _equilibrium_c(fit, _lowest_outdoor(outdoor_series, horizon_min), running_power_kw)
    if best_eq >= target_c:
        return Forecast(
            False, None, None, None, t_eq,
            f"unreachable: room settles at {t_eq:.1f} C under this power",
        )

    energy = 0.0
    cost: float | None = 0.0
    temp = temp_c
    minute = 0.0
    while temp > target_c:
        if minute >= horizon_min:
            return Forecast(False, None, None, None, t_eq, f"not reached within {horizon_min / 60.0:g} hours")
        dt_h = STEP_MIN / 60.0
        step_energy = running_power_kw * dt_h
        energy += step_energy
        if cost is not None:
            price = price_at(prices, minute)
            cost = None if price is None else cost + step_energy * price
        outdoor_step = outdoor_c if outdoor_series is None else outdoor_series(minute + STEP_MIN / 2.0)
        step_fit = fit if varying is None else varying(minute + STEP_MIN / 2.0, outdoor_step)
        temp = max(_step_temperature(step_fit, temp, outdoor_step, running_power_kw, dt_h), target_c - 1e-9)
        minute += STEP_MIN

    return Forecast(True, minute, energy, cost, t_eq, "reachable")


def forecast_range(
    fit: Fit,
    temp_c: float,
    target_c: float,
    outdoor_c: float,
    running_power_kw: float,
    prices: list[PriceSlot],
    horizon_min: float = HORIZON_MIN,
    outdoor_series: Callable[[float], float] | None = None,
    fit_at: Callable[[float, float], Fit] | None = None,
) -> tuple[float | None, float | None]:
    """Minutes to the setpoint with c one standard error above (low) and below (high) the fit.

    Both are None when the fit has no standard error of c. `high` is also None when the smaller c cannot
    reach the setpoint within the horizon. The smaller c is clamped to MIN_RANGE_C.
    """
    if fit.c_stderr is None:
        return None, None
    stderr = fit.c_stderr
    faster = replace(fit, c=fit.c + stderr)
    slower = replace(fit, c=max(fit.c - stderr, MIN_RANGE_C))
    faster_at = slower_at = None
    if fit_at is not None:
        def faster_at(minute: float, outdoor: float) -> Fit:
            at = fit_at(minute, outdoor)
            return replace(at, c=at.c + stderr)

        def slower_at(minute: float, outdoor: float) -> Fit:
            at = fit_at(minute, outdoor)
            return replace(at, c=max(at.c - stderr, MIN_RANGE_C))

    low = forecast(faster, temp_c, target_c, outdoor_c, running_power_kw, prices, horizon_min, outdoor_series, faster_at).minutes
    high = forecast(slower, temp_c, target_c, outdoor_c, running_power_kw, prices, horizon_min, outdoor_series, slower_at).minutes
    return low, high
