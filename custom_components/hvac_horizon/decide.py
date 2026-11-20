"""Turn the room state into one AC action per control cycle. Pure logic.

Priority, highest first:
1. An open window longer than its delay pauses the AC.
2. Nobody home sets the away setpoint.
3. A scheduled arrival pre-cools when the planner says to start now.
4. Otherwise the comfort setpoint holds.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


COOL_MIN_OUTDOOR_C = 16.0  # below this outdoors, cooling is blocked


class Mode(str, Enum):
    OFF = "off"
    COOL = "cool"


@dataclass(frozen=True)
class RoomState:
    room_c: float
    window_open_min: float  # minutes the window has been open, 0 when closed
    someone_home: bool
    precool_start_in_min: float | None  # from the planner; None when no plan exists
    current_setpoint_c: float | None = None
    outdoor_c: float | None = None  # None means unknown: the outdoor limit is not applied
    night_precool_c: float | None = None  # set when the night plan says pre-cool: cool to this target, away or home


class Preset(str, Enum):
    COMFORT = "comfort"
    ECO = "eco"
    BOOST = "boost"
    AWAY = "away"


@dataclass(frozen=True)
class Settings:
    comfort_c: float
    away_c: float
    window_delay_min: float = 10.0
    eco_c: float | None = None  # setpoint for eco, a few degrees above comfort; None when unset
    boost_c: float | None = None  # setpoint for boost, a few degrees below comfort; None when unset
    preset: Preset = Preset.COMFORT


def cool_above_c(settings: Settings, someone_home: bool) -> float:
    """Room temperature above which decide() cools. Away uses eco when set, else the away setback."""
    if not someone_home:
        return settings.eco_c if settings.eco_c is not None else settings.away_c
    return _preset_setpoint(settings)


def _preset_setpoint(settings: Settings) -> float:
    """The setpoint the active preset asks for. Eco and boost fall back to comfort when unset."""
    if settings.preset is Preset.ECO:
        return settings.eco_c if settings.eco_c is not None else settings.comfort_c
    if settings.preset is Preset.BOOST:
        return settings.boost_c if settings.boost_c is not None else settings.comfort_c
    if settings.preset is Preset.AWAY:
        return settings.away_c
    return settings.comfort_c


@dataclass(frozen=True)
class Action:
    mode: Mode
    setpoint_c: float | None  # None when the AC is off
    reason: str


def decide(state: RoomState, settings: Settings) -> Action:
    if state.window_open_min >= settings.window_delay_min:
        return Action(Mode.OFF, None, f"window open {state.window_open_min:.0f} min")
    if state.outdoor_c is not None and state.outdoor_c < COOL_MIN_OUTDOOR_C:
        return Action(Mode.OFF, None, f"outdoor {state.outdoor_c:.0f} C below cooling limit")
    if state.night_precool_c is not None and state.room_c > state.night_precool_c:
        return Action(Mode.COOL, state.night_precool_c, "night pre-cool")
    if not state.someone_home:
        # Away: hold the setback only when the room is above it. Never switch on for a cooler room.
        away_target = cool_above_c(settings, False)
        if state.room_c > away_target:
            reason = "eco when unoccupied" if settings.eco_c is not None else "away setback"
            return Action(Mode.COOL, away_target, reason)
        return Action(Mode.OFF, None, "away, room within setback")
    if state.precool_start_in_min is not None and state.precool_start_in_min <= 0:
        return Action(Mode.COOL, settings.comfort_c, "pre-cool starts now")
    target = cool_above_c(settings, True)
    if state.room_c > target:
        return Action(Mode.COOL, target, f"above {settings.preset.value} target")
    return Action(Mode.OFF, None, f"at or below {settings.preset.value} target")
