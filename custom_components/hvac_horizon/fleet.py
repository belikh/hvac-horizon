"""Room devices: each heater or cooler is its own device, chosen automatically for the room's need.

A device has a rate: how many kelvin per hour it changes the room for each kW it draws. Its power is its
measured power when it has a power sensor, and its entered rated watts otherwise. What a device delivers to
the room is rate times power, in K per hour. For a temperature gap and a horizon, the room needs gap / horizon
kelvin per hour. `choose_devices` picks the devices that supply it.

Mode "fast" picks the devices that deliver the most first. Mode "economy" picks the devices with the lowest
cost per kelvin changed first, and the smaller device on a tie, so a small unit covers a small gap and a
large unit runs only when the small ones cannot.

`calibrate_rates` fits each device's rate from the room's response while that device alone runs.
"""

from __future__ import annotations

from dataclasses import dataclass

from .devices import RoomSample, fit_room
from .model import NotEnoughData

COOL = "cool"
HEAT = "heat"
FAST = "fast"
ECONOMY = "economy"
MIN_RUN_SAMPLES = 6  # a device needs this many 5-minute intervals running alone before its fitted rate is used


@dataclass(frozen=True)
class Device:
    device_id: str
    kind: str  # COOL or HEAT
    rate_k_per_h_per_kw: float | None = None  # calibrated effect on the room; a device without it is not chosen
    rated_w: float | None = None  # used while running when there is no power sensor
    power_sensor: str | None = None  # entity id of a power sensor in watts, when there is one
    entity_id: str | None = None  # the climate or switch entity that drives the device; without it the device is not driven

    @property
    def domain(self) -> str | None:
        """"climate" or "switch", from the entity id."""
        return self.entity_id.split(".", 1)[0] if self.entity_id and "." in self.entity_id else None


def device_kw(device: Device, sensor_w: float | None, running: bool) -> float:
    """Power of one device in kW. A sensor reading wins. Without one, a running device draws its rated watts."""
    if not running:
        return 0.0
    if sensor_w is not None and device.power_sensor is not None:
        return max(0.0, sensor_w) / 1000.0
    return max(0.0, device.rated_w or 0.0) / 1000.0


def _delivered_k_per_h(device: Device) -> float:
    """Kelvin per hour the device delivers to the room while it runs: its rate times its power in kW."""
    return (device.rate_k_per_h_per_kw or 0.0) * device_kw(device, None, True)


def choose_devices(
    devices: list[Device], kind: str, gap_k: float, horizon_h: float, mode: str, price_per_kwh: float
) -> list[str]:
    """Device ids of one kind that supply the room's need, chosen for the mode.

    The need is gap_k over horizon_h, in K per hour. Devices are added in mode order until what they deliver
    covers the need. If no set covers it, every calibrated device of the kind runs.
    """
    if gap_k <= 0 or horizon_h <= 0:
        return []
    usable = [
        d for d in devices
        if d.kind == kind and d.rate_k_per_h_per_kw and d.rate_k_per_h_per_kw > 0 and (d.rated_w or 0) > 0
    ]
    if mode == FAST:
        usable.sort(key=lambda d: (-_delivered_k_per_h(d), d.device_id))
    else:
        # Cost per kelvin changed: price times kW, divided by the K per hour delivered. Ties go to the smaller device.
        usable.sort(
            key=lambda d: (
                round(device_kw(d, None, True) * price_per_kwh / _delivered_k_per_h(d), 9),
                device_kw(d, None, True),
                d.device_id,
            )
        )
    need = gap_k / horizon_h
    chosen: list[str] = []
    supplied = 0.0
    for device in usable:
        chosen.append(device.device_id)
        supplied += _delivered_k_per_h(device)
        if supplied >= need:
            break
    return chosen


def calibrate_rates(samples: list[RoomSample], devices: list[Device]) -> dict[str, dict[str, float]]:
    """Fitted rate per device, from the intervals where at most one fleet device ran.

    Returns {device_id: {"rate": K per hour per kW, "samples": intervals the device ran alone}}. A device with fewer
    than MIN_RUN_SAMPLES such intervals, or a fitted rate of zero, has no entry, so its entered rate stays in use.
    Returns {} when the room fit has too few samples or too little outdoor variation.
    """
    ids = [d.device_id for d in devices]
    alone = [s for s in samples if sum(1 for i in ids if s.device_kw.get(i, 0.0) > 0.0) <= 1]
    try:
        fitted = fit_room(alone, {d.device_id: d.kind for d in devices})
    except NotEnoughData:
        return {}
    out: dict[str, dict[str, float]] = {}
    for device in devices:
        runs = sum(1 for s in alone if s.device_kw.get(device.device_id, 0.0) > 0.0)
        rate = fitted.coef.get(device.device_id, 0.0)
        if runs >= MIN_RUN_SAMPLES and rate > 0.0:
            out[device.device_id] = {"rate": rate, "samples": float(runs)}
    return out
