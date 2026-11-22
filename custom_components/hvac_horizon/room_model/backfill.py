"""Find cool-downs in aligned samples and backtest the room model on them.

A cool-down is a run of AC-on intervals that starts at least `margin_c` above the target and reaches it.
The backtest fits the model on the samples before each cool-down, then predicts that cool-down.
"""

from __future__ import annotations

from dataclasses import dataclass

from .forecast import forecast, forecast_range
from .model import NotEnoughData, Sample, fit


@dataclass(frozen=True)
class Cooldown:
    """One cool-down: power on from `start` until the room reaches `target_c`."""

    start: int  # index of the first sample
    end: int  # index of the sample where the room first reaches the target
    start_c: float
    target_c: float
    actual_min: float
    mean_power_kw: float
    mean_outdoor_c: float


def _find_cooldowns(samples: list[Sample], target_c: float, margin_c: float = 1.0) -> list[Cooldown]:
    """Find AC-on runs that start at least `margin_c` above the target and reach it."""
    found = []
    i = 0
    n = len(samples)
    while i < n:
        s = samples[i]
        if s.power_kw <= 0 or s.temp_c < target_c + margin_c:
            i += 1
            continue
        j = i
        powers = []
        outdoors = []
        reached = None
        while j < n and samples[j].power_kw > 0:
            powers.append(samples[j].power_kw)
            outdoors.append(samples[j].outdoor_c)
            if samples[j].temp_next_c <= target_c:
                reached = j
                break
            j += 1
        if reached is not None:
            minutes = sum(samples[x].minutes for x in range(i, reached + 1))
            found.append(
                Cooldown(
                    start=i,
                    end=reached,
                    start_c=s.temp_c,
                    target_c=target_c,
                    actual_min=minutes,
                    mean_power_kw=sum(powers) / len(powers),
                    mean_outdoor_c=sum(outdoors) / len(outdoors),
                )
            )
        i = max(j, i + 1)
    return found


@dataclass(frozen=True)
class BacktestRun:
    start: int
    actual_min: float
    predicted_min: float | None
    error_min: float | None
    low_min: float | None = None  # the forecast range for this run, from the same earlier fit
    high_min: float | None = None


def backtest(samples: list[Sample], target_c: float, min_train: int = 60, margin_c: float = 1.0) -> list[BacktestRun]:
    """For each cool-down, fit only on samples before its start, then forecast it.

    No run uses data from its own cool-down or any later one, so the error is an honest forecast error.
    A run with too little earlier data is kept with `predicted_min` None.
    """
    out: list[BacktestRun] = []
    for cd in _find_cooldowns(samples, target_c, margin_c):
        if cd.start < min_train:
            out.append(BacktestRun(cd.start, cd.actual_min, None, None))
            continue
        try:
            f_fit = fit(samples[: cd.start])
        except NotEnoughData:
            out.append(BacktestRun(cd.start, cd.actual_min, None, None))
            continue
        inputs = dict(temp_c=cd.start_c, target_c=cd.target_c, outdoor_c=cd.mean_outdoor_c, running_power_kw=cd.mean_power_kw, prices=[])
        result = forecast(f_fit, **inputs)
        if result.reachable and result.minutes is not None:
            low, high = forecast_range(f_fit, **inputs)
            out.append(BacktestRun(cd.start, cd.actual_min, result.minutes, result.minutes - cd.actual_min, low, high))
        else:
            out.append(BacktestRun(cd.start, cd.actual_min, None, None))
    return out


def median_abs_error(runs: list[BacktestRun]) -> float | None:
    """Median of |predicted - actual| minutes over the runs that have a prediction."""
    errors = sorted(abs(r.error_min) for r in runs if r.error_min is not None)
    if not errors:
        return None
    mid = len(errors) // 2
    return errors[mid] if len(errors) % 2 else (errors[mid - 1] + errors[mid]) / 2


def range_coverage(runs: list[BacktestRun]) -> tuple[float | None, int]:
    """The percentage of runs whose measured minutes fell inside the forecast range, and the count of runs checked.

    A run counts when it has a low value. A run without a high value has no upper limit. The percentage is None
    with no such run.
    """
    checked = [r for r in runs if r.predicted_min is not None and r.low_min is not None]
    if not checked:
        return None, 0
    inside = sum(1 for r in checked if r.low_min <= r.actual_min and (r.high_min is None or r.actual_min <= r.high_min))
    return round(100.0 * inside / len(checked), 1), len(checked)


ACCURACY_PAIRS_LIMIT = 20  # runs listed in the accuracy_pairs attribute


def accuracy_pairs(runs: list[BacktestRun], limit: int = ACCURACY_PAIRS_LIMIT) -> list[dict[str, float]]:
    """The last `limit` runs that have a prediction, oldest first. Each entry holds predicted, measured and error in minutes.

    The error is predicted minus measured. Runs without a prediction are left out.
    """
    done = [r for r in runs if r.predicted_min is not None and r.error_min is not None]
    return [
        {
            "predicted_min": round(r.predicted_min, 1),
            "measured_min": round(r.actual_min, 1),
            "error_min": round(r.error_min, 1),
        }
        for r in done[-limit:]
    ]
