"""Outdoor temperature series from a weather entity's hourly forecast. No Home Assistant imports.

The series holds one value per minute from a start time. The value at the start equals the measured outdoor reading.
Past the last forecast hour the series holds the last value.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from collections.abc import Callable
from datetime import date, datetime, tzinfo
from typing import Any

ANCHOR_DECAY_H = 3.0  # the offset between the sensor and the forecast fades to zero over this long
MAX_SERIES_H = 96  # a series covers at most this many hours


BIAS_BINS = 24  # one bias per hour of day
BIAS_MIN_PAIRS = 3  # a bin with fewer pairs uses the overall bias
BIAS_ALPHA = 0.2  # weight of a new pair once a bin holds a few; the first pairs weigh 1 / (count + 1)
BIAS_STEP_MAX_K = 0.5  # one pair moves a bin by at most this much
BIAS_LIMIT_K = 8.0  # the bias stays within plus or minus this


@dataclass(frozen=True)
class HourlyPoint:
    """One hourly forecast entry. `t` is epoch seconds."""

    t: float
    temp_c: float
    condition: str | None


def _parse_time(value: Any, tz: tzinfo) -> datetime | None:
    """A forecast datetime as an aware datetime. A string or datetime without an offset is read as local time in `tz`."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=tz) if value.tzinfo is None else value


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def parse_hourly(items: list[dict], tz: tzinfo) -> list[HourlyPoint]:
    """Hourly points sorted by time. An item without a parseable datetime or a numeric temperature is skipped."""
    points = []
    for item in items:
        when = _parse_time(item.get("datetime"), tz)
        temp = _number(item.get("temperature"))
        if when is None or temp is None:
            continue
        condition = item.get("condition")
        points.append(HourlyPoint(when.timestamp(), temp, condition if isinstance(condition, str) else None))
    points.sort(key=lambda p: p.t)
    return points


def _interpolate(times: list[float], temps: list[float], t: float) -> float:
    if t <= times[0]:
        return temps[0]
    if t >= times[-1]:
        return temps[-1]
    i = bisect.bisect_right(times, t)
    span = times[i] - times[i - 1]
    return temps[i - 1] + (temps[i] - temps[i - 1]) * (t - times[i - 1]) / span


class OutdoorSeries:
    """Outdoor temperature per minute from a start time. Call it with minutes from the start."""

    def __init__(self, values: list[float]) -> None:
        if not values:
            raise ValueError("a series needs at least one value")
        self._values = values

    def __call__(self, minute: float) -> float:
        index = int(minute)
        return self._values[max(0, min(len(self._values) - 1, index))]


def build_series(
    points: list[HourlyPoint], start: float, anchor_c: float | None, bias_at: Callable[[float], float] | None = None,
) -> OutdoorSeries | None:
    """The series from `start` (epoch seconds), linear between hours.

    With `anchor_c` set, the difference between it and the forecast at `start` is added and fades to zero over
    ANCHOR_DECAY_H hours, so the series starts at the measured reading. A forecast that starts before `start` is
    interpolated at `start`. `bias_at` maps epoch seconds to the sensor-minus-forecast bias in kelvin. It is added to
    the forecast at each time, and the anchor fades toward the corrected forecast. Returns None when there are no
    points or the last point is before `start`.
    """
    if not points or points[-1].t < start:
        return None
    times = [p.t for p in points]
    temps = [p.temp_c for p in points]
    bias = bias_at or (lambda t: 0.0)
    offset = 0.0 if anchor_c is None else anchor_c - (_interpolate(times, temps, start) + bias(start))
    decay_min = ANCHOR_DECAY_H * 60.0
    length = int(min(MAX_SERIES_H * 60, max(decay_min, (times[-1] - start) / 60.0))) + 1
    values = [
        _interpolate(times, temps, start + 60.0 * m) + bias(start + 60.0 * m) + offset * max(0.0, 1.0 - m / decay_min)
        for m in range(length)
    ]
    return OutdoorSeries(values)


def condition_now(points: list[HourlyPoint], now: float) -> str | None:
    """The condition of the hourly entry at or before `now`, or of the first entry when all are later."""
    if not points:
        return None
    current = points[0]
    for point in points:
        if point.t > now:
            break
        current = point
    return current.condition


def temperature_now(points: list[HourlyPoint], now: float) -> float | None:
    """The raw forecast temperature at `now`, interpolated between hours. None when `now` is outside the forecast span."""
    if not points or now < points[0].t or now > points[-1].t:
        return None
    return _interpolate([p.t for p in points], [p.temp_c for p in points], now)


def night_low(items: list[dict], today: date, tz: tzinfo) -> float | None:
    """The coming night's low from daily forecast items: `templow` of the first item dated today or later that has one.

    Today's `templow` is null on some integrations once the day has started. Then the next day's low applies.
    """
    dated = []
    for item in items:
        when = _parse_time(item.get("datetime"), tz)
        low = _number(item.get("templow"))
        if when is not None and low is not None and when.astimezone(tz).date() >= today:
            dated.append((when, low))
    return min(dated, key=lambda pair: pair[0])[1] if dated else None


@dataclass(frozen=True)
class OutdoorBias:
    """Sensor minus forecast, as an exponentially weighted mean per hour of day and overall."""

    means: tuple[float, ...] = (0.0,) * BIAS_BINS
    counts: tuple[int, ...] = (0,) * BIAS_BINS
    overall: float = 0.0
    total: int = 0

    def for_hour(self, hour: int) -> float:
        """The bias for an hour of day: its own bin with at least BIAS_MIN_PAIRS pairs, else the overall bias."""
        bin_ = hour % BIAS_BINS
        return self.means[bin_] if self.counts[bin_] >= BIAS_MIN_PAIRS else self.overall

    def record(self, sensor_c: float, forecast_c: float, hour: int) -> OutdoorBias:
        """The bias after one pair. A pair moves its bin and the overall mean by at most BIAS_STEP_MAX_K."""
        bin_ = hour % BIAS_BINS
        diff = sensor_c - forecast_c
        # An empty bin starts from the overall mean.
        start = self.means[bin_] if self.counts[bin_] else self.overall
        means, counts = list(self.means), list(self.counts)
        means[bin_] = _moved(start, diff, self.counts[bin_])
        counts[bin_] += 1
        return OutdoorBias(tuple(means), tuple(counts), _moved(self.overall, diff, self.total), self.total + 1)

    def to_dict(self) -> dict[str, Any]:
        return {"means": list(self.means), "counts": list(self.counts), "overall": self.overall, "total": self.total}

    @classmethod
    def from_dict(cls, data: Any) -> OutdoorBias:
        """Restore from stored data. Missing or malformed data gives an empty bias."""
        try:
            means = [max(-BIAS_LIMIT_K, min(BIAS_LIMIT_K, float(v))) for v in data["means"]]
            counts = [max(0, int(v)) for v in data["counts"]]
            if len(means) != BIAS_BINS or len(counts) != BIAS_BINS:
                return cls()
            overall = max(-BIAS_LIMIT_K, min(BIAS_LIMIT_K, float(data["overall"])))
            return cls(tuple(means), tuple(counts), overall, max(0, int(data["total"])))
        except (KeyError, TypeError, ValueError):
            return cls()


def _moved(mean: float, diff: float, count: int) -> float:
    """`mean` moved toward `diff` by the weight for `count` earlier pairs, at most BIAS_STEP_MAX_K, within the limit."""
    step = max(BIAS_ALPHA, 1.0 / (count + 1)) * (diff - mean)
    step = max(-BIAS_STEP_MAX_K, min(BIAS_STEP_MAX_K, step))
    return max(-BIAS_LIMIT_K, min(BIAS_LIMIT_K, mean + step))
