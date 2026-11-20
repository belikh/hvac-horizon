"""Learn a room's cooling response from measured AC power and room temperature.

The model is a single-node energy balance:

    dT/dt = k * (T_out - T) - c * P

where
    T      room temperature, degrees C
    T_out  outdoor temperature, degrees C
    P      AC electrical power, kW (measured by the wall plug)
    k      heat-gain rate toward outdoors, 1/h
    c      cooling effect per kW of AC input, K/h per kW

Both k and c are fitted by ordinary least squares over consecutive samples.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Sample:
    """One reading pair: the room and outdoor temperature, and mean AC power, over one interval."""

    minutes: float  # length of the interval until the next sample
    temp_c: float  # room temperature at the start of the interval
    temp_next_c: float  # room temperature at the end of the interval
    outdoor_c: float  # outdoor temperature during the interval
    power_kw: float  # mean AC power during the interval


@dataclass(frozen=True)
class Fit:
    k: float  # heat-gain rate toward outdoors, 1/h
    c: float  # cooling effect, K/h per kW
    g: float = 0.0  # constant gain, K/h (internal heat from people and appliances)
    samples: int = 0


MIN_SAMPLES = 12  # about one hour of 5-minute samples
MIN_POWER_SPREAD_KW = 0.05  # the fit needs some AC running, or c is unidentified
MIN_OUTDOOR_SPREAD_K = 1.0  # outdoor minus room must vary, or k and g are unidentified


class NotEnoughData(ValueError):
    """Raised when the samples cannot identify the coefficients."""


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Solve a small linear system by Gaussian elimination with partial pivoting."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            raise NotEnoughData("samples are collinear; cannot separate the coefficients")
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                for c2 in range(col, n + 1):
                    m[r][c2] -= f * m[col][c2]
    return [m[i][n] / m[i][i] for i in range(n)]


def _least_squares(rows: list[list[float]], y: list[float], active: list[int]) -> list[float]:
    """Ordinary least squares over the columns in `active`. Returns one value per active column."""
    k = len(active)
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for row, target in zip(rows, y):
        xa = [row[j] for j in active]
        for i in range(k):
            xty[i] += xa[i] * target
            for j in range(k):
                xtx[i][j] += xa[i] * xa[j]
    return _solve(xtx, xty)


def fit(samples: list[Sample]) -> Fit:
    """Fit k, c and g by least squares. Raises NotEnoughData when the data cannot identify them.

    Each sample gives one equation:
        y = k * x1 + c * x2 + g
    with
        y  = (T_next - T) / dt_h
        x1 = T_out - T
        x2 = -P
        g is a constant term, the same for every sample.
    A coefficient that comes out negative is set to zero and the others are refit.
    """
    usable = [s for s in samples if s.minutes > 0]
    if len(usable) < MIN_SAMPLES:
        raise NotEnoughData(f"need at least {MIN_SAMPLES} samples, got {len(usable)}")

    powers = [s.power_kw for s in usable]
    if max(powers) - min(powers) < MIN_POWER_SPREAD_KW:
        raise NotEnoughData("AC power never varied; cannot separate cooling from heat gain")

    rows: list[list[float]] = []
    y: list[float] = []
    for s in usable:
        dt_h = s.minutes / 60.0
        y.append((s.temp_next_c - s.temp_c) / dt_h)
        rows.append([s.outdoor_c - s.temp_c, -s.power_kw, 1.0])

    x1 = [r[0] for r in rows]
    if max(x1) - min(x1) < MIN_OUTDOOR_SPREAD_K:
        raise NotEnoughData("outdoor minus room temperature hardly varied; cannot fit heat gain")

    active = [0, 1, 2]
    while True:
        coef = _least_squares(rows, y, active)
        negative = [idx for idx, v in zip(active, coef) if v < 0 and idx in (0, 1)]
        if not negative:
            break
        active = [idx for idx in active if idx != negative[0]]
    values = dict(zip(active, coef))
    return Fit(
        k=max(values.get(0, 0.0), 0.0),
        c=max(values.get(1, 0.0), 0.0),
        g=values.get(2, 0.0),
        samples=len(usable),
    )
