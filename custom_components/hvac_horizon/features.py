"""Pure decision functions ported from the feature sets of the other projects. No Home Assistant imports.

Each function is one decision with its own tests, so it can be wired into a control cycle later.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from custom_components.hvac_horizon.const import CONF_CLIMATE, CONF_CLIMATE_2, CONF_HEATER_SWITCH, CONF_VALVE, HEAT_BOOST_OFFSET_C, HEAT_ECO_OFFSET_C


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


def heat_source(outdoor_c: float, boiler_below_c: float = 5.0) -> str:
    """Heat source for a room that needs heat: "boiler" at or below `boiler_below_c` outdoors, otherwise "ac"."""
    return "boiler" if outdoor_c <= boiler_below_c else "ac"


def motion_preset(motion_on: bool | None, minutes_since_motion: float, away_delay_min: float = 30.0) -> str | None:
    """Preset from a motion sensor: "home" while motion is on or recent, "away" after `away_delay_min` without motion.

    Returns None when there is no sensor (`motion_on` is None). Raises ValueError if `away_delay_min` <= 0.
    """
    if away_delay_min <= 0:
        raise ValueError("away_delay_min must be positive")
    if motion_on is None:
        return None
    if motion_on:
        return "home"
    return "away" if minutes_since_motion >= away_delay_min else "home"


def boiler_demand(room_c: float, comfort_low_c: float, source: str) -> bool:
    """True when the boiler is the chosen source and the room is below the low edge of the comfort band."""
    return source == "boiler" and room_c < comfort_low_c


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


def timed_preset_name(active: bool, timed_preset: str | None, normal_preset: str) -> str:
    """The timed preset while it is active, otherwise the normal preset."""
    if active and timed_preset is not None:
        return timed_preset
    return normal_preset


def round_to_step(value: float, step: float) -> float:
    """Round a setpoint to the nearest multiple of the device's step."""
    if step <= 0:
        raise ValueError("step must be positive")
    return round(round(value / step) * step, 6)


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


def minutes_until_preset(now_h: float, blocks: list[ScheduleBlock], preset: str, horizon_min: int = 1440) -> float | None:
    """Minutes from `now_h` to the next minute the schedule selects `preset`, within `horizon_min`. None when it never does."""
    for minute in range(1, horizon_min + 1):
        if scheduled_preset((now_h + minute / 60.0) % 24.0, blocks) == preset:
            return float(minute)
    return None


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


def heating_failed(minutes_on: float, rise_c: float, window_min: float = 60.0, min_rise_c: float = 0.5) -> bool:
    """True when the heater has been commanded on for `window_min` or longer and the room has risen less than `min_rise_c`.

    Ported from the heating failure detection in Versatile Thermostat. Only meaningful while the heater is commanded on.
    """
    return minutes_on >= window_min and rise_c < min_rise_c


def heat_setpoint(preset: str, comfort_c: float, heat_eco_c: float | None, heat_boost_c: float | None, motion_idle: bool) -> float:
    """Heat target for an occupied house. Boost uses its heat setpoint. Eco and motion idle use the eco heat setpoint.

    An unset heat setpoint falls back to comfort minus HEAT_ECO_OFFSET_C (eco) or comfort plus HEAT_BOOST_OFFSET_C (boost).
    Every other preset heats to comfort.
    """
    if preset == "boost":
        return heat_boost_c if heat_boost_c is not None else comfort_c + HEAT_BOOST_OFFSET_C
    if preset == "eco" or motion_idle:
        return heat_eco_c if heat_eco_c is not None else comfort_c - HEAT_ECO_OFFSET_C
    return comfort_c


def heat_on_below(target_c: float, deadband_c: float) -> float:
    """The heater heats when the room is below this value: the effective target minus the deadband.

    The AC cools above the same target (decide.cool_above_c), so the heater and the AC never both run
    at one temperature. Between the two thresholds both are off.
    """
    if deadband_c < 0:
        raise ValueError("deadband_c must not be negative")
    return target_c - deadband_c


def switch_feedback(commanded_on: bool | None, actual_state: str | None) -> str:
    """Compare the heater switch's actual state with the last commanded state.

    Returns "not_configured", "unavailable", "match" or "mismatch".
    """
    if commanded_on is None:
        return "not_configured"
    if actual_state is None or actual_state in ("unavailable", "unknown"):
        return "unavailable"
    if (actual_state == "on" and commanded_on) or (actual_state == "off" and not commanded_on):
        return "match"
    return "mismatch"


# Seconds without a report before a sensor counts as stalled. Also how long no room may lack a valid reading before the AC stops.
SENSOR_STALL_S = 900.0


def sensor_stalled(last_update_s: float | None, now_s: float, stall_s: float = SENSOR_STALL_S) -> bool:
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


def suggestion_visible(suggestion: str | None, dismissed_for_k: float | None, learned_k: float | None) -> str | None:
    """Return the suggestion unless the user dismissed it at this learned gain (within 1 % relative)."""
    if suggestion is None or learned_k is None:
        return None
    if dismissed_for_k is not None and abs(learned_k - dismissed_for_k) <= 0.01 * max(abs(learned_k), 1e-9):
        return None
    return suggestion


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


def presence_sources(value: Any) -> list[str]:
    """Presence entity ids from the stored value. Accepts a list (current), one id string (older entries), or None."""
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value if item]


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


def natural_ventilation_window(outdoor_c: float, indoor_c: float, min_delta_c: float = 2.0, max_outdoor_c: float = 24.0, cool_above_c: float | None = None) -> str:
    """'open' when fresh air will cool the room: outdoors is cooler than indoors by `min_delta_c` and not above `max_outdoor_c`.
    'close' when outdoors is warmer than indoors or above the cap. 'hold' otherwise.

    When `cool_above_c` is given and the room is at or below it, the room needs no cooling and the answer is 'close'.
    Ported from ClimateAdvisor's natural ventilation timing. The decision is for windows, not the AC.
    """
    if cool_above_c is not None and indoor_c <= cool_above_c:
        return "close"
    if outdoor_c > max_outdoor_c or outdoor_c >= indoor_c:
        return "close"
    if indoor_c - outdoor_c >= min_delta_c:
        return "open"
    return "hold"


def economizer_phase(outdoor_c: float, indoor_c: float, comfort_c: float, minutes_in_open: float, min_delta_c: float = 2.0, max_outdoor_c: float = 24.0, fan_after_min: float = 30.0, cool_above_c: float | None = None) -> str:
    """Two-phase window cooling advice: 'off', 'open_windows' or 'fan_assist'.

    'off' when outdoors is above `max_outdoor_c` or less than `min_delta_c` cooler than indoors.
    Also 'off' when `cool_above_c` is given and the room is at or below it, because the room needs no cooling.
    Otherwise 'fan_assist' when the room is above `comfort_c` after `fan_after_min` minutes of open windows, else 'open_windows'.
    Ported from ClimateAdvisor's economizer FSM. Publishes advice only.
    """
    if min_delta_c < 0 or fan_after_min <= 0:
        raise ValueError("min_delta_c must be >= 0 and fan_after_min must be > 0")
    if cool_above_c is not None and indoor_c <= cool_above_c:
        return "off"
    if outdoor_c > max_outdoor_c or outdoor_c > indoor_c - min_delta_c:
        return "off"
    if indoor_c > comfort_c and minutes_in_open >= fan_after_min:
        return "fan_assist"
    return "open_windows"


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


def mould_target(target_c: float, mould: bool, bump_c: float = 1.0) -> float:
    """Raise `target_c` by `bump_c` when there is mould risk, so the room warms and surface humidity drops.

    Ported from RoomMind's mould manager. Raises ValueError if `bump_c` is negative.
    """
    if bump_c < 0:
        raise ValueError("bump_c must be >= 0")
    return target_c + bump_c if mould else target_c


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


def forecast_highs(forecast_items: list[dict]) -> list[float]:
    """Daily highs from Home Assistant forecast dicts. Items without a numeric "temperature" are skipped."""
    highs: list[float] = []
    for item in forecast_items:
        value = item.get("temperature")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            highs.append(float(value))
    return highs


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


def fan_mode_for_gap(gap_c: float, available: list[str] | None, high_above_c: float = 2.0, low_below_c: float = 0.5) -> str | None:
    """Climate fan mode for the room's gap above the target: "high" above `high_above_c`, "auto" (or "low") below `low_below_c`.

    Returns None between the two limits. Returns None too when the device lists no fan modes, or does not list the mode to send.
    """
    if high_above_c <= low_below_c:
        raise ValueError("high_above_c must be above low_below_c")
    if not available:
        return None
    if gap_c > high_above_c:
        return "high" if "high" in available else None
    if gap_c < low_below_c:
        return next((mode for mode in ("auto", "low") if mode in available), None)
    return None


def window_actuator_command(open_advice: bool, indoor_c: float, outdoor_c: float, is_open: bool, opened_by_us: bool, min_delta_c: float = 2.0) -> str | None:
    """"open" when the advice says open windows, outdoors is at least `min_delta_c` cooler than indoors and the actuator is shut.

    "close" when the advice has ended or the air is no longer cool enough, and this integration opened the actuator, and it is open.
    None otherwise. An actuator this integration did not open is never closed.
    """
    cooler = indoor_c - outdoor_c >= min_delta_c
    if open_advice and cooler:
        return None if is_open else "open"
    if opened_by_us and is_open:
        return "close"
    return None


def peer_warmer(room_c: float, peer_room_c: float, min_delta_c: float = 2.0) -> bool:
    """True when this room is at least `min_delta_c` warmer than the peer entry's room, so the peer valve should open."""
    return room_c - peer_room_c >= min_delta_c


def fan_assist_on(phase: str | None, enabled: bool, unlocked: bool, house_home: bool, stalled: bool) -> bool:
    """True when the economizer fan runs: the option is on, the phase is fan_assist, and control is unlocked,
    the house is home and no sensor is stalled. The window actuator uses the same gates."""
    return enabled and phase == "fan_assist" and unlocked and house_home and not stalled


MPC_IDLE_LOOKAHEAD = 4  # plan steps that must all be idle (1 hour at 15 min per step)


def mpc_idle(plan: list[bool] | None, room_c: float, cool_above_c: float, deadband_c: float, lookahead: int = MPC_IDLE_LOOKAHEAD) -> bool:
    """True when the plan may idle a cool decision.

    The room must be at most `deadband_c` above the cool threshold, and the first `lookahead` plan steps must all be
    idle (the plan expects the room to stay at or below target without the AC). A room more than `deadband_c` above
    the threshold is never idled. An empty or missing plan never idles.
    """
    if not plan or room_c > cool_above_c + deadband_c:
        return False
    return not any(plan[:lookahead])


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


def inhibited(now_min: float, inhibit_until_min: float | None) -> bool:
    """True while an inhibition period is running. Ported from Versatile Thermostat's inhibition manager.

    After a manual change the integration stops sending commands until `inhibit_until_min`.
    """
    return inhibit_until_min is not None and now_min < inhibit_until_min


def lock_allows_control(locked: bool) -> bool:
    """Control commands are allowed only when the lock is off. Ported from Versatile Thermostat's lock."""
    return not locked


def check_pin(entered: str | None, configured: str | None) -> bool:
    """True when the entered PIN matches the configured one. No PIN configured means the lock cannot be opened."""
    if not configured or entered is None:
        return False
    return entered == configured


def keep_alive_due(last_sent_min: float | None, now_min: float, interval_min: float = 5.0) -> bool:
    """True when a command must be re-sent to keep the device from timing out. Ported from Versatile Thermostat's keep-alive."""
    if last_sent_min is None:
        return True
    return now_min - last_sent_min >= interval_min


def valve_exercise_due(last_moved_days: float, interval_days: float = 7.0) -> bool:
    """True when an idle valve has not moved for `interval_days`, so it should be cycled to stop seizing.

    Ported from RoomMind's TRV valve protection.
    """
    return last_moved_days >= interval_days


def trv_offset(trv_c: list[float], room_c: list[float], max_abs_k: float = 5.0) -> float | None:
    """Median of (TRV reading minus room reading) over paired samples. Ported from RoomMind's head offset tracker.

    A TRV's own sensor sits on the radiator and reads differently from the room sensor. Differences larger than
    max_abs_k in absolute value are dropped as outliers. Returns None when no differences remain.
    Raises ValueError when the lists differ in length.
    """
    if len(trv_c) != len(room_c):
        raise ValueError("trv_c and room_c must have the same length")
    diffs = [t - r for t, r in zip(trv_c, room_c)]
    kept = [d for d in diffs if abs(d) <= max_abs_k]
    if not kept:
        return None
    return statistics.median(kept)


def corrected_setpoint(setpoint_c: float, offset_k: float | None) -> float:
    """Valve target for the room: setpoint_c plus the learned TRV offset.

    A TRV that reads warmer than the room closes its valve early, so the room stays cold. A positive offset
    raises the target, which opens the valve more. Returns setpoint_c unchanged when offset_k is None.
    """
    if offset_k is None:
        return setpoint_c
    return setpoint_c + offset_k



def whole_house_fan_allowed(house_sealed: bool, hvac_running: bool, outdoor_c: float | None, indoor_c: float, min_delta_c: float = 2.0) -> bool:
    """True when the whole-house fan may run to cool: outdoors must be cooler than indoors by `min_delta_c`,
    the house must not be sealed, and the AC must not be running.

    Ported from ClimateAdvisor's whole-house fan safety rules.
    """
    if outdoor_c is None or house_sealed or hvac_running:
        return False
    return indoor_c - outdoor_c >= min_delta_c


def comfort_energy_setpoint(comfort_c: float, energy_c: float, weight: float) -> float:
    """Blend comfort and energy setpoints. `weight` 0 gives comfort, 1 gives the energy setpoint.

    Ported from RoomMind's comfort-versus-energy weight.
    """
    if not 0.0 <= weight <= 1.0:
        raise ValueError("weight must be in [0, 1]")
    return comfort_c + (energy_c - comfort_c) * weight


def transition_message(previous: bool, current: bool, on_text: str, off_text: str) -> str | None:
    """Return `on_text` when a state turns True, `off_text` when it turns False, None when unchanged.

    Ported from ClimateAdvisor's notifications.
    """
    if current and not previous:
        return on_text
    if previous and not current:
        return off_text
    return None


def mode_disagrees(written_mode: str | None, actual_mode: str | None, setpoint_changed: bool) -> bool:
    """True when the AC mode differs from the last mode written and the setpoint did not change.

    A setpoint change is a manual change handled by inhibition. Ported from Versatile Thermostat's incorrect-state repair.
    """
    if written_mode is None or actual_mode is None or setpoint_changed:
        return False
    return written_mode != actual_mode


def pwm_on(output_pct: float, window_min: float, elapsed_min: float) -> bool:
    """Time-proportional output: on for the first output_pct % of each window."""
    if not 0 <= output_pct <= 100:
        raise ValueError("output_pct must be between 0 and 100")
    if window_min <= 0:
        raise ValueError("window_min must be positive")
    position = (elapsed_min % window_min) / window_min
    return position < output_pct / 100.0


# Width of the proportional valve band, kelvin. The TRV offset scale follows from it: 1 K of offset moves the opening by 100 / VALVE_BAND_C points.
VALVE_BAND_C = 1.0


def valve_opening_pct(room_c: float, target_c: float, band_c: float = VALVE_BAND_C) -> float:
    """Valve opening in percent from how far the room is below the target.

    Fully open at or below target minus `band_c`, closed at or above target, proportional between.
    Ported from RoomMind's modulating valve control and Versatile Thermostat's valve handling.
    """
    if band_c <= 0:
        raise ValueError("band_c must be positive")
    error = target_c - room_c
    if error >= band_c:
        return 100.0
    if error <= 0:
        return 0.0
    return error / band_c * 100.0


def shed_plan(
    total_w: float, max_w: float, loads: list[tuple[str, float, bool]], margin_w: float = 200.0
) -> list[str]:
    """Entity IDs to switch off so the load fits under `max_w`, lowest priority first.

    `loads` holds (entity_id, watts, is_on) with the lowest priority first. Loads that are off or
    have watts <= 0 are skipped. Returns [] below `max_w`. `margin_w` is validated here and is the
    hysteresis a caller applies before switching loads back on (below max_w - margin_w).
    Ported from Versatile Thermostat's power manager and central power manager.
    """
    if max_w <= 0:
        raise ValueError("max_w must be positive")
    if margin_w < 0:
        raise ValueError("margin_w must not be negative")
    if total_w < max_w:
        return []
    cut = total_w - max_w
    shed: list[str] = []
    freed = 0.0
    for entity_id, watts, is_on in loads:
        if freed >= cut:
            break
        if not is_on or watts <= 0:
            continue
        shed.append(entity_id)
        freed += watts
    return shed


# A shed load comes back on only while the house stays under this share of the limit.
SHED_RESTORE_FRACTION = 0.9


def shed_restore(total_w: float, max_w: float, shed_loads: list[tuple[str, float]]) -> str | None:
    """Entity ID of the shed load to switch back on this cycle, or None.

    `shed_loads` holds (entity_id, watts) for the loads this integration shed, lowest priority first.
    The highest priority load whose watts fit comes back first. A load fits when the house stays at or
    below 90 percent of `max_w` with its watts added, so a restore does not trigger a shed. One load per cycle.
    """
    if max_w <= 0:
        raise ValueError("max_w must be positive")
    for entity_id, watts in reversed(shed_loads):
        if total_w + watts <= max_w * SHED_RESTORE_FRACTION:
            return entity_id
    return None


def house_cooling(cooling: dict[str, tuple[str | None, bool]], entry_id: str, group: str | None) -> bool:
    """True when another entry in scope has its AC cooling.

    `cooling` maps each entry id to (compressor group or None, AC is cooling). An entry with a group looks at
    the entries in that group. An entry without a group looks at every entry.
    """
    return any(
        is_cooling and (group is None or other_group == group)
        for other_id, (other_group, is_cooling) in cooling.items()
        if other_id != entry_id
    )


def group_start_allowed(last_start_min: float | None, now_min: float, min_gap_min: float = 5.0) -> bool:
    """True when no AC in the compressor group started less than `min_gap_min` minutes before `now_min`.

    `last_start_min` is the time of the group's last AC start, or None when no AC has started in the group.
    """
    return last_start_min is None or now_min - last_start_min >= min_gap_min


def stagger_allowed(
    device: str,
    wanting: list[str],
    last_start_min: dict[str, float],
    now_min: float,
    min_gap_min: float = 5.0,
) -> bool:
    """True when `device` may start its compressor now.

    `wanting` lists the devices that want to start, earliest first. Another device that started
    less than `min_gap_min` minutes before `now_min` blocks the start, and only the first device
    in `wanting` may start in a cycle. Ported from RoomMind's compressor group manager.
    """
    if min_gap_min < 0:
        raise ValueError("min_gap_min must not be negative")
    if device not in wanting:
        return False
    for other, started in last_start_min.items():
        if other != device and now_min - started < min_gap_min:
            return False
    return wanting[0] == device


def mpc_plan(
    fit_k: float,
    fit_c: float,
    fit_g: float,
    room_c: float,
    target_c: float,
    outdoor_c: float,
    running_kw: float,
    prices: list[float],
    step_min: float = 15.0,
    discomfort_per_k_step: float = 1.0,
    cost_weight: float = 1.0,
) -> list[bool]:
    """Choose AC on/off per step by dynamic programming over room temperature.

    The room follows dT/dt = k (T_out - T) - c P + g (same as `forecast()`), with P = `running_kw` when on
    and 0 when off. Temperature is bucketed to 0.1 C over [target - 5, target + 10] and clamped.
    Step cost: cost_weight * price * kW * hours (when on) + discomfort_per_k_step * max(0, T - target).
    """
    if step_min <= 0:
        raise ValueError("step_min must be positive")
    if running_kw < 0:
        raise ValueError("running_kw must not be negative")
    if fit_c < 0:
        raise ValueError("fit_c must not be negative")
    if not prices:
        return []

    return _mpc_dp(
        room_c,
        target_c - 5.0,
        lambda temp, power: fit_k * (outdoor_c - temp) - fit_c * power + fit_g,
        running_kw,
        lambda temp: discomfort_per_k_step * max(0.0, temp - target_c),
        prices,
        step_min,
        cost_weight,
    )


def mpc_heat_plan(
    fit_k: float,
    fit_c: float,
    fit_g: float,
    room_c: float,
    target_c: float,
    outdoor_c: float,
    heat_kw: float,
    prices: list[float],
    step_min: float = 15.0,
    discomfort_per_k_step: float = 1.0,
    cost_weight: float = 1.0,
) -> list[bool]:
    """Choose heat on or off per step by dynamic programming over room temperature.

    The room follows dT/dt = k (T_out - T) + c P + g, with P = `heat_kw` when heating and 0 when idle. The heat side
    reuses the cooling coefficient c, so the heater adds heat at the rate the AC removes it. Temperature is bucketed
    to 0.1 C over [target - 10, target + 5] and clamped. Step cost: cost_weight * price * kW * hours (when heating)
    plus discomfort_per_k_step * max(0, target - T).
    """
    if step_min <= 0:
        raise ValueError("step_min must be positive")
    if heat_kw < 0:
        raise ValueError("heat_kw must not be negative")
    if fit_c < 0:
        raise ValueError("fit_c must not be negative")
    if not prices:
        return []
    return _mpc_dp(
        room_c,
        target_c - 10.0,
        lambda temp, power: fit_k * (outdoor_c - temp) + fit_c * power + fit_g,
        heat_kw,
        lambda temp: discomfort_per_k_step * max(0.0, target_c - temp),
        prices,
        step_min,
        cost_weight,
    )


def _mpc_dp(
    room_c: float,
    low: float,
    rate: Callable[[float, float], float],
    running_kw: float,
    discomfort_at: Callable[[float], float],
    prices: list[float],
    step_min: float,
    cost_weight: float,
) -> list[bool]:
    """Plan the on or off state per step. `rate(temp, power_kw)` is dT/dt in K/h, and the grid runs 15 K up from `low`.

    The state is the temperature bucket. Each step costs the discomfort at its start, plus the energy cost when on.
    """
    bucket = 0.1
    n = int(round(15.0 / bucket)) + 1
    temps = [low + i * bucket for i in range(n)]

    def index(temp: float) -> int:
        return min(n - 1, max(0, int(round((temp - low) / bucket))))

    # Integrate each step in sub-steps of at most 1 minute, like forecast().
    substeps = max(1, int(math.ceil(step_min)))
    dt_h = step_min / 60.0 / substeps

    def advance(temp: float, power_kw: float) -> float:
        for _ in range(substeps):
            temp += rate(temp, power_kw) * dt_h
        return temp

    nxt_off = [index(advance(t, 0.0)) for t in temps]
    nxt_on = [index(advance(t, running_kw)) for t in temps]
    discomfort = [discomfort_at(t) for t in temps]

    hours = step_min / 60.0
    horizon = len(prices)
    cost_to_go = [0.0] * n
    best_action = [[False] * n for _ in range(horizon)]
    for i in range(horizon - 1, -1, -1):
        energy_cost = cost_weight * prices[i] * running_kw * hours
        new_cost = [0.0] * n
        for b in range(n):
            off = discomfort[b] + cost_to_go[nxt_off[b]]
            on = discomfort[b] + energy_cost + cost_to_go[nxt_on[b]]
            if on < off:
                new_cost[b] = on
                best_action[i][b] = True
            else:
                new_cost[b] = off
        cost_to_go = new_cost

    plan: list[bool] = []
    state = index(room_c)
    for i in range(horizon):
        action = best_action[i][state]
        plan.append(action)
        state = nxt_on[state] if action else nxt_off[state]
    return plan


def apply_polarity(wanted_on: bool, inverted: bool) -> bool:
    """Return the state to command. An inverted switch is wired so that on means off."""
    return not wanted_on if inverted else wanted_on


def clamp_output(pct: float, lo: float = 0.0, hi: float = 100.0) -> float:
    """Limit an output percentage to [lo, hi]. Raises ValueError when lo is above hi."""
    if lo > hi:
        raise ValueError("lo must not exceed hi")
    return max(lo, min(hi, pct))


def boost_output(preset: str, pid_output_pct: float) -> float:
    """Heater output for the PWM pattern: 100.0 in boost, else the PID output clamped to [0, 100].

    Ported from the PID thermostat's boost handling. Raises ValueError when the output is NaN.
    """
    if math.isnan(pid_output_pct):
        raise ValueError("pid_output_pct must not be NaN")
    if preset == "boost":
        return 100.0
    return max(0.0, min(100.0, pid_output_pct))


def learned_solar_scale(modelled_kw: list[float], measured_kw: list[float], min_irradiance_kw: float = 0.05) -> float | None:
    """Median of modelled/measured solar gain, clamped to [0.1, 10.0]. Ported from RoomMind's solar sensor manager.

    Pairs whose measured value is below min_irradiance_kw are skipped. Returns None when no pairs remain.
    """
    if len(modelled_kw) != len(measured_kw):
        raise ValueError("modelled and measured lists must have the same length")
    ratios = [m / v for m, v in zip(modelled_kw, measured_kw) if v >= min_irradiance_kw]
    if not ratios:
        return None
    return max(0.1, min(10.0, statistics.median(ratios)))


def house_mode_from_store(store: dict[str, Any]) -> str:
    """The house-wide mode set by the set_house_mode service. Defaults to "home"."""
    return str(store.get("house_mode", "home"))


def effective_home(room_home: bool, house_mode: str) -> bool:
    """Return False when the house mode is "away", otherwise the room's own presence."""
    if house_mode not in ("home", "away"):
        raise ValueError(f"unknown house mode: {house_mode!r}")
    if house_mode == "away":
        return False
    return room_home


def coil_dry_due(was_cooling: bool, now_cooling: bool, dry_started_min: float | None, now_min: float, dry_minutes: float = 20.0) -> bool:
    """True when the AC should run in dry mode to dry its coil after cooling. Ported from RoomMind's AC coil dry manager.

    Starts when cooling has just stopped and no drying runs. Continues for dry_minutes after dry_started_min.
    Raises ValueError when dry_minutes is not positive.
    """
    if dry_minutes <= 0:
        raise ValueError("dry_minutes must be positive")
    if dry_started_min is None:
        return was_cooling and not now_cooling
    return now_min - dry_started_min < dry_minutes


def _residual_rise(peak_rise_k: float, minutes_since_off: float, tau_min: float = 20.0) -> float:
    """Extra room temperature rise in kelvin still to come after a heater stops.

    The rise decays exponentially: peak_rise_k * exp(-minutes_since_off / tau_min).
    Ported from RoomMind's radiator and underfloor residual heat model.
    Raises ValueError when minutes_since_off < 0, tau_min <= 0 or peak_rise_k < 0.
    """
    if minutes_since_off < 0:
        raise ValueError("minutes_since_off must be >= 0")
    if tau_min <= 0:
        raise ValueError("tau_min must be > 0")
    if peak_rise_k < 0:
        raise ValueError("peak_rise_k must be >= 0")
    return peak_rise_k * math.exp(-minutes_since_off / tau_min)


def residual_correction(
    room_c: float, peak_rise_k: float, minutes_since_off: float, tau_min: float = 20.0
) -> float:
    """Room temperature plus the residual rise still to come, so the heater gate reads the room warmer.

    The heater cuts off earlier, because the radiators keep adding heat after it stops.
    """
    return room_c + _residual_rise(peak_rise_k, minutes_since_off, tau_min)


def pid_gain(user_value: float, default: float, tuned: float | None) -> float:
    """Heater PID gain. A value the user set (not the default) wins. At the default, the tuned value applies."""
    if tuned is None or user_value != default:
        return user_value
    return tuned


def room_devices(entry_data: dict) -> dict[str, list[str]]:
    """Devices of the room, built from the flat config keys. Absent or empty keys give empty lists."""
    cooling = [entry_data[key] for key in (CONF_CLIMATE, CONF_CLIMATE_2) if entry_data.get(key)]
    heating = [entry_data[CONF_HEATER_SWITCH]] if entry_data.get(CONF_HEATER_SWITCH) else []
    valves = [entry_data[CONF_VALVE]] if entry_data.get(CONF_VALVE) else []
    return {"cooling": cooling, "heating": heating, "valves": valves}
