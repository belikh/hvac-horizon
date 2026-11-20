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


@dataclass(frozen=True)
class Settings:
    comfort_c: float
    away_c: float
    window_delay_min: float = 10.0


@dataclass(frozen=True)
class Action:
    mode: Mode
    setpoint_c: float | None  # None when the AC is off
    reason: str


def decide(state: RoomState, settings: Settings) -> Action:
    if state.window_open_min >= settings.window_delay_min:
        return Action(Mode.OFF, None, f"window open {state.window_open_min:.0f} min")
    if not state.someone_home:
        # Away: hold the setback only when the room is above it. Never switch on for a cooler room.
        if state.room_c > settings.away_c:
            return Action(Mode.COOL, settings.away_c, "away setback")
        return Action(Mode.OFF, None, "away, room within setback")
    if state.precool_start_in_min is not None and state.precool_start_in_min <= 0:
        return Action(Mode.COOL, settings.comfort_c, "pre-cool starts now")
    if state.room_c > settings.comfort_c:
        return Action(Mode.COOL, settings.comfort_c, "above comfort")
    return Action(Mode.OFF, None, "at or below comfort")
