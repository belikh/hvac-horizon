"""Room model with several devices: one cooling or heating coefficient per device.

Per interval the room obeys

    dT/dt = k (T_out - T) + g - sum_i c_i P_i + sum_j h_j Q_j

where P_i are cooling devices (AC) and Q_j heating devices (plug heater), all in kW.
Every coefficient is clamped to zero or above, so a cooler always cools and a heater always heats.
A device that never ran has no variation, so its coefficient is 0 and it does not block the fit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import MIN_OUTDOOR_SPREAD_K, MIN_SAMPLES, Fit, NotEnoughData, _least_squares

COOL = "cool"
HEAT = "heat"
HEATER = "heater"  # device id of the room heater; its sample is 1 while the heater runs, so its coefficient is K/h while on


@dataclass(frozen=True)
class RoomSample:
    minutes: float
    temp_c: float
    temp_next_c: float
    outdoor_c: float
    device_kw: dict[str, float] = field(default_factory=dict)  # device id -> mean kW over the interval


@dataclass(frozen=True)
class RoomFit:
    k: float
    g: float
    coef: dict[str, float]  # device id -> coefficient, K/h per kW, >= 0
    samples: int


MIN_DEVICE_SPREAD_KW = 0.05  # a device needs this much power variation to be identified


def fit_room(samples: list[RoomSample], kinds: dict[str, str]) -> RoomFit:
    """Fit k, g and one coefficient per device. `kinds` maps each device id to COOL or HEAT."""
    usable = [s for s in samples if s.minutes > 0]
    if len(usable) < MIN_SAMPLES:
        raise NotEnoughData(f"need at least {MIN_SAMPLES} samples, got {len(usable)}")
    x1 = [s.outdoor_c - s.temp_c for s in usable]
    if max(x1) - min(x1) < MIN_OUTDOOR_SPREAD_K:
        raise NotEnoughData("outdoor minus room temperature hardly varied; cannot fit heat gain")

    # Columns: k, g, then one per device that has power variation. Devices without variation are left out.
    devices = []
    for dev, kind in kinds.items():
        values = [s.device_kw.get(dev, 0.0) for s in usable]
        if max(values) - min(values) >= MIN_DEVICE_SPREAD_KW:
            devices.append(dev)

    names = ["k", "g"] + devices
    rows, y = [], []
    for s in usable:
        dt_h = s.minutes / 60.0
        y.append((s.temp_next_c - s.temp_c) / dt_h)
        row = [s.outdoor_c - s.temp_c, 1.0]
        for dev in devices:
            sign = -1.0 if kinds[dev] == COOL else 1.0
            row.append(sign * s.device_kw.get(dev, 0.0))
        rows.append(row)

    active = list(range(len(names)))
    while True:
        coef = _least_squares(rows, y, active)
        negative = [i for i, v in zip(active, coef) if v < 0 and i >= 2]  # device columns only
        if not negative:
            break
        active = [i for i in active if i != negative[0]]
    values = dict(zip(active, coef))

    k = max(values.get(0, 0.0), 0.0)
    g = values.get(1, 0.0)
    device_coef: dict[str, float] = {}
    for idx, dev in enumerate(devices, start=2):
        # The column already carries the sign of the device's effect, so its coefficient is the magnitude.
        device_coef[dev] = max(values.get(idx, 0.0), 0.0)
    for dev in kinds:
        device_coef.setdefault(dev, 0.0)
    return RoomFit(k=k, g=g, coef=device_coef, samples=len(usable))


def forecast_fit(room: RoomFit, device_kw: dict[str, float]) -> tuple[Fit, float] | None:
    """The two-device model as one Fit, and the total running power in kW.

    The forecast runs the AC at constant power. The combined cooling coefficient weights each device's coefficient
    by its share of the running power. The heater coefficient is left out: a cooling forecast has the heater off.
    Returns None when no cooling device runs.
    """
    cooling = {dev: coef for dev, coef in room.coef.items() if dev != HEATER}
    total = sum(device_kw.get(dev, 0.0) for dev in cooling)
    if total <= 0:
        return None
    c = sum(coef * device_kw.get(dev, 0.0) for dev, coef in cooling.items()) / total
    return Fit(k=room.k, c=c, g=room.g, samples=room.samples), total
