"""Heating and drying models. They stand beside the cooling fit in model.py and reuse the room's k and g.

Heating: dT/dt = k (T_out - T) + g + h, where h is the heat rate in K/h. For the AC in heat mode h = c_heat * P.
Drying: the room humidity falls at a constant rate while the AC runs in dry mode.

Assumptions, stated so the output can be checked:
- The heater or the AC runs at full output and constant power for the whole forecast.
- The drying rate does not depend on the humidity or the temperature.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from .forecast import HORIZON_MIN, Forecast, PriceSlot, forecast, price_at
from .model import Fit, NotEnoughData, Sample

MIN_MODE_SAMPLES = 12  # one hour of 5-minute intervals
MIN_RATE = 1e-6


@dataclass(frozen=True)
class RateFit:
    """A rate fitted through zero: y = rate * x."""

    rate: float
    stderr: float | None
    samples: int


def _fit_through_zero(pairs: list[tuple[float, float]], min_samples: int = MIN_MODE_SAMPLES) -> RateFit:
    """Least squares of y = rate * x. Raises NotEnoughData with fewer than `min_samples` pairs or no variation in x."""
    if len(pairs) < min_samples:
        raise NotEnoughData(f"need {min_samples} intervals, have {len(pairs)}")
    den = sum(x * x for x, _ in pairs)
    if den <= 0:
        raise NotEnoughData("the device never ran in these intervals")
    rate = sum(x * y for x, y in pairs) / den
    stderr = None
    if len(pairs) > 1:
        variance = sum((y - rate * x) ** 2 for x, y in pairs) / (len(pairs) - 1)
        stderr = math.sqrt(variance / den)
    return RateFit(rate, stderr, len(pairs))


def learn_ac_heat(samples: list[Sample], k: float, g: float) -> RateFit:
    """Heating per kW of AC power, K/h per kW, from intervals in which the AC ran in heat mode.

    Each interval gives: measured rise per hour minus the passive rise (k * (outdoor - room) + g) equals c_heat * P.
    Raises NotEnoughData when the fitted rate is not positive.
    """
    pairs = [
        (s.power_kw, (s.temp_next_c - s.temp_c) / (s.minutes / 60.0) - (k * (s.outdoor_c - s.temp_c) + g))
        for s in samples
        if s.minutes > 0 and s.power_kw > 0
    ]
    found = _fit_through_zero(pairs)
    if found.rate <= MIN_RATE:
        raise NotEnoughData("heat mode did not warm the room in these intervals")
    return found


def heat_forecast(
    k: float,
    g: float,
    rate_k_per_h: float,
    power_kw: float | None,
    room_c: float,
    target_c: float,
    outdoor_c: float,
    prices: list[PriceSlot],
    horizon_min: float = HORIZON_MIN,
    outdoor_series: Callable[[float], float] | None = None,
) -> Forecast:
    """Minutes, energy and cost to warm the room to `target_c` at a heat rate of `rate_k_per_h`.

    `power_kw` is the electric power while heating. With None the energy and the cost are None.
    The cooling forecast does the work on mirrored temperatures: warming to a target is cooling of the negated values.
    """
    if room_c >= target_c:
        return Forecast(True, 0.0, 0.0, 0.0, math.nan, "already at or above the heat target")
    power = power_kw if power_kw and power_kw > 0 else 1.0
    mirrored = Fit(k=k, c=rate_k_per_h / power, g=-g)
    series = None if outdoor_series is None else (lambda minute: -outdoor_series(minute))
    found = forecast(mirrored, -room_c, -target_c, -outdoor_c, power, prices, horizon_min, series)
    settles = -found.equilibrium_c
    if not found.reachable:
        reason = found.reason if found.reason.startswith("not reached") else f"unreachable: room settles at {settles:.1f} C at full heat"
        return Forecast(False, None, None, None, settles, reason)
    known = power_kw is not None and power_kw > 0
    return Forecast(True, found.minutes, found.energy_kwh if known else None, found.cost if known else None, settles, "reachable")


def learn_dry(rates: list[tuple[float, float]]) -> RateFit:
    """The fall of room humidity in percent per hour while the AC dries, as a positive rate.

    `rates` holds (AC power, change of humidity per hour) for each dry interval. Raises NotEnoughData with too few
    intervals or when the humidity did not fall.
    """
    found = _fit_through_zero([(1.0, -change) for _, change in rates])
    if found.rate <= MIN_RATE:
        raise NotEnoughData("dry mode did not lower the humidity in these intervals")
    return found


def dry_forecast(rate_pct_per_h: float, humidity_pct: float, target_pct: float, power_kw: float | None, prices: list[PriceSlot], horizon_min: float = HORIZON_MIN) -> Forecast:
    """Minutes, energy and cost to dry the room from `humidity_pct` to `target_pct` at `rate_pct_per_h`."""
    if humidity_pct <= target_pct:
        return Forecast(True, 0.0, 0.0, 0.0, math.nan, "already at or below the humidity target")
    if rate_pct_per_h <= MIN_RATE:
        return Forecast(False, None, None, None, math.nan, "unreachable: dry mode does not lower the humidity")
    minutes = math.ceil(60.0 * (humidity_pct - target_pct) / rate_pct_per_h)
    if minutes > horizon_min:
        return Forecast(False, None, None, None, math.nan, f"not reached within {horizon_min / 60.0:g} hours")
    if not power_kw or power_kw <= 0:
        return Forecast(True, float(minutes), None, None, math.nan, "reachable")
    energy = power_kw * minutes / 60.0
    cost: float | None = 0.0
    for minute in range(minutes):
        price = price_at(prices, float(minute))
        if price is None:
            cost = None
            break
        cost += power_kw / 60.0 * price
    return Forecast(True, float(minutes), energy, cost, math.nan, "reachable")
