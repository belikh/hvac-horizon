"""PID controller for a heater output, built in so the integration needs no other integration.

Behaviour follows the common PID design used by smart-thermostat integrations:
- Trapezoidal integration of the error.
- Derivative on the measurement, filtered with a time-based factor, so a setpoint change does not kick.
- Optional outdoor feedforward: a term `ke * (setpoint - outdoor)` added to the output.
- Anti-windup that stops the integral growing into saturation, measured including the feedforward term.
- `reset()` for a switch-off, and `set_gains()` for changes while running.

The output is a percentage, clamped to [out_min, out_max].
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Pid:
    kp: float
    ki: float  # per hour
    kd: float  # hours
    ke: float = 0.0  # outdoor feedforward, percent per kelvin of (setpoint - outdoor)
    derivative_filter_h: float = 0.1  # filter time constant, hours; 0 disables the filter
    out_min: float = 0.0
    out_max: float = 100.0
    _integral: float = 0.0
    _last_error: float | None = None
    _last_measure: float | None = None
    _derivative: float = 0.0

    def reset(self, clear_integral: bool = False) -> None:
        """Forget the last samples, e.g. after the heater was switched off. The integral is kept unless asked."""
        self._last_error = None
        self._last_measure = None
        self._derivative = 0.0
        if clear_integral:
            self._integral = 0.0

    def set_gains(self, kp: float | None = None, ki: float | None = None, kd: float | None = None, ke: float | None = None) -> None:
        if kp is not None:
            self.kp = kp
        if ki is not None:
            self.ki = ki
        if kd is not None:
            self.kd = kd
        if ke is not None:
            self.ke = ke

    def update(self, setpoint: float, measure: float, dt_h: float, outdoor: float | None = None) -> float:
        """Return the output percentage for one step of `dt_h` hours."""
        if dt_h <= 0:
            raise ValueError("dt_h must be positive")
        if self.out_min >= self.out_max:
            raise ValueError("out_min must be less than out_max")

        error = setpoint - measure
        feedforward = self.ke * (setpoint - outdoor) if outdoor is not None else 0.0

        # Anti-windup: decide before adding this step whether the output is already saturated.
        last_error = self._last_error if self._last_error is not None else error
        trapezoid = 0.5 * (error + last_error) * dt_h
        provisional = self.kp * error + self._integral + self.ki * trapezoid + feedforward + self._derivative
        saturated_high = provisional >= self.out_max and error > 0
        saturated_low = provisional <= self.out_min and error < 0
        if not (saturated_high or saturated_low):
            self._integral += self.ki * trapezoid
        self._integral = min(max(self._integral, self.out_min - feedforward), self.out_max - feedforward)

        # Derivative on the measurement with a time-based filter weight alpha = dt / (tau + dt).
        if self._last_measure is not None:
            raw = -self.kd * (measure - self._last_measure) / dt_h
            if self.derivative_filter_h > 0:
                alpha = dt_h / (self.derivative_filter_h + dt_h)
                self._derivative = (1.0 - alpha) * self._derivative + alpha * raw
            else:
                self._derivative = raw
        else:
            self._derivative = 0.0

        self._last_error = error
        self._last_measure = measure
        output = self.kp * error + self._integral + self._derivative + feedforward
        return min(max(output, self.out_min), self.out_max)
