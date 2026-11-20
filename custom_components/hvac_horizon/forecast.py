"""Forecast time and cost to reach a setpoint from a fitted room model.

Assumptions, stated so the output can be checked:
- The AC runs at a constant power, `running_power_kw`, for the whole forecast.
  Real units cycle, so the forecast is an upper bound on energy when the
  thermostat cycles and a lower bound on time when it modulates.
- Outdoor temperature stays at its current value.
- The price is the value of the slot that contains each minute.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .model import Fit

STEP_MIN = 1.0  # integration step, minutes
HORIZON_MIN = 12 * 60.0  # give up after 12 hours


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


def equilibrium_c(fit: Fit, outdoor_c: float, running_power_kw: float) -> float:
    """Temperature where the room stops changing under this power.

    With k > 0 this is outdoor + (g - c P) / k. With k = 0 the room keeps moving:
    +inf when the net rate is still a heating rate, -inf when it cools without limit.
    """
    net = fit.g - fit.c * running_power_kw
    if fit.k <= 0:
        return math.inf if net >= 0 else -math.inf
    return outdoor_c + net / fit.k


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
) -> Forecast:
    """Return the time, energy and cost to cool from `temp_c` down to `target_c`."""
    if running_power_kw <= 0 or fit.c <= 0:
        return Forecast(False, None, None, None, math.nan, "no cooling effect learned")

    if temp_c <= target_c:
        return Forecast(True, 0.0, 0.0, 0.0, math.nan, "already at or below setpoint")

    t_eq = equilibrium_c(fit, outdoor_c, running_power_kw)
    if t_eq >= target_c:
        return Forecast(
            False, None, None, None, t_eq,
            f"unreachable: room settles at {t_eq:.1f} C under this power",
        )

    energy = 0.0
    cost: float | None = 0.0
    temp = temp_c
    minute = 0.0
    while temp > target_c:
        if minute >= HORIZON_MIN:
            return Forecast(False, None, None, None, t_eq, "not reached within 12 hours")
        dt_h = STEP_MIN / 60.0
        # Explicit Euler step. It is accurate for STEP_MIN well below the time constant 1/k.
        dtemp = (fit.k * (outdoor_c - temp) - fit.c * running_power_kw + fit.g) * dt_h
        step_energy = running_power_kw * dt_h
        energy += step_energy
        if cost is not None:
            price = price_at(prices, minute)
            cost = None if price is None else cost + step_energy * price
        temp = max(temp + dtemp, target_c - 1e-9)
        minute += STEP_MIN

    return Forecast(True, minute, energy, cost, t_eq, "reachable")
