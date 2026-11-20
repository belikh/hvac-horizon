"""Pure decision functions ported from the feature sets of the other projects. No Home Assistant imports.

Each function is one decision with its own tests, so it can be wired into a control cycle later.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DayType(str, Enum):
    HOT = "hot"
    WARM = "warm"
    MILD = "mild"
    COOL = "cool"
    COLD = "cold"


def classify_day(forecast_high_c: float) -> DayType:
    """Classify a day from its forecast high, in Celsius. Thresholds: 30, 25, 18, 12 degrees."""
    if forecast_high_c >= 30.0:
        return DayType.HOT
    if forecast_high_c >= 25.0:
        return DayType.WARM
    if forecast_high_c >= 18.0:
        return DayType.MILD
    if forecast_high_c >= 12.0:
        return DayType.COOL
    return DayType.COLD


def outdoor_allows(mode: str, outdoor_c: float, cool_min_c: float = 16.0, heat_max_c: float = 22.0) -> bool:
    """Cooling is blocked below `cool_min_c` outdoors. Heating is blocked above `heat_max_c` outdoors."""
    if mode == "cool":
        return outdoor_c >= cool_min_c
    if mode == "heat":
        return outdoor_c <= heat_max_c
    raise ValueError(f"unknown mode {mode!r}")


@dataclass(frozen=True)
class Ventilation:
    run: bool
    reason: str


def air_quality_ventilation(co2_ppm: float | None, pm25_ugm3: float | None, co2_on: float = 1000.0, pm25_on: float = 25.0) -> Ventilation:
    """Run the room fan when CO2 or PM2.5 is above its threshold. Missing readings never switch it on."""
    if co2_ppm is not None and co2_ppm >= co2_on:
        return Ventilation(True, f"CO2 {co2_ppm:.0f} ppm at or above {co2_on:.0f}")
    if pm25_ugm3 is not None and pm25_ugm3 >= pm25_on:
        return Ventilation(True, f"PM2.5 {pm25_ugm3:.0f} ug/m3 at or above {pm25_on:.0f}")
    return Ventilation(False, "air quality within limits")


@dataclass(frozen=True)
class HumidityAction:
    action: str  # "dehumidify", "humidify" or "none"
    reason: str


def humidity_action(humidity_pct: float | None, low: float = 40.0, high: float = 60.0) -> HumidityAction:
    """Keep relative humidity inside [low, high]. Outside the band, run the matching device."""
    if humidity_pct is None:
        return HumidityAction("none", "no humidity reading")
    if humidity_pct > high:
        return HumidityAction("dehumidify", f"{humidity_pct:.0f} % above {high:.0f} %")
    if humidity_pct < low:
        return HumidityAction("humidify", f"{humidity_pct:.0f} % below {low:.0f} %")
    return HumidityAction("none", "within band")


def minimum_time_ok(current_state: bool, since_change_min: float, min_on_min: float, min_off_min: float) -> bool:
    """True when the device may change state now. A compressor or heater must run or rest for its minimum time."""
    minimum = min_on_min if current_state else min_off_min
    return since_change_min >= minimum


def timed_preset_active(started_at_min: float | None, now_min: float, duration_min: float) -> bool:
    """A timed preset holds from its start for its duration, then reverts."""
    if started_at_min is None:
        return False
    return 0.0 <= now_min - started_at_min < duration_min


def price_shifted_target(comfort_c: float, price_per_kwh: float | None, cheap_per_kwh: float, expensive_per_kwh: float, max_shift_c: float = 1.0) -> float:
    """Move the target by up to `max_shift_c` with the electricity price.

    Below `cheap_per_kwh` the target moves down by the full shift (cool a little more while power is cheap).
    Above `expensive_per_kwh` it moves up by the full shift. In between the shift is linear.
    A missing price leaves the target at comfort.
    """
    if price_per_kwh is None:
        return comfort_c
    if expensive_per_kwh <= cheap_per_kwh:
        raise ValueError("expensive_per_kwh must be above cheap_per_kwh")
    if price_per_kwh <= cheap_per_kwh:
        return comfort_c - max_shift_c
    if price_per_kwh >= expensive_per_kwh:
        return comfort_c + max_shift_c
    fraction = (price_per_kwh - cheap_per_kwh) / (expensive_per_kwh - cheap_per_kwh)
    return comfort_c - max_shift_c + 2.0 * max_shift_c * fraction


@dataclass(frozen=True)
class ScheduleBlock:
    start_h: float  # hour of day, 0 to 24
    end_h: float  # hour of day, 0 to 24; may be less than start_h for a block that crosses midnight
    preset: str


def scheduled_preset(now_h: float, blocks: list[ScheduleBlock], default: str = "comfort") -> str:
    """The preset of the first block that covers `now_h`, or `default` when none does.

    A block covers [start_h, end_h). A block whose end is before its start wraps past midnight.
    """
    if not 0.0 <= now_h < 24.0:
        raise ValueError("now_h must be in [0, 24)")
    for block in blocks:
        if block.start_h <= block.end_h:
            if block.start_h <= now_h < block.end_h:
                return block.preset
        elif now_h >= block.start_h or now_h < block.end_h:
            return block.preset
    return default
