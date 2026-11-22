"""Rule-based answers to "why did the AC run or not run", and a per-day budget for model calls.

Everything here is pure logic. The answer comes from the last decision and the recent actions. No model is
involved. The 24-hour report builds its text from the stored readings. The coordinator calls a conversation agent
only when a flow field sets one, and CallBudget caps those calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

RECENT_LIMIT = 5  # actions listed with each answer
REPORT_WINDOW_MIN = 24 * 60.0  # the on-demand report covers the last 24 hours
MAX_CHANGES_LISTED = 50  # mode changes listed in the model prompt, newest kept
REPORT_INSTRUCTION = (
    "Write a short report on this AC for the last 24 hours. Cover the activity, anything in the readings that "
    "does not fit, and the likely cause. Label each claim as fact or inference. Use plain text with no headings."
)


@dataclass(frozen=True)
class ActionRecord:
    minutes: float  # time of the action, minutes since the Unix epoch
    text: str  # the action as the coordinator publishes it, for example "cool: above comfort target"


def investigate(
    action: str | None,
    room_c: float | None,
    cool_above_c: float | None,
    someone_home: bool,
    window_open_min: float,
    history: list[ActionRecord],
) -> dict[str, object]:
    """Explain the current action from the inputs that decided it. Returns the answer and the last actions."""
    if not action:
        answer = "No decision yet. The control path has not run."
    else:
        mode, _, reason = action.partition(":")
        mode = mode.strip()
        reason = reason.strip() or "no reason recorded"
        verb = {"cool": "running", "off": "not running", "dry": "drying", "heat": "heating", "fan": "running its fan"}.get(mode, mode)
        parts = [f"The AC is {verb}: {reason}."]
        if room_c is not None and cool_above_c is not None:
            parts.append(f"Room {room_c:.1f} C, cool above {cool_above_c:.1f} C.")
        if not someone_home:
            parts.append("Nobody is home, so the away setback applies.")
        if window_open_min > 0:
            parts.append(f"A window has been open for {window_open_min:.0f} min.")
        answer = " ".join(parts)
    recent = [record.text for record in history[-RECENT_LIMIT:]]
    return {"answer": answer, "recent": recent}


@dataclass(frozen=True)
class DayReading:
    minutes: float  # time of the reading, minutes since the Unix epoch
    room_c: float  # room temperature at the reading
    outdoor_c: float  # outdoor temperature at the reading
    mode: str | None  # AC mode at the reading, for example "cool", "heat" or "off"


def _mode_changes(readings: list[DayReading]) -> list[DayReading]:
    """Readings whose AC mode differs from the reading before. Input must be sorted by time."""
    return [current for previous, current in zip(readings, readings[1:]) if current.mode != previous.mode]


def day_summary(readings: list[DayReading], now_min: float) -> str:
    """Rule-based report over the readings. Used when no model answers."""
    if not readings:
        return "No readings in the last 24 hours."
    ordered = sorted(readings, key=lambda r: r.minutes)
    rooms = [r.room_c for r in ordered]
    outdoor = [r.outdoor_c for r in ordered]
    running = sum(1 for r in ordered if r.mode in ("cool", "heat"))
    changes = _mode_changes(ordered)
    lines = [
        f"Last 24 hours: {len(ordered)} readings, {len(changes)} mode changes.",
        f"Room {min(rooms):.1f} to {max(rooms):.1f} C, last {rooms[-1]:.1f} C.",
        f"Outdoor {min(outdoor):.1f} to {max(outdoor):.1f} C.",
        f"AC in cool or heat mode for {100 * running / len(ordered):.0f} % of readings.",
    ]
    if changes:
        last = changes[-1]
        lines.append(f"Last mode change: {last.mode}, {now_min - last.minutes:.0f} min ago.")
    return "\n".join(lines)


def day_context(readings: list[DayReading], now_min: float) -> str:
    """The rule-based summary, then the mode changes oldest first, as the model prompt body."""
    body = [day_summary(readings, now_min)]
    listed = [f"{now_min - c.minutes:.0f} min ago: {c.mode}" for c in _mode_changes(sorted(readings, key=lambda r: r.minutes))[-MAX_CHANGES_LISTED:]]
    if listed:
        body += ["Mode changes, oldest first:", *listed]
    return "\n".join(body)


class CallBudget:
    """Counts model calls per calendar day. A limit of 0 allows no call."""

    def __init__(self) -> None:
        self._day: date | None = None
        self._count = 0

    def used(self, day: date) -> int:
        return self._count if day == self._day else 0

    def try_take(self, day: date, limit: int) -> bool:
        """Take one call from today's budget. Returns False when the budget is spent."""
        if day != self._day:
            self._day, self._count = day, 0
        if self._count >= limit:
            return False
        self._count += 1
        return True
