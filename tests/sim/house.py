"""A simulated room, richer than the controller's own model, for closed-loop tests.

Pure Python, no Home Assistant import. The controller fits a one-node model (room temperature, outdoor
temperature, device power). This room has two thermal nodes, so the controller never sees its own model back:

    air node   Ca dTa/dt = Uao (To - Ta) + Uam (Tm - Ta) + Qint + Fair Qsol - Qac + Qheat
    mass node  Cm dTm/dt = Uam (Ta - Tm) + Umo (To - Tm) + (1 - Fair) Qsol

The outdoor temperature is a daily sine (minimum 03:00, maximum 15:00) plus slow noise. Solar gain follows a
half sine from 06:00 to 18:00. An optional resistive heater adds heat to the air node. Sensors add noise and can
drop out (unavailable) or stick (stop reporting).

Each AC is a split unit with the modes off, cool, heat, dry and fan_only:

- cool: a capacity and a COP that fall as the outdoor temperature rises. Its own thermostat starts the compressor
  above setpoint + 0.5 K and stops it below setpoint - 1 K.
- heat: a capacity that falls below 7 C outdoors. The thermostat starts below setpoint - 0.5 K and stops above setpoint + 1 K.
- dry: the compressor runs at a share of the cooling capacity and removes more water than cool does.
- fan_only, and any mode with the compressor idle: the indoor fan draws its own power and moves no heat.
- The thermostat reads the unit's own sensor, which can sit a fixed bias away from the room air (return air near the ceiling).
- A minimum compressor run time, a restart lockout, and a stop when the mode changes.

Room humidity is one node in percent: it moves toward the outdoor humidity, rises with people in the room and falls
while a compressor cools or dries. It is a test double for the humidity control, with no psychrometrics.

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
    humidity_pct: float = 60.0  # outdoor relative humidity

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
    heat_kw: float | None = None  # thermal heating capacity at 7 C outdoor; None is 1.1 times cool_kw
    heat_cop: float = 3.0
    sensor_bias_c: float = 0.0  # the unit's own sensor reads the room air plus this
    fan_w: float = 30.0  # indoor fan, drawn in every mode but off
    dry_share: float = 0.35  # share of the cooling capacity the compressor delivers in dry mode
    cool_dries_pct_per_h: float = 3.0  # fall of room humidity per hour while the compressor cools
    dry_dries_pct_per_h: float = 8.0  # the same in dry mode


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
        self.mode = "off"  # what Home Assistant shows: off, cool, heat, dry or fan_only
        self.setpoint_c = 26.0
        self.compressor = False
        self.since_change_min = 99  # minutes since the compressor last started or stopped
        self.sensed_c: float | None = None  # the unit's own sensor
        self._compressor_mode = "off"  # the mode the compressor started in

    def capacity_w(self, outdoor_c: float) -> float:
        factor = 1.0 - self.spec.derate_per_k * (outdoor_c - 27.0)
        return self.spec.cool_kw * 1000.0 * min(1.1, max(0.4, factor))

    def heat_capacity_w(self, outdoor_c: float) -> float:
        rated = (self.spec.heat_kw if self.spec.heat_kw is not None else 1.1 * self.spec.cool_kw) * 1000.0
        return rated * min(1.1, max(0.5, 1.0 - 0.02 * (7.0 - outdoor_c)))

    def thermal_w(self, outdoor_c: float) -> float:
        """Heat the unit adds to the room air. Negative while it cools or dries."""
        if not self.compressor:
            return 0.0
        if self.mode == "heat":
            return self.heat_capacity_w(outdoor_c)
        share = self.spec.dry_share if self.mode == "dry" else 1.0
        return -share * self.capacity_w(outdoor_c)

    def drying_pct_per_h(self) -> float:
        if not self.compressor or self.mode == "heat":
            return 0.0
        return self.spec.dry_dries_pct_per_h if self.mode == "dry" else self.spec.cool_dries_pct_per_h

    def power_w(self, outdoor_c: float) -> float:
        if self.mode == "off":
            return 0.0
        if not self.compressor:
            return self.spec.fan_w
        if self.mode == "heat":
            return self.heat_capacity_w(outdoor_c) / self.spec.heat_cop + self.spec.fan_w
        cop = max(1.5, self.spec.cop * (1.0 - 0.015 * (outdoor_c - 27.0)))
        share = self.spec.dry_share if self.mode == "dry" else 1.0
        return share * self.capacity_w(outdoor_c) / cop + self.spec.fan_w

    def step(self, air_c: float) -> None:
        """One minute of compressor logic."""
        self.since_change_min += 1
        self.sensed_c = round(air_c + self.spec.sensor_bias_c, 1)
        if self.mode in ("off", "fan_only") or (self.compressor and self.mode != self._compressor_mode):
            if self.compressor:
                self._set(False)  # off, fan and a change of mode stop the compressor at once
            return
        s, sensed = self.spec, air_c + self.spec.sensor_bias_c
        if self.mode == "dry":
            wants, done = True, False
        elif self.mode == "heat":
            wants, done = sensed < self.setpoint_c - s.stat_start_k, sensed > self.setpoint_c + s.stat_stop_k
        else:
            wants, done = sensed > self.setpoint_c + s.stat_start_k, sensed < self.setpoint_c - s.stat_stop_k
        if self.compressor:
            if done and self.since_change_min >= s.min_run_min:
                self._set(False)
        elif wants and self.since_change_min >= s.lockout_min:
            self._set(True)
            self._compressor_mode = self.mode

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
    start_rh_pct: float = 55.0
    air_changes_per_h: float = 0.3  # share of the indoor to outdoor humidity gap that closes per hour
    people_pct_per_h: float = 1.0  # rise of room humidity per hour from the people in the room
    sensor_noise_c: float = 0.1
    faults: list[SensorFault] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)
        self.minute = 0  # minutes since the run start
        self.air_c = self.start_air_c
        self.mass_c = self.start_mass_c
        self.rh_pct = self.start_rh_pct
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
        q_ac = -sum(ac.thermal_w(self.outdoor_c) for ac in self.acs.values())
        d_rh = self.air_changes_per_h * (self.weather.humidity_pct - self.rh_pct) + self.people_pct_per_h
        d_rh -= sum(ac.drying_pct_per_h() for ac in self.acs.values())
        self.rh_pct = min(95.0, max(20.0, self.rh_pct + d_rh / 60.0))
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
