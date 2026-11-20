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


def heating_failure(heater_on_min: float, temp_rise_c: float, window_min: float = 30.0, min_rise_c: float = 0.2) -> bool:
    """True when the heater has run for `window_min` or longer and the room has risen less than `min_rise_c`.

    Ported from the heating failure detection in Versatile Thermostat. Only meaningful while the heater is commanded on.
    """
    return heater_on_min >= window_min and temp_rise_c < min_rise_c


def sensor_stalled(last_update_s: float | None, now_s: float, stall_s: float = 900.0) -> bool:
    """True when a sensor has not updated for `stall_s` seconds, or has never updated."""
    if last_update_s is None:
        return True
    return now_s - last_update_s >= stall_s


def safety_output(sensor_stalled_flag: bool, requested_pct: float, fallback_pct: float = 0.0, minimum_pct: float = 0.0) -> float:
    """Output to use. When the sensor has stalled, use the fallback, but never below the minimum.

    Ported from the safety fallback in Versatile Thermostat.
    """
    if not 0.0 <= minimum_pct <= 100.0 or not 0.0 <= fallback_pct <= 100.0:
        raise ValueError("percentages must be in [0, 100]")
    if sensor_stalled_flag:
        return max(fallback_pct, minimum_pct)
    return max(min(requested_pct, 100.0), minimum_pct)


def daily_briefing(day_type: str, comfort_c: float, next_action: str | None, window_close_h: float | None) -> str:
    """One-line plan for the day. Ported from the daily briefing in ClimateAdvisor."""
    parts = [f"Day: {day_type}.", f"Comfort {comfort_c:.1f} C."]
    if next_action:
        parts.append(f"Next: {next_action}.")
    if window_close_h is not None:
        hour = int(window_close_h) % 24
        minute = int(round((window_close_h - int(window_close_h)) * 60))
        parts.append(f"Close windows by {hour:02d}:{minute:02d}.")
    return " ".join(parts)


def night_plan(outdoor_low_c: float, indoor_c: float, comfort_c: float, cool_below_c: float = 20.0) -> str:
    """Overnight plan from the forecast low and the room temperature. Ported from ClimateAdvisor's night strategy.

    Returns "pre-cool", "hold" or "none". Pre-cool when the room is above comfort and the outdoor low
    is cool enough to flush heat overnight. Hold when the room is already at comfort. None otherwise.
    """
    if indoor_c > comfort_c and outdoor_low_c <= cool_below_c:
        return "pre-cool"
    if indoor_c <= comfort_c:
        return "hold"
    return "none"


def learning_suggestion(current_k: float, learned_k: float, tolerance: float = 0.2) -> str | None:
    """Suggest a change when the learned heat gain differs from the current value by more than `tolerance` (relative).

    Ported from ClimateAdvisor's learning suggestions. Returns text for the user, or None when no change is needed.
    """
    if current_k <= 0:
        return None
    change = (learned_k - current_k) / current_k
    if abs(change) <= tolerance:
        return None
    direction = "more" if change > 0 else "less"
    return f"Heat gain is {abs(change) * 100:.0f} % {direction} than the current model. Review the room model."


def vacation_target(vacation_on: bool, vacation_c: float, normal_target_c: float) -> float:
    """While vacation mode is on, hold the vacation temperature. Otherwise keep the normal target.

    Ported from RoomMind's vacation mode, which holds a lower target through one switch.
    """
    return vacation_c if vacation_on else normal_target_c


def presence_mode(home_states: list[bool], away_delay_min: float, minutes_since_last_home: float) -> str:
    """Occupancy mode from several presence sources. 'home' if any source is home. 'away' only after the delay.

    Ported from RoomMind's presence and vacancy handling. An empty list means no sources, so the room is treated as home.
    """
    if not home_states or any(home_states):
        return "home"
    return "away" if minutes_since_last_home >= away_delay_min else "home"
