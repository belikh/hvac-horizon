"""Rule-based answer to "why did the AC run or not run", and a per-day budget for model calls.

Everything here is pure logic. The answer comes from the last decision and the recent actions. No model is
involved. The coordinator calls a conversation agent only when a flow field sets one, and CallBudget caps those calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

RECENT_LIMIT = 5  # actions listed with each answer


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
        verb = "running" if mode == "cool" else "not running" if mode == "off" else mode
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
