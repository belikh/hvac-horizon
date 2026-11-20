"""Pure logic for the collector: sample history, running power and price slots.

Nothing here imports Home Assistant, so the tests run without it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .forecast import PriceSlot
from .model import Sample

STEP_S = 300  # collector interval, seconds
TOLERANCE_S = 90  # accept a gap of STEP_S plus or minus this, otherwise break the chain
RUNNING_MIN_KW = 0.2  # below this the AC counts as off


@dataclass(frozen=True)
class Entry:
    """One collector reading. `t` is a Unix time in seconds."""

    t: float
    room_c: float
    outdoor_c: float
    power_w: float


def _running_kw(power_w: float) -> float:
    """Power in kW, or 0 when the AC is off. Fan and standby draw would otherwise dilute the cooling term."""
    kw = power_w / 1000.0
    return kw if kw >= RUNNING_MIN_KW else 0.0


def samples_from_entries(entries: list[Entry]) -> list[Sample]:
    """Pair consecutive readings that are one step apart. Gaps break the chain."""
    ordered = sorted(entries, key=lambda e: e.t)
    out: list[Sample] = []
    for a, b in zip(ordered, ordered[1:]):
        gap = b.t - a.t
        if abs(gap - STEP_S) > TOLERANCE_S:
            continue
        out.append(
            Sample(
                minutes=gap / 60.0,
                temp_c=a.room_c,
                temp_next_c=b.room_c,
                outdoor_c=a.outdoor_c,
                power_kw=_running_kw(a.power_w),
            )
        )
    return out


def running_power_kw(entries: list[Entry], now: float, window_s: float = 7200.0) -> float | None:
    """Mean power over the last `window_s` seconds, counting only readings with the AC running.

    Returns None when the AC has not run in the window. The forecast then needs a fallback.
    """
    on = [e.power_w / 1000.0 for e in entries if now - window_s <= e.t <= now and e.power_w / 1000.0 >= RUNNING_MIN_KW]
    if not on:
        return None
    return sum(on) / len(on)


def price_slots(forecast: list[tuple[float, float]], now: float, default_step_s: float = 1800.0) -> list[PriceSlot]:
    """Turn (start_time, $/kWh) pairs into slots relative to `now`, in minutes.

    The slot length is the gap to the next start time. The last slot uses `default_step_s`.
    Slots that ended before `now` are dropped.
    """
    ordered = sorted(forecast)
    slots: list[PriceSlot] = []
    for i, (start, value) in enumerate(ordered):
        if i + 1 < len(ordered):
            length = ordered[i + 1][0] - start
        else:
            length = default_step_s
        end = start + length
        if end <= now:
            continue
        slots.append(PriceSlot(start_min=(start - now) / 60.0, duration_min=length / 60.0, price_per_kwh=value))
    return slots
