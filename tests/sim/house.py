"""A simulated room, richer than the controller's own model, for closed-loop tests.

Pure Python, no Home Assistant import. The controller fits a one-node model (room temperature, outdoor
temperature, device power). This room has two thermal nodes, so the controller never sees its own model back:

    air node   Ca dTa/dt = Uao (To - Ta) + Uam (Tm - Ta) + Qint + Fair Qsol - Qac + Qheat
    mass node  Cm dTm/dt = Uam (Ta - Tm) + Umo (To - Tm) + (1 - Fair) Qsol

The outdoor temperature is a daily sine (minimum 03:00, maximum 15:00) plus slow noise. Solar gain follows a
half sine from 06:00 to 18:00. Each AC has a cooling capacity that falls as the outdoor temperature rises, a
COP that falls the same way, its own thermostat (starts above setpoint + 0.5 K, stops below setpoint - 1 K),
a minimum compressor run time and a restart lockout. An optional resistive heater adds heat to the air node.
Sensors add noise and can drop out (unavailable) or stick (stop reporting).

The step is one minute. All randomness comes from one seeded generator, so a run repeats exactly.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

STEP_S = 60.0


@dataclass(frozen=True)
class Envelope:
    """Thermal parameters of the room."""

    air_j_per_k: float = 3.0e5
    mass_j_per_k: float = 1.5e6
    air_to_outdoor_w_per_k: float = 40.0
    mass_to_outdoor_w_per_k: float = 50.0
    air_to_mass_w_per_k: float = 250.0
    solar_peak_w: float = 500.0
    solar_to_air: float = 0.4  # share of solar gain that lands in the air node; the rest warms the mass
    internal_w: float = 200.0


@dataclass(frozen=True)
class Weather:
    mean_c: float = 30.0
    amplitude_c: float = 6.0
    noise_c: float = 0.0  # standard deviation of the slow outdoor noise; 0 is off

    @property
    def peak_c(self) -> float:
        return self.mean_c + self.amplitude_c


@dataclass(frozen=True)
class AcSpec:
    cool_kw: float  # thermal cooling capacity at 27 C outdoor
    cop: float = 3.2  # at 27 C outdoor
    derate_per_k: float = 0.025  # capacity fraction lost per K of outdoor temperature above 27 C
    min_run_min: int = 3  # the thermostat cannot stop a compressor that started less than this long ago
    lockout_min: int = 3  # the compressor cannot restart sooner than this after it stopped
    stat_start_k: float = 0.5  # thermostat starts the compressor this far above the setpoint
    stat_stop_k: float = 1.0  # thermostat stops the compressor this far below the setpoint


@dataclass(frozen=True)
class SensorFault:
    """From start_min to end_min (minutes since the run start) the sensors named in `targets` fail.

    kind "dropout": the sensor reports unavailable. kind "stuck": the sensor stops reporting and keeps its last value.
    """

    start_min: float
    end_min: float
    kind: str  # "dropout" or "stuck"
    targets: tuple[str, ...] = ("room",)  # "room" and or "outdoor"


class Ac:
    def __init__(self, spec: AcSpec) -> None:
        self.spec = spec
        self.mode = "off"  # what Home Assistant shows: off or cool
        self.setpoint_c = 26.0
        self.compressor = False
        self.since_change_min = 99  # minutes since the compressor last started or stopped

    def capacity_w(self, outdoor_c: float) -> float:
        factor = 1.0 - self.spec.derate_per_k * (outdoor_c - 27.0)
        return self.spec.cool_kw * 1000.0 * min(1.1, max(0.4, factor))

    def power_w(self, outdoor_c: float) -> float:
        if not self.compressor:
            return 0.0
        cop = max(1.5, self.spec.cop * (1.0 - 0.015 * (outdoor_c - 27.0)))
        return self.capacity_w(outdoor_c) / cop + 30.0  # 30 W for the fans

    def step(self, air_c: float) -> None:
        """One minute of compressor logic."""
        self.since_change_min += 1
        if self.mode != "cool":
            if self.compressor:
                self._set(False)  # an off command stops the compressor at once
            return
        if self.compressor:
            if air_c < self.setpoint_c - self.spec.stat_stop_k and self.since_change_min >= self.spec.min_run_min:
                self._set(False)
        elif air_c > self.setpoint_c + self.spec.stat_start_k and self.since_change_min >= self.spec.lockout_min:
            self._set(True)

    def _set(self, on: bool) -> None:
        self.compressor = on
        self.since_change_min = 0


class Heater:
    def __init__(self, kw: float) -> None:
        self.kw = kw
        self.on = False

    def power_w(self) -> float:
        return self.kw * 1000.0 if self.on else 0.0


@dataclass
class Sensor:
    """A noisy sensor. read() returns ("value", x), ("unavailable", None) or ("silent", None)."""

    name: str
    noise_c: float
    bias_c: float = 0.0
    kind: str = "room"
    last: float | None = None

    def read(self, truth_c: float, rng: random.Random, fault: SensorFault | None) -> tuple[str, float | None]:
        if fault is not None and self.kind in fault.targets:
            return ("unavailable", None) if fault.kind == "dropout" else ("silent", None)
        self.last = round(truth_c + self.bias_c + rng.gauss(0.0, self.noise_c), 1)
        return ("value", self.last)


@dataclass
class House:
    envelope: Envelope = field(default_factory=Envelope)
    weather: Weather = field(default_factory=Weather)
    acs: dict[str, Ac] = field(default_factory=dict)
    heater: Heater | None = None
    seed: int = 1
    start_hour: float = 0.0
    start_air_c: float = 26.0
    start_mass_c: float = 27.0
    sensor_noise_c: float = 0.1
    faults: list[SensorFault] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)
        self.minute = 0  # minutes since the run start
        self.air_c = self.start_air_c
        self.mass_c = self.start_mass_c
        self._noise = 0.0
        self.outdoor_c = self._outdoor()
        self.room_sensors = [Sensor("room_a", self.sensor_noise_c, 0.05), Sensor("room_b", self.sensor_noise_c, -0.05)]
        self.outdoor_sensor = Sensor("outdoor", 0.2, kind="outdoor")

    @property
    def hour(self) -> float:
        return (self.start_hour + self.minute / 60.0) % 24.0

    def _outdoor(self) -> float:
        w = self.weather
        base = w.mean_c + w.amplitude_c * math.sin(2.0 * math.pi * (self.hour - 9.0) / 24.0)
        return base + self._noise

    def solar_w(self) -> float:
        h = self.hour
        if not 6.0 < h < 18.0:
            return 0.0
        return self.envelope.solar_peak_w * math.sin(math.pi * (h - 6.0) / 12.0)

    def active_fault(self) -> SensorFault | None:
        for fault in self.faults:
            if fault.start_min <= self.minute < fault.end_min:
                return fault
        return None

    def power_w(self) -> float:
        total = sum(ac.power_w(self.outdoor_c) for ac in self.acs.values())
        return total + (self.heater.power_w() if self.heater else 0.0)

    def step(self) -> None:
        """Advance one minute. Devices act on the state they have now."""
        e = self.envelope
        if self.weather.noise_c:
            self._noise = 0.97 * self._noise + self.rng.gauss(0.0, self.weather.noise_c * 0.25)
        self.outdoor_c = self._outdoor()
        for ac in self.acs.values():
            ac.step(self.air_c)
        q_ac = sum(ac.capacity_w(self.outdoor_c) for ac in self.acs.values() if ac.compressor)
        q_heat = self.heater.power_w() if self.heater else 0.0
        solar = self.solar_w()
        d_air = (
            e.air_to_outdoor_w_per_k * (self.outdoor_c - self.air_c)
            + e.air_to_mass_w_per_k * (self.mass_c - self.air_c)
            + e.internal_w + e.solar_to_air * solar - q_ac + q_heat
        ) / e.air_j_per_k
        d_mass = (
            e.air_to_mass_w_per_k * (self.air_c - self.mass_c)
            + e.mass_to_outdoor_w_per_k * (self.outdoor_c - self.mass_c)
            + (1.0 - e.solar_to_air) * solar
        ) / e.mass_j_per_k
        self.air_c += d_air * STEP_S
        self.mass_c += d_mass * STEP_S
        self.minute += 1
