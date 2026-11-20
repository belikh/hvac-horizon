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
    """One collector reading. `t` is a Unix time in seconds.

    The optional fields default to None so older stored records still load.
    """

    t: float
    room_c: float
    outdoor_c: float
    power_w: float
    setpoint_c: float | None = None
    mode: str | None = None
    rooms_c: tuple[float, ...] = ()
    humidity_pct: float | None = None
    price: float | None = None
    energy_kwh: float | None = None  # cumulative energy counter, when the meter has one
    power_mean_w: float | None = None  # integral of power over the interval, from state changes


def _running_kw(power_w: float) -> float:
    """Power in kW, or 0 when the AC is off. Fan and standby draw would otherwise dilute the cooling term."""
    kw = power_w / 1000.0
    return kw if kw >= RUNNING_MIN_KW else 0.0


def interval_power_kw(a: Entry, b: Entry) -> float:
    """Mean AC power between two readings, in kW.

    Uses the energy counter when both readings have one, because it averages the
    whole interval and so handles a cycling compressor. Otherwise it uses the
    first reading's power. A counter that fell (a meter reset) also falls back.
    The interval mean from state changes, when present, comes first: it sees every change in between.
    """
    gap_h = (b.t - a.t) / 3600.0
    if b.power_mean_w is not None:
        return _running_kw(b.power_mean_w)
    if a.energy_kwh is not None and b.energy_kwh is not None and gap_h > 0 and b.energy_kwh >= a.energy_kwh:
        kw = (b.energy_kwh - a.energy_kwh) / gap_h
        return kw if kw >= RUNNING_MIN_KW else 0.0
    return _running_kw(a.power_w)


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
                power_kw=interval_power_kw(a, b),
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


MIN_CYCLE_SAMPLES = 36  # three hours of 5-minute samples per state
MIN_CYCLES = 3  # distinct AC-on runs


def has_enough_cycles(samples: list[Sample]) -> bool:
    """True when the data has enough AC-on and AC-off time, and enough separate runs, to fit safely."""
    on = [s.power_kw > 0 for s in samples]
    if sum(on) < MIN_CYCLE_SAMPLES or len(on) - sum(on) < MIN_CYCLE_SAMPLES:
        return False
    runs = sum(1 for prev, cur in zip([False] + on, on) if cur and not prev)
    return runs >= MIN_CYCLES
