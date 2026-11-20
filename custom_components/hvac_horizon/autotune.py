"""PID auto-tuning from a relay test, using the Ziegler-Nichols rules. Pure logic.

The relay test switches the heater between two outputs around the setpoint until the room oscillates.
The ultimate gain Ku is 4d / (pi * a), where d is the relay amplitude and a the measured swing amplitude
of the input. The ultimate period Tu is the mean time between peaks.
"""

from __future__ import annotations

import math
from dataclasses import field
from dataclasses import dataclass


@dataclass(frozen=True)
class Gains:
    kp: float
    ki: float  # per hour
    kd: float  # hours


def _ultimate_gain(relay_amplitude_pct: float, input_amplitude_k: float) -> float:
    """Ku from the relay output amplitude (percent) and the input swing amplitude (kelvin)."""
    if relay_amplitude_pct <= 0 or input_amplitude_k <= 0:
        raise ValueError("amplitudes must be positive")
    return 4.0 * relay_amplitude_pct / (math.pi * input_amplitude_k)


def _ziegler_nichols(ku: float, tu_h: float) -> Gains:
    """Classic PID rules: Kp = 0.6 Ku, Ti = Tu / 2, Td = Tu / 8. Returns Ki and Kd in the same units as the PID class."""
    if ku <= 0 or tu_h <= 0:
        raise ValueError("Ku and Tu must be positive")
    kp = 0.6 * ku
    ti = tu_h / 2.0
    td = tu_h / 8.0
    return Gains(kp=kp, ki=kp / ti, kd=kp * td)


def _period_from_peaks(peak_times_h: list[float]) -> float | None:
    """Mean time between consecutive peaks. None with fewer than two peaks."""
    if len(peak_times_h) < 2:
        return None
    gaps = [b - a for a, b in zip(peak_times_h, peak_times_h[1:])]
    return sum(gaps) / len(gaps)


@dataclass
class RelayTuner:
    """Relay test state machine. Switches the heater on below setpoint - hysteresis and off above setpoint + hysteresis.

    Records the times of input peaks. When `min_peaks` peaks are recorded and the swing is measured, `result` gives Gains.
    """

    setpoint_c: float
    hysteresis_c: float = 0.3
    relay_pct: float = 100.0
    min_peaks: int = 4
    heater_on: bool = False
    _peaks_h: list[float] = field(default_factory=list)
    _max_since_peak: float | None = None
    _rising: bool = False
    _swing: list[float] = field(default_factory=list)
    _last_temp: float | None = None

    def step(self, temp_c: float, now_h: float) -> bool:
        """One control step. Returns the heater state to command."""
        if self.heater_on and temp_c >= self.setpoint_c + self.hysteresis_c:
            self.heater_on = False
        elif not self.heater_on and temp_c <= self.setpoint_c - self.hysteresis_c:
            self.heater_on = True
        if self._last_temp is not None:
            rising = temp_c > self._last_temp
            if self._rising and not rising and self._max_since_peak is not None:
                self._peaks_h.append(now_h)
                self._swing.append(self._max_since_peak - self.setpoint_c)
                self._max_since_peak = None
            self._rising = rising
        self._max_since_peak = temp_c if self._max_since_peak is None else max(self._max_since_peak, temp_c)
        self._last_temp = temp_c
        return self.heater_on

    @property
    def done(self) -> bool:
        return len(self._peaks_h) >= self.min_peaks

    def result(self) -> Gains | None:
        """Gains from the recorded peaks, or None until enough peaks are recorded."""
        if not self.done:
            return None
        period = _period_from_peaks(self._peaks_h)
        amplitude = sum(abs(s) for s in self._swing[-3:]) / min(3, len(self._swing))
        if period is None or amplitude <= 0:
            return None
        return _ziegler_nichols(_ultimate_gain(self.relay_pct, amplitude), period)
