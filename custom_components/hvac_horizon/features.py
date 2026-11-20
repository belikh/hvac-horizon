"""Pure decision functions ported from the feature sets of the other projects. No Home Assistant imports.

Each function is one decision with its own tests, so it can be wired into a control cycle later.
"""

from __future__ import annotations

import math
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


def sun_heat_gain(irradiance_w_m2: float, window_area_m2: float, shgc: float = 0.6) -> float:
    """Solar heat gain through a window, in kW: irradiance times area times solar heat gain coefficient.

    Ported from the solar gain estimate in RoomMind. Irradiance is the measured or modelled value on the window.
    """
    if irradiance_w_m2 < 0 or window_area_m2 < 0 or not 0.0 <= shgc <= 1.0:
        raise ValueError("irradiance and area must be non-negative, shgc in [0, 1]")
    return irradiance_w_m2 * window_area_m2 * shgc / 1000.0


def cover_action(solar_gain_kw: float, threshold_kw: float, cover_open: bool) -> str:
    """'close' when solar gain is above the threshold and the cover is open, 'open' when it is below and the cover is closed, else 'hold'.

    Ported from RoomMind's cover control. Covers only cut solar gain, so the decision uses the threshold.
    """
    if solar_gain_kw > threshold_kw and cover_open:
        return "close"
    if solar_gain_kw <= threshold_kw and not cover_open:
        return "open"
    return "hold"


def comfort_band(comfort_c: float, band_c: float = 1.0) -> tuple[float, float]:
    """Heat and cool edges around comfort. Heating starts below the low edge, cooling above the high edge.

    Ported from ClimateAdvisor's comfort bands. The band is symmetric by default.
    """
    if band_c < 0:
        raise ValueError("band_c must be non-negative")
    return comfort_c - band_c, comfort_c + band_c


def band_action(room_c: float, low_c: float, high_c: float) -> str:
    """'cool' above the high edge, 'heat' below the low edge, otherwise 'none'."""
    if low_c > high_c:
        raise ValueError("low edge must not exceed high edge")
    if room_c > high_c:
        return "cool"
    if room_c < low_c:
        return "heat"
    return "none"


def window_open_pause(window_open_min: float, delay_min: float = 10.0) -> bool:
    """True once a window has been open for `delay_min` minutes. Brief openings do not pause the AC.

    Ported from the window handling in Versatile Thermostat and RoomMind. Same rule as the decision logic,
    kept here as a separate pure check for the status.
    """
    return window_open_min >= delay_min


def mould_risk(surface_humidity_pct: float | None, threshold_pct: float = 80.0) -> bool:
    """True when the surface humidity is at or above `threshold_pct`. Missing readings are not a risk.

    Ported from RoomMind's mould risk. Surface humidity is estimated from room humidity and wall temperature.
    """
    return surface_humidity_pct is not None and surface_humidity_pct >= threshold_pct


def surface_humidity(room_humidity_pct: float, room_c: float, surface_c: float) -> float:
    """Relative humidity at a cold surface, from the room's relative humidity and temperature.

    Uses the Magnus approximation for saturation vapour pressure. Surface humidity rises as the surface gets colder.
    """
    def saturation_hpa(t: float) -> float:
        return 6.112 * math.exp(17.62 * t / (243.12 + t))

    if not 0.0 <= room_humidity_pct <= 100.0:
        raise ValueError("room humidity must be in [0, 100]")
    vapour = room_humidity_pct / 100.0 * saturation_hpa(room_c)
    return min(vapour / saturation_hpa(surface_c) * 100.0, 100.0)


def compliance_score(in_band_minutes: float, total_minutes: float) -> float | None:
    """Share of time the room was inside the comfort band, as a percentage. None when there is no time yet.

    Ported from ClimateAdvisor's compliance sensor.
    """
    if total_minutes <= 0:
        return None
    if in_band_minutes < 0 or in_band_minutes > total_minutes:
        raise ValueError("in-band minutes must be between 0 and the total")
    return round(100.0 * in_band_minutes / total_minutes, 1)


def weather_day_summary(highs_c: list[float]) -> dict[str, int]:
    """Count of each day class across the forecast highs. Ported from ClimateAdvisor's forecast summary."""
    counts: dict[str, int] = {d.value: 0 for d in DayType}
    for high in highs_c:
        counts[classify_day(high).value] += 1
    return counts


def hot_streak(highs_c: list[float], threshold_c: float = 30.0, min_days: int = 3) -> bool:
    """True when at least `min_days` consecutive forecast days reach `threshold_c`. Triggers pre-cooling over a heatwave."""
    run = 0
    for high in highs_c:
        run = run + 1 if high >= threshold_c else 0
        if run >= min_days:
            return True
    return False


def fan_speed_from_gap(room_c: float, target_c: float, max_gap_c: float = 3.0) -> int:
    """Fan speed 0 to 3 from the gap between the room and the target. Ported from Versatile Thermostat's auto fan mode.

    Gap 0 or below is speed 0. The top speed is reached at `max_gap_c`.
    """
    if max_gap_c <= 0:
        raise ValueError("max_gap_c must be positive")
    gap = room_c - target_c
    if gap <= 0:
        return 0
    return min(3, 1 + int(2 * gap / max_gap_c))


def drying_needed(humidity_pct: float | None, band_high_pct: float = 60.0, cool_gap_c: float = 0.0) -> bool:
    """True when the room is above the humidity band and no cooling is needed (cool gap at or below zero).

    Ported from ClimateAdvisor's humidity handling. Drying through the AC's dry mode happens only then.
    """
    return humidity_pct is not None and humidity_pct > band_high_pct and cool_gap_c <= 0.0


def stale_price_fallback(price_per_kwh: float | None, last_good_per_kwh: float | None, last_good_age_min: float, max_age_min: float = 60.0) -> float | None:
    """Use the current price, or the last good price for up to `max_age_min` minutes. None after that.

    Ported from the price fallback in RoomMind. A stale price is kept for a short time so a brief outage does not
    stop price-based decisions, but it is not used indefinitely.
    """
    if price_per_kwh is not None:
        return price_per_kwh
    if last_good_per_kwh is not None and last_good_age_min <= max_age_min:
        return last_good_per_kwh
    return None


def ventilation_rate_from_co2(co2_ppm: float | None, low_ppm: float = 800.0, high_ppm: float = 1200.0) -> float:
    """Fan rate from 0 to 1 by CO2: 0 below `low_ppm`, 1 at `high_ppm` and above, linear in between.

    Gives a proportional ventilation rate instead of the on-off rule. Missing CO2 gives 0.
    """
    if high_ppm <= low_ppm:
        raise ValueError("high_ppm must be above low_ppm")
    if co2_ppm is None or co2_ppm <= low_ppm:
        return 0.0
    if co2_ppm >= high_ppm:
        return 1.0
    return (co2_ppm - low_ppm) / (high_ppm - low_ppm)
