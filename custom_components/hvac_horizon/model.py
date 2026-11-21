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

import math
import statistics
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
    # Fit quality. None when the fit did not compute it (for example a fit built from stored coefficients).
    residual_k_per_h: float | None = None  # residual standard deviation, K/h
    k_stderr: float | None = None  # standard error of k, 1/h; None when k was clamped to zero
    c_stderr: float | None = None  # standard error of c, K/h per kW; None when c was clamped to zero
    g_stderr: float | None = None  # standard error of g, K/h
    condition: float | None = None  # eigenvalue ratio of the column-scaled normal matrix
    outliers_dropped: int = 0  # samples removed by the residual filter before the final fit


MIN_SAMPLES = 12  # about one hour of 5-minute samples
MIN_POWER_SPREAD_KW = 0.05  # the fit needs some AC running, or c is unidentified
MIN_OUTDOOR_SPREAD_K = 1.0  # outdoor minus room must vary, or k and g are unidentified
MAD_SCALE = 1.4826  # converts a median absolute deviation to a standard deviation for Gaussian noise
OUTLIER_MAD_LIMIT = 3.5  # a residual beyond this many scaled MADs from the median is an outlier
OUTLIER_MAX_FRACTION = 0.10  # drop at most this share of the samples
MIN_RESIDUAL_SCALE = 1e-9  # K/h; floor for the scaled MAD so float noise on exact data drops nothing
MIN_COEFFICIENT = 1e-9  # a fitted c below this is treated as zero
MAX_CONDITION = 1000.0  # reject fits whose scaled normal matrix is worse conditioned than this


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


def _invert(a: list[list[float]]) -> list[list[float]]:
    """Inverse of a small matrix, one column per unit vector."""
    n = len(a)
    cols = [_solve(a, [1.0 if i == j else 0.0 for i in range(n)]) for j in range(n)]
    return [[cols[j][i] for j in range(n)] for i in range(n)]


def _eigenvalues(a: list[list[float]]) -> list[float]:
    """Eigenvalues of a small symmetric matrix by cyclic Jacobi rotations."""
    n = len(a)
    m = [row[:] for row in a]
    for _ in range(60):
        off = sum(m[i][j] ** 2 for i in range(n) for j in range(n) if i != j)
        if off < 1e-24:
            break
        for p in range(n - 1):
            for q in range(p + 1, n):
                if abs(m[p][q]) < 1e-300:
                    continue
                theta = (m[q][q] - m[p][p]) / (2.0 * m[p][q])
                t = math.copysign(1.0, theta) / (abs(theta) + math.sqrt(theta * theta + 1.0))
                c = 1.0 / math.sqrt(t * t + 1.0)
                s = t * c
                for r in range(n):
                    mrp, mrq = m[r][p], m[r][q]
                    m[r][p], m[r][q] = c * mrp - s * mrq, s * mrp + c * mrq
                for r in range(n):
                    mpr, mqr = m[p][r], m[q][r]
                    m[p][r], m[q][r] = c * mpr - s * mqr, s * mpr + c * mqr
    return [m[i][i] for i in range(n)]


def _normal_matrix(rows: list[list[float]], active: list[int]) -> list[list[float]]:
    k = len(active)
    xtx = [[0.0] * k for _ in range(k)]
    for row in rows:
        xa = [row[j] for j in active]
        for i in range(k):
            for j in range(k):
                xtx[i][j] += xa[i] * xa[j]
    return xtx


def _condition(xtx: list[list[float]]) -> float:
    """Eigenvalue ratio after scaling every column to unit length, so the units of the columns do not matter."""
    n = len(xtx)
    d = [math.sqrt(xtx[i][i]) for i in range(n)]
    if min(d) <= 0:
        return math.inf
    eig = _eigenvalues([[xtx[i][j] / (d[i] * d[j]) for j in range(n)] for i in range(n)])
    if min(eig) <= 0:
        return math.inf
    return max(eig) / min(eig)


def _solve_clamped(rows: list[list[float]], y: list[float]) -> tuple[list[int], list[float]]:
    """Least squares over k, c and g with k and c at or above zero.

    The plain fit is used when its k and c are not negative. Otherwise k, c or both are set to zero, and the
    choice with the smallest squared error among those with no negative k or c is used.
    """
    active = [0, 1, 2]
    coef = _least_squares(rows, y, active)
    if all(v >= 0 for idx, v in zip(active, coef) if idx in (0, 1)):
        return active, coef
    best: tuple[float, list[int], list[float]] | None = None
    for subset in ([1, 2], [0, 2], [2]):
        try:
            found = _least_squares(rows, y, subset)
        except NotEnoughData:
            continue
        if any(v < 0 for idx, v in zip(subset, found) if idx in (0, 1)):
            continue
        error = sum(r * r for r in _residuals(rows, y, subset, found))
        if best is None or error < best[0]:
            best = (error, subset, found)
    if best is None:
        raise NotEnoughData("samples are collinear; cannot separate the coefficients")
    return best[1], best[2]


def _residuals(rows: list[list[float]], y: list[float], active: list[int], coef: list[float]) -> list[float]:
    return [t - sum(row[j] * v for j, v in zip(active, coef)) for row, t in zip(rows, y)]


def _outlier_indices(res: list[float]) -> set[int]:
    """Indices whose residual is beyond OUTLIER_MAD_LIMIT scaled MADs, the worst first, capped at 10 percent."""
    med = statistics.median(res)
    scale = max(MAD_SCALE * statistics.median(abs(r - med) for r in res), MIN_RESIDUAL_SCALE)
    bad = [i for i, r in enumerate(res) if abs(r - med) > OUTLIER_MAD_LIMIT * scale]
    bad.sort(key=lambda i: -abs(res[i] - med))
    return set(bad[: int(OUTLIER_MAX_FRACTION * len(res))])


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
    Residuals beyond OUTLIER_MAD_LIMIT scaled MADs are dropped (at most 10 percent) and the fit is repeated once.
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

    active, coef = _solve_clamped(rows, y)
    dropped = 0
    bad = _outlier_indices(_residuals(rows, y, active, coef))
    if bad and len(rows) - len(bad) >= MIN_SAMPLES:
        keep = [i for i in range(len(rows)) if i not in bad]
        rows = [rows[i] for i in keep]
        y = [y[i] for i in keep]
        dropped = len(bad)
        active, coef = _solve_clamped(rows, y)

    xtx = _normal_matrix(rows, active)
    cond = _condition(xtx)
    if cond > MAX_CONDITION:
        raise NotEnoughData(
            f"samples are nearly collinear (condition {cond:.0f} above {MAX_CONDITION:.0f}); cannot separate the coefficients"
        )
    res = _residuals(rows, y, active, coef)
    dof = len(rows) - len(active)
    variance = sum(r * r for r in res) / dof
    inv = _invert(xtx)
    stderr = {j: math.sqrt(max(variance * inv[i][i], 0.0)) for i, j in enumerate(active)}
    values = dict(zip(active, coef))
    c = max(values.get(1, 0.0), 0.0)
    if c < MIN_COEFFICIENT:
        c = 0.0  # float noise around zero
    if c > 0 and stderr[1] > c:
        raise NotEnoughData(
            f"cooling effect is not identified (c {c:.3f} with standard error {stderr[1]:.3f} K/h per kW)"
        )
    return Fit(
        k=max(values.get(0, 0.0), 0.0),
        c=c,
        g=values.get(2, 0.0),
        samples=len(rows) + dropped,
        residual_k_per_h=math.sqrt(variance),
        k_stderr=stderr.get(0),
        c_stderr=stderr.get(1),
        g_stderr=stderr.get(2),
        condition=cond,
        outliers_dropped=dropped,
    )


CONFIDENCE_HIGH_REL_SE = 0.10  # standard error of c below 10 percent of c
CONFIDENCE_MEDIUM_REL_SE = 0.30  # below 30 percent; anything above is low


def fit_confidence(f: Fit) -> str | None:
    """Label from the relative standard error of c. None when the fit has no standard error."""
    if f.c_stderr is None or f.c <= 0:
        return None
    rel = f.c_stderr / f.c
    if rel < CONFIDENCE_HIGH_REL_SE:
        return "high"
    if rel < CONFIDENCE_MEDIUM_REL_SE:
        return "medium"
    return "low"
