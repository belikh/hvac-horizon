"""Room rate coefficients (k, g, c) from the stored history, and an online cooling gain.

learn_room_rates fits k and g from the AC-off intervals, then c from the AC-on intervals. It runs at each refit.

adjust_cool_gain runs once per interval between two readings. It scales the fitted c by a gain that follows the
AC's measured effect on the room. In cool mode a room that warms faster than predicted lowers the gain, and a room
that cools faster raises it. The gain stays between GAIN_MIN and GAIN_MAX and moves at most GAIN_STEP_MAX per interval.
The forecast and the pre-cool estimate use the scaled c. The AC's setpoint and mode are not changed by these values.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import MIN_OUTDOOR_SPREAD_K, Fit, NotEnoughData, Sample, _least_squares

MIN_STATE_SAMPLES = 12  # about one hour of 5-minute samples in each state
GAIN_MIN = 0.5  # the cool gain never drops below half the fitted cooling
GAIN_MAX = 1.5  # the cool gain never rises above 1.5 times the fitted cooling
GAIN_RATE = 0.2  # share of the gap between the gain and the implied gain applied in one interval
GAIN_STEP_MAX = 0.02  # largest change of the cool gain in one interval
MIN_AC_DROP_K = 0.01  # cooling per interval below this, at gain 1.0, says nothing about the gain


@dataclass(frozen=True)
class TpiFit:
    k: float  # heat-gain rate toward outdoors, 1/h, from the AC-off intervals
    g: float  # constant gain, K/h, from the AC-off intervals
    c: float  # cooling effect, K/h per kW, from the AC-on intervals, before the online gain
    samples_off: int
    samples_on: int

    def to_fit(self, cool_gain: float = 1.0) -> Fit:
        """The coefficients as a model fit. `cool_gain` scales c; the forecast passes the online gain."""
        return Fit(k=self.k, c=self.c * cool_gain, g=self.g, samples=self.samples_off + self.samples_on)


def adjust_cool_gain(gain: float, sample: Sample, tpi: TpiFit) -> float:
    """Return the cool gain after one measured interval.

    The implied gain is the value that would have predicted the measured room change. The gain moves toward it by
    GAIN_RATE of the gap, by at most GAIN_STEP_MAX, and stays within GAIN_MIN to GAIN_MAX. The gain comes back
    unchanged when the interval has no AC power or too little predicted cooling.
    """
    if sample.power_kw <= 0 or sample.minutes <= 0 or tpi.c <= 0:
        return gain
    dt_h = sample.minutes / 60.0
    drop_at_unit_gain = tpi.c * sample.power_kw * dt_h  # K removed in the interval when the gain is 1.0
    if drop_at_unit_gain < MIN_AC_DROP_K:
        return gain
    rise_without_ac = (tpi.k * (sample.outdoor_c - sample.temp_c) + tpi.g) * dt_h  # K, the AC off
    measured = sample.temp_next_c - sample.temp_c  # K, as the room changed
    implied = (rise_without_ac - measured) / drop_at_unit_gain
    step = max(-GAIN_STEP_MAX, min(GAIN_STEP_MAX, GAIN_RATE * (implied - gain)))
    return min(GAIN_MAX, max(GAIN_MIN, gain + step))


HEAT_RATE_MAX = 20.0  # K/h at full heater output; a learned heat rate above this is clamped


def learn_heat_rate(pairs: list[tuple[Sample, float]], k: float, g: float) -> float:
    """Heat rate in K/h at 100 percent heater output, from the AC-off intervals in which the heater ran.

    Each interval gives: measured rise per hour minus the passive rise (k * (outdoor - room) + g) equals h * duty.
    The fit is least squares through zero. Raises NotEnoughData with fewer than MIN_STATE_SAMPLES heating intervals.
    """
    rows: list[tuple[float, float]] = []
    for sample, duty_pct in pairs:
        if sample.minutes <= 0 or sample.power_kw > 0 or duty_pct <= 0:
            continue
        duty = duty_pct / 100.0
        rise = (sample.temp_next_c - sample.temp_c) / (sample.minutes / 60.0)
        rows.append((duty, rise - (k * (sample.outdoor_c - sample.temp_c) + g)))
    if len(rows) < MIN_STATE_SAMPLES:
        raise NotEnoughData(f"need {MIN_STATE_SAMPLES} heating intervals, got {len(rows)}")
    den = sum(x * x for x, _ in rows)
    if den <= 0:
        raise NotEnoughData("heater duty is zero in every interval; cannot fit the heat rate")
    return min(HEAT_RATE_MAX, max(0.0, sum(x * y for x, y in rows) / den))


def heat_up_minutes(room_c: float, target_c: float, outdoor_c: float, k: float, g: float, heat_rate: float, limit_min: float = 360.0) -> float | None:
    """Minutes for the room to reach `target_c` at full heater output, stepping the room model one minute at a time.

    Returns 0 when the room is already at the target. Returns None when it does not get there within `limit_min`.
    """
    if room_c >= target_c:
        return 0.0
    temp = room_c
    minutes = 0.0
    while minutes < limit_min:
        rate = k * (outdoor_c - temp) + g + heat_rate  # K per hour, heater at full output
        if rate <= 0:
            return None
        temp += rate / 60.0
        minutes += 1.0
        if temp >= target_c:
            return minutes
    return None


MIN_HEAT_RISE_K = 0.01  # heating per interval below this, at gain 1.0, says nothing about the heat gain


def adjust_heat_gain(gain: float, sample: Sample, duty_pct: float, heat_rate: float, k: float, g: float) -> float:
    """Return the heat gain after one interval with the heater running and the AC off.

    The heat gain scales the learned heat rate. The implied gain is the value that would have predicted the measured
    room change. The gain moves toward it by GAIN_RATE of the gap, by at most GAIN_STEP_MAX, and stays within GAIN_MIN
    to GAIN_MAX. The gain comes back unchanged when the AC ran, the heater was off or the heat rate is not known.
    """
    if sample.power_kw > 0 or sample.minutes <= 0 or duty_pct <= 0 or heat_rate <= 0:
        return gain
    dt_h = sample.minutes / 60.0
    heat_at_unit_gain = heat_rate * (duty_pct / 100.0) * dt_h  # K added in the interval when the gain is 1.0
    if heat_at_unit_gain < MIN_HEAT_RISE_K:
        return gain
    passive = (k * (sample.outdoor_c - sample.temp_c) + g) * dt_h  # K, the room drifts without the heater
    measured = sample.temp_next_c - sample.temp_c  # K, as the room changed
    implied = (measured - passive) / heat_at_unit_gain
    step = max(-GAIN_STEP_MAX, min(GAIN_STEP_MAX, GAIN_RATE * (implied - gain)))
    return min(GAIN_MAX, max(GAIN_MIN, gain + step))


def learn_room_rates(samples: list[Sample]) -> TpiFit:
    """Fit the room's k and g from the AC-off intervals, then c from the AC-on intervals.

    k is the heat-gain rate toward outdoors and g the constant gain. c is the cooling per kW.
    The fit runs on the stored history at each refit. It does not change the AC's controller gains.
    Raises NotEnoughData when a state has too few samples or the temperatures barely vary.
    """
    usable = [s for s in samples if s.minutes > 0]
    off = [s for s in usable if s.power_kw <= 0]
    on = [s for s in usable if s.power_kw > 0]
    if len(off) < MIN_STATE_SAMPLES or len(on) < MIN_STATE_SAMPLES:
        raise NotEnoughData(f"need {MIN_STATE_SAMPLES} AC-off and AC-on samples, got {len(off)} and {len(on)}")
    x1 = [s.outdoor_c - s.temp_c for s in usable]
    if max(x1) - min(x1) < MIN_OUTDOOR_SPREAD_K:
        raise NotEnoughData("outdoor minus room temperature hardly varied; cannot fit heat gain")

    rows = [[s.outdoor_c - s.temp_c, 1.0] for s in off]
    y = [(s.temp_next_c - s.temp_c) / (s.minutes / 60.0) for s in off]
    if max(r[0] for r in rows) - min(r[0] for r in rows) < MIN_OUTDOOR_SPREAD_K:
        raise NotEnoughData("AC-off intervals hardly varied in outdoor minus room temperature")
    k, g = _least_squares(rows, y, [0, 1])
    k = max(k, 0.0)

    # With k and g fixed, each AC-on interval gives c = (k x1 + g - y) / P. Least squares over the on intervals.
    num = 0.0
    den = 0.0
    for s in on:
        rise = (s.temp_next_c - s.temp_c) / (s.minutes / 60.0)
        predicted_without_ac = k * (s.outdoor_c - s.temp_c) + g
        num += s.power_kw * (predicted_without_ac - rise)
        den += s.power_kw * s.power_kw
    if den <= 0:
        raise NotEnoughData("AC power never varied; cannot separate cooling from heat gain")
    c = max(num / den, 0.0)
    return TpiFit(k=k, g=g, c=c, samples_off=len(off), samples_on=len(on))
