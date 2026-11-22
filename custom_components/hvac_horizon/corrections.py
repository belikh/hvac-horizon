"""Corrections to the room model for sun, outdoor temperature and room humidity.

The room model has a constant gain g and a constant cooling effect c. Three things move them in a real room:

    g_eff = g + s * S                                   S: solar gain on the window, kW
    c_eff = c + c_out * (T_out - REF_OUTDOOR_C)         an AC cools less per kW on a hot day
              + c_rh * (RH - REF_HUMIDITY_PCT)          and less when the room air is humid

Each correction is fitted on what the base model leaves unexplained. A correction is kept only when it has the sign
physics gives it and it lowers the error on the newest readings, which the fit did not see.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .model import Fit, Sample, _least_squares, NotEnoughData

REF_OUTDOOR_C = 30.0
REF_HUMIDITY_PCT = 50.0
MIN_CORRECTION_SAMPLES = 144  # 12 hours of readings that carry the term
HOLDOUT_SHARE = 0.3  # the newest share of the readings checks each correction
MIN_IMPROVEMENT = 0.03  # a correction must lower the holdout error by this share
MIN_C_SHARE = 0.2  # the corrected c stays at or above this share of c
TERMS = ("solar", "outdoor", "humidity")


@dataclass(frozen=True)
class Corrections:
    s: float = 0.0  # K/h per kW of solar gain
    c_out: float = 0.0  # change of c per K of outdoor temperature above REF_OUTDOOR_C
    c_rh: float = 0.0  # change of c per percent of room humidity above REF_HUMIDITY_PCT
    improvement_pct: float = 0.0  # how far the kept corrections lower the holdout error, percent

    @property
    def terms(self) -> tuple[str, ...]:
        return tuple(name for name, value in zip(TERMS, (self.s, self.c_out, self.c_rh)) if value != 0.0)


def apply(fit: Fit, corrections: Corrections | None, outdoor_c: float, solar_kw: float | None, humidity_pct: float | None) -> Fit:
    """The fit at these conditions: g and c moved by the corrections. Without corrections the fit is returned as it is."""
    if corrections is None or not corrections.terms:
        return fit
    g = fit.g + corrections.s * (solar_kw or 0.0)
    c = fit.c + corrections.c_out * (outdoor_c - REF_OUTDOOR_C)
    if humidity_pct is not None:
        c += corrections.c_rh * (humidity_pct - REF_HUMIDITY_PCT)
    return replace(fit, g=g, c=max(c, MIN_C_SHARE * fit.c))


def _column(term: str, s: Sample) -> float | None:
    if term == "solar":
        return s.solar_kw
    if term == "outdoor":
        return -s.power_kw * (s.outdoor_c - REF_OUTDOOR_C)
    return None if s.humidity_pct is None else -s.power_kw * (s.humidity_pct - REF_HUMIDITY_PCT)


def _residual(fit: Fit, s: Sample) -> float:
    rate = (s.temp_next_c - s.temp_c) / (s.minutes / 60.0)
    return rate - (fit.k * (s.outdoor_c - s.temp_c) - fit.c * s.power_kw + fit.g)


def _rmse(values: list[float]) -> float:
    return math.sqrt(sum(v * v for v in values) / len(values))


def _fit_terms(rows: list[tuple[list[float], float]], count: int) -> list[float] | None:
    try:
        return _least_squares([x for x, _ in rows], [y for _, y in rows], list(range(count)))
    except NotEnoughData:
        return None


def _physical(term: str, value: float) -> bool:
    return value > 0 if term == "solar" else value < 0


def learn_corrections(samples: list[Sample], fit: Fit) -> Corrections:
    """The corrections that lower the error of `fit` on the newest readings. `samples` are in time order.

    Terms are added one at a time, the best first. A term needs MIN_CORRECTION_SAMPLES readings that carry it.
    """
    usable = [s for s in samples if s.minutes > 0]
    kept: list[str] = []
    best_error: float | None = None
    base_error: float | None = None
    while True:
        best: tuple[float, str] | None = None
        for term in TERMS:
            if term in kept:
                continue
            terms = [*kept, term]
            rows = []
            for s in usable:
                x = [_column(name, s) for name in terms]
                if all(v is not None for v in x):
                    rows.append((x, _residual(fit, s)))
            if len(rows) < MIN_CORRECTION_SAMPLES:
                continue
            split = int(len(rows) * (1.0 - HOLDOUT_SHARE))
            train, test = rows[:split], rows[split:]
            coef = _fit_terms(train, len(terms))
            if coef is None or not all(_physical(name, v) for name, v in zip(terms, coef)):
                continue
            before = _rmse([y for _, y in test])
            after = _rmse([y - sum(v * c for v, c in zip(x, coef)) for x, y in test])
            reference = before if best_error is None else best_error
            if after <= reference * (1.0 - MIN_IMPROVEMENT) and (best is None or after < best[0]):
                best = (after, term)
                base_error = before if base_error is None else base_error
        if best is None:
            break
        best_error = best[0]
        kept.append(best[1])
    if not kept:
        return Corrections()
    rows = []
    for s in usable:
        x = [_column(name, s) for name in kept]
        if all(v is not None for v in x):
            rows.append((x, _residual(fit, s)))
    coef = _fit_terms(rows, len(kept))
    if coef is None or not all(_physical(name, v) for name, v in zip(kept, coef)):
        return Corrections()
    values = dict(zip(kept, coef))
    improvement = 0.0 if not base_error or best_error is None else 100.0 * (1.0 - best_error / base_error)
    return Corrections(values.get("solar", 0.0), values.get("outdoor", 0.0), values.get("humidity", 0.0), round(improvement, 1))
