"""Room rate coefficients (k, g, c), fitted offline from AC-off and AC-on intervals.

This is not Versatile Thermostat's Auto TPI, which adjusts controller gains online during each cycle.
The AC-off intervals give the heat gain toward outdoors (k) and the constant gain (g). The AC-on intervals,
with k and g held from the AC-off fit, give the cooling per kW (c). A forecast uses these coefficients when
they are present.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import MIN_OUTDOOR_SPREAD_K, Fit, NotEnoughData, Sample, _least_squares

MIN_STATE_SAMPLES = 12  # about one hour of 5-minute samples in each state


@dataclass(frozen=True)
class TpiFit:
    k: float  # heat-gain rate toward outdoors, 1/h, from the AC-off intervals
    g: float  # constant gain, K/h, from the AC-off intervals
    c: float  # cooling effect, K/h per kW, from the AC-on intervals
    samples_off: int
    samples_on: int

    def to_fit(self) -> Fit:
        """The same coefficients as a model fit, so the forecast can use them."""
        return Fit(k=self.k, c=self.c, g=self.g, samples=self.samples_off + self.samples_on)


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
