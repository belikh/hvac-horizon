"""Control planning: when to start cooling so the room reaches its setpoint by a deadline, at the lowest cost.

Pure logic. The integration turns the plan into climate service calls.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..room_model.forecast import Forecast, PriceSlot, forecast
from ..room_model.model import Fit

START_STEP_MIN = 5.0  # candidate start times are spaced this far apart


@dataclass(frozen=True)
class Plan:
    start_in_min: float  # minutes from now to switch the AC on
    forecast: Forecast  # the forecast for that start
    reason: str


def _shift(slots: list[PriceSlot], offset_min: float) -> list[PriceSlot]:
    """Price slots as seen from a start `offset_min` minutes in the future."""
    return [PriceSlot(s.start_min - offset_min, s.duration_min, s.price_per_kwh) for s in slots]


def _plan_precool(
    fit: Fit,
    room_c: float,
    setpoint_c: float,
    outdoor_c: float,
    running_power_kw: float,
    deadline_min: float,
    prices: list[PriceSlot],
    outdoor_series: Callable[[float], float] | None = None,
) -> Plan | None:
    """Pick the cheapest start that still reaches the setpoint by `deadline_min` minutes from now.

    Returns None when no start reaches the setpoint in time. The earliest possible start is 0.
    `outdoor_series` maps minutes from now to the outdoor temperature. A start at `start` sees it shifted by `start`.
    """
    best: Plan | None = None
    start = 0.0
    while start <= deadline_min:
        shifted = None if outdoor_series is None else (lambda m, s=start: outdoor_series(m + s))
        f = forecast(
            fit, room_c, setpoint_c, outdoor_c if shifted is None else shifted(0.0), running_power_kw,
            _shift(prices, start), outdoor_series=shifted,
        )
        if f.reachable and f.minutes is not None and start + f.minutes <= deadline_min:
            # Rank by cost, then prefer the later start. A missing price counts as zero, so with no
            # prices every feasible start ties and the latest one wins.
            key = (f.cost or 0.0, -start)
            if best is None or key < (best.forecast.cost or 0.0, -best.start_in_min):
                best = Plan(start_in_min=start, forecast=f, reason=f"start in {start:.0f} min, arrives in {start + f.minutes:.0f} min")
        start += START_STEP_MIN
    if best is None:
        return None
    return best


def precool_start_in_min(
    fit: Fit,
    room_c: float,
    setpoint_c: float,
    outdoor_c: float,
    running_power_kw: float,
    minutes_to_deadline: float | None,
    prices: list[PriceSlot],
    outdoor_series: Callable[[float], float] | None = None,
) -> float | None:
    """Minutes from now to start pre-cooling, or None when there is no deadline or no feasible plan.

    A deadline is the time the room should reach the setpoint. With no deadline the planner stays idle.
    """
    if minutes_to_deadline is None or minutes_to_deadline <= 0:
        return None
    plan = _plan_precool(fit, room_c, setpoint_c, outdoor_c, running_power_kw, minutes_to_deadline, prices, outdoor_series)
    return None if plan is None else plan.start_in_min
