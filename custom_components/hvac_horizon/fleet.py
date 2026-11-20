"""Room devices: each heater or cooler is its own device, chosen automatically for the room's need.

A device has a rate: how many kelvin per hour it changes the room for each kW it draws. Its power is its
measured power when it has a power sensor, and its entered rated watts otherwise. For a temperature gap
and a horizon, the room needs gap / horizon kelvin per hour. `choose_devices` picks the devices that supply it.

Mode "fast" picks the highest-rate devices first. Mode "economy" picks the devices that cost least per
kelvin changed, so a small unit covers a small gap and a large unit runs only when the small ones cannot.
"""

from __future__ import annotations

from dataclasses import dataclass

COOL = "cool"
HEAT = "heat"
FAST = "fast"
ECONOMY = "economy"


@dataclass(frozen=True)
class Device:
    device_id: str
    kind: str  # COOL or HEAT
    rate_k_per_h_per_kw: float | None = None  # calibrated effect on the room; a device without it is not chosen
    rated_w: float | None = None  # used while running when there is no power sensor
    power_sensor: str | None = None  # entity id of a power sensor in watts, when there is one


def device_kw(device: Device, sensor_w: float | None, running: bool) -> float:
    """Power of one device in kW. A sensor reading wins. Without one, a running device draws its rated watts."""
    if not running:
        return 0.0
    if sensor_w is not None and device.power_sensor is not None:
        return max(0.0, sensor_w) / 1000.0
    return max(0.0, device.rated_w or 0.0) / 1000.0


def choose_devices(
    devices: list[Device], kind: str, gap_k: float, horizon_h: float, mode: str, price_per_kwh: float
) -> list[str]:
    """Device ids of one kind that supply the room's need, chosen for the mode.

    The need is gap_k over horizon_h, in K per hour. Devices are added in mode order until their rates
    cover the need. If no set covers it, every calibrated device of the kind runs.
    """
    if gap_k <= 0 or horizon_h <= 0:
        return []
    usable = [
        d for d in devices
        if d.kind == kind and d.rate_k_per_h_per_kw and d.rate_k_per_h_per_kw > 0 and (d.rated_w or 0) > 0
    ]
    if mode == FAST:
        usable.sort(key=lambda d: (-d.rate_k_per_h_per_kw, d.device_id))
    else:
        # Cost per kelvin changed per hour: the device's power times the price, divided by its rate.
        usable.sort(key=lambda d: (device_kw(d, None, True) * price_per_kwh / d.rate_k_per_h_per_kw, d.device_id))
    need = gap_k / horizon_h
    chosen: list[str] = []
    supplied = 0.0
    for device in usable:
        chosen.append(device.device_id)
        supplied += device.rate_k_per_h_per_kw * device_kw(device, None, True)
        if supplied >= need:
            break
    return chosen
