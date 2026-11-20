"""PID controller for a heater output, built in so the integration needs nothing else installed.

Output is a percentage from 0 to 100. The integral term stops growing while the output is saturated
(anti-windup), and the derivative acts on the measurement through a first-order filter, so a
setpoint step does not kick the output.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Pid:
    kp: float
    ki: float  # per hour: integral gain on error (K * h)
    kd: float  # hours: derivative time
    derivative_filter: float = 0.3  # 0 to 1, higher is smoother
    out_min: float = 0.0
    out_max: float = 100.0
    _integral: float = 0.0
    _last_measure: float | None = None
    _d_state: float = 0.0

    def reset(self) -> None:
        self._integral = 0.0
        self._last_measure = None
        self._d_state = 0.0

    def update(self, setpoint: float, measure: float, dt_h: float) -> float:
        """Return the output percentage for one step of `dt_h` hours."""
        if dt_h <= 0:
            raise ValueError("dt_h must be positive")
        error = setpoint - measure
        p = self.kp * error

        raw_d = 0.0
        if self._last_measure is not None:
            raw_d = -(measure - self._last_measure) / dt_h  # derivative on measurement, sign flipped
        self._d_state += (1.0 - self.derivative_filter) * (raw_d - self._d_state)
        self._last_measure = measure
        d = self.kd * self._d_state

        candidate_integral = self._integral + self.ki * error * dt_h
        unsat = p + candidate_integral + d
        output = min(max(unsat, self.out_min), self.out_max)
        # Anti-windup: only accept the integral step when it does not push further into saturation.
        pushing_into_saturation = (unsat > self.out_max and error > 0) or (unsat < self.out_min and error < 0)
        if not pushing_into_saturation:
            self._integral = candidate_integral
        return output
