"""PID auto-tuning from a relay test, using the Ziegler-Nichols rules. Pure logic.

The relay test switches the heater between two outputs around the setpoint until the room oscillates.
The ultimate gain Ku is 4d / (pi * a), where d is the relay amplitude and a the measured swing amplitude
of the input. The ultimate period Tu is the mean time between peaks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Gains:
    kp: float
    ki: float  # per hour
    kd: float  # hours


def ultimate_gain(relay_amplitude_pct: float, input_amplitude_k: float) -> float:
    """Ku from the relay output amplitude (percent) and the input swing amplitude (kelvin)."""
    if relay_amplitude_pct <= 0 or input_amplitude_k <= 0:
        raise ValueError("amplitudes must be positive")
    return 4.0 * relay_amplitude_pct / (math.pi * input_amplitude_k)


def ziegler_nichols(ku: float, tu_h: float) -> Gains:
    """Classic PID rules: Kp = 0.6 Ku, Ti = Tu / 2, Td = Tu / 8. Returns Ki and Kd in the same units as the PID class."""
    if ku <= 0 or tu_h <= 0:
        raise ValueError("Ku and Tu must be positive")
    kp = 0.6 * ku
    ti = tu_h / 2.0
    td = tu_h / 8.0
    return Gains(kp=kp, ki=kp / ti, kd=kp * td)


def period_from_peaks(peak_times_h: list[float]) -> float | None:
    """Mean time between consecutive peaks. None with fewer than two peaks."""
    if len(peak_times_h) < 2:
        return None
    gaps = [b - a for a, b in zip(peak_times_h, peak_times_h[1:])]
    return sum(gaps) / len(gaps)
