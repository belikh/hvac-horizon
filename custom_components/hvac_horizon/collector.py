"""Pure logic for the collector: sample history, running power and price slots.

Nothing here imports Home Assistant, so the tests run without it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .forecast import PriceSlot
from .devices import HEATER, RoomSample
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
    heat_pct: float | None = None  # heater output in percent during the tick before this reading
    power_2_w: float | None = None  # power of the second AC at this reading, W; None when no second power sensor
    device_kw: dict[str, float] | None = None  # kW of each fleet device at this reading, by device_id; None without a fleet


def _running_kw(power_w: float) -> float:
    """Power in kW, or 0 when the AC is off. Fan and standby draw would otherwise dilute the cooling term."""
    kw = power_w / 1000.0
    return kw if kw >= RUNNING_MIN_KW else 0.0


def _interval_power_kw(a: Entry, b: Entry) -> float:
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


HEAT_MODE = "heat"
DRY_MODE = "dry"
NON_COOL_MODES = (HEAT_MODE, DRY_MODE, "fan_only")  # AC modes whose running power is not cooling


def _interval_kind(a: Entry, b: Entry, kw: float) -> str:
    """Label one interval: "off" (no AC power), "cool", "heat", "dry" or "other" (fan only).

    An interval with AC power counts as cooling when neither reading is in a non-cool mode. A reading with no
    mode (older stored records) counts as cool. Heat and dry intervals have their own labels.
    """
    if kw <= 0:
        return "off"
    modes = {a.mode, b.mode}
    if HEAT_MODE in modes:
        return "heat"
    if DRY_MODE in modes:
        return "dry"
    if "fan_only" in modes:
        return "other"
    return "cool"


def mode_interval_counts(entries: list[Entry]) -> dict[str, int]:
    """Number of one-step intervals per label: off, cool, heat, dry, other."""
    ordered = sorted(entries, key=lambda e: e.t)
    counts = {"off": 0, "cool": 0, "heat": 0, "dry": 0, "other": 0}
    for a, b in zip(ordered, ordered[1:]):
        if abs((b.t - a.t) - STEP_S) > TOLERANCE_S:
            continue
        counts[_interval_kind(a, b, _interval_power_kw(a, b))] += 1
    return counts


def samples_from_entries(entries: list[Entry]) -> list[Sample]:
    """Pair consecutive readings that are one step apart. Gaps break the chain.

    Intervals where the AC ran in heat, dry or fan-only mode are left out: they are not passive drift and not cooling.
    """
    return [sample for sample, _ in heat_pairs_from_entries(entries)]


def heat_pairs_from_entries(entries: list[Entry]) -> list[tuple[Sample, float]]:
    """Like samples_from_entries, with the heater output in percent during each interval (from the first reading)."""
    ordered = sorted(entries, key=lambda e: e.t)
    out: list[tuple[Sample, float]] = []
    for a, b in zip(ordered, ordered[1:]):
        gap = b.t - a.t
        if abs(gap - STEP_S) > TOLERANCE_S:
            continue
        kw = _interval_power_kw(a, b)
        if _interval_kind(a, b, kw) not in ("off", "cool"):
            continue
        sample = Sample(
            minutes=gap / 60.0,
            temp_c=a.room_c,
            temp_next_c=b.room_c,
            outdoor_c=a.outdoor_c,
            power_kw=kw,
        )
        out.append((sample, a.heat_pct or 0.0))
    return out


def room_samples_from_entries(entries: list[Entry]) -> list[RoomSample]:
    """Two-device samples from consecutive readings one step apart. The first AC is "ac", the second is "ac_2".

    The heater is "heater", 1 when the heater output was above 0 during the interval, else 0.
    """
    ordered = sorted(entries, key=lambda e: e.t)
    out: list[RoomSample] = []
    for a, b in zip(ordered, ordered[1:]):
        gap = b.t - a.t
        if abs(gap - STEP_S) > TOLERANCE_S:
            continue
        ac_kw = _interval_power_kw(a, b)
        if _interval_kind(a, b, ac_kw) not in ("off", "cool"):
            continue
        second = _running_kw(a.power_2_w) if a.power_2_w is not None else 0.0
        heater_on = 1.0 if (a.heat_pct or 0.0) > 0 else 0.0
        out.append(
            RoomSample(
                minutes=gap / 60.0,
                temp_c=a.room_c,
                temp_next_c=b.room_c,
                outdoor_c=a.outdoor_c,
                device_kw={"ac": ac_kw, "ac_2": second, HEATER: heater_on},
            )
        )
    return out


def fleet_samples_from_entries(entries: list[Entry]) -> list[RoomSample]:
    """Room samples whose device_kw holds the fleet devices' power, from consecutive readings one step apart."""
    ordered = sorted(entries, key=lambda e: e.t)
    out: list[RoomSample] = []
    for a, b in zip(ordered, ordered[1:]):
        if abs((b.t - a.t) - STEP_S) > TOLERANCE_S or not a.device_kw:
            continue
        out.append(
            RoomSample(
                minutes=(b.t - a.t) / 60.0,
                temp_c=a.room_c,
                temp_next_c=b.room_c,
                outdoor_c=a.outdoor_c,
                device_kw=dict(a.device_kw),
            )
        )
    return out


def running_power_kw(entries: list[Entry], now: float, window_s: float = 7200.0, field: str = "power_w") -> float | None:
    """Mean power over the last `window_s` seconds, counting only readings with the AC running.

    `field` is "power_w" for the first AC or "power_2_w" for the second. Returns None when the AC has not run in the window.
    The forecast then needs a fallback.
    """
    on = []
    for e in entries:
        value = getattr(e, field)
        if value is not None and now - window_s <= e.t <= now and value / 1000.0 >= RUNNING_MIN_KW:
            on.append(value / 1000.0)
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


def _resample_states(points: list[tuple[float, float]], start: float, end: float, step: float = STEP_S) -> list[tuple[float, float | None]]:
    """Last known value on a fixed grid. `points` are (time, value) state changes in any order.

    Each grid time takes the most recent state at or before it. Grid times before the first state get None.

    """
    if step <= 0 or end < start:
        raise ValueError("step must be positive and end must not precede start")
    ordered = sorted(points)
    out: list[tuple[float, float | None]] = []
    i = 0
    last: float | None = None
    t = start
    while t <= end:
        while i < len(ordered) and ordered[i][0] <= t:
            last = ordered[i][1]
            i += 1
        out.append((t, last))
        t += step
    return out


def entries_from_history(
    rooms: list[list[tuple[float, float]]],
    outdoor: list[tuple[float, float]],
    power: list[tuple[float, float]],
    start: float,
    end: float,
    step: float = STEP_S,
) -> list[Entry]:
    """Readings on the fixed grid from recorder histories, for the grid times where every input has a value.

    `rooms` is one history per room sensor. The room value is the median of the sensors that have a value.
    Power and outdoor values are taken as recorded. Grid times missing any input are skipped.
    """
    if not rooms:
        return []
    room_grids = [_resample_states(points, start, end, step) for points in rooms]
    outdoor_grid = _resample_states(outdoor, start, end, step)
    power_grid = _resample_states(power, start, end, step)
    out: list[Entry] = []
    for idx, (t, out_v) in enumerate(outdoor_grid):
        if out_v is None or power_grid[idx][1] is None:
            continue
        room_values = [grid[idx][1] for grid in room_grids if grid[idx][1] is not None]
        if not room_values:
            continue
        room_values.sort()
        mid = len(room_values) // 2
        room_c = room_values[mid] if len(room_values) % 2 else (room_values[mid - 1] + room_values[mid]) / 2
        out.append(Entry(t=t, room_c=room_c, outdoor_c=out_v, power_w=power_grid[idx][1]))
    return out


def entries_to_csv(entries: list[Entry]) -> str:
    """CSV text of the stored readings, in the layout the backfill tool reads: time, room_c, outdoor_c, power_w.

    Times are Unix seconds.
    """
    lines = ["time,room_c,outdoor_c,power_w"]
    for e in sorted(entries, key=lambda x: x.t):
        lines.append(f"{e.t:.0f},{e.room_c:.2f},{e.outdoor_c:.2f},{e.power_w:.0f}")
    return "\n".join(lines) + "\n"


def has_enough_cycles(samples: list[Sample]) -> bool:
    """True when the data has enough AC-on and AC-off time, and enough separate runs, to fit safely."""
    on = [s.power_kw > 0 for s in samples]
    if sum(on) < MIN_CYCLE_SAMPLES or len(on) - sum(on) < MIN_CYCLE_SAMPLES:
        return False
    runs = sum(1 for prev, cur in zip([False] + on, on) if cur and not prev)
    return runs >= MIN_CYCLES
