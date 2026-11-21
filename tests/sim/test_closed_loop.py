"""Closed-loop properties: the real integration controls a simulated room for 24 hours.

Every profile runs once with control on and sensors healthy, and once per fault mode with the room sensors
failing from hour 12 to hour 13. A trace is recorded once per run and shared by the property tests.

Fixed settings: minimum on time 10 min, minimum off time 5 min, tick 5 min, stall window 900 s (the default),
cool threshold 24.0 C (the default comfort setpoint), heater PWM window 10 min.

Bounds:
- Minimum times. An AC changes state only after it held the previous state for the minimum time.
- AC mode writes. At most one on and one off per 15 min cycle, so 2 * ceil(60 / (min_on + min_off)) = 8 set_hvac_mode
  calls per clock hour for each AC.
- Heater switch writes. The PWM window is 10 min with an on edge and an off edge, so 12 turn_on or turn_off calls per clock hour.
- Comfort. After hour 3 the room air stays within 1.5 K of the cool threshold for at least 90 percent of the minutes.
- Stop. With every room sensor stuck or unavailable from the first fault minute, each device is off from
  stall window (15 min) + one tick (5 min) on, and stays off until the sensors return.
"""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from tests.sim.harness import COMFORT_C, Harness, Trace
from tests.sim.house import SensorFault
from tests.sim.profiles import PROFILES

pytestmark = pytest.mark.sim

NAMES = list(PROFILES)
FAULT_START_MIN = 12 * 60
FAULT_END_MIN = 13 * 60
FAULT_RUN_HOURS = 14
STALL_WINDOW_MIN = 15
TICK_MIN = 5
COMFORT_BAND_K = 1.5
COMFORT_SHARE = 0.9
WARMUP_MIN = 3 * 60
PEAK_HOURS = (13.0, 17.0)  # sim clock hours of the outdoor peak, 15:00 plus and minus two hours

_TRACES: dict[tuple[str, str | None], Trace] = {}


async def traced(hass, freezer, name: str, fault: str | None = None) -> Trace:
    """The trace of one profile, simulated on first use. `fault` is None, "stuck" or "dropout"."""
    key = (name, fault)
    if key not in _TRACES:
        faults = [SensorFault(FAULT_START_MIN, FAULT_END_MIN, fault)] if fault else []
        hours = FAULT_RUN_HOURS if fault else 24
        _TRACES[key] = await Harness(hass, freezer, PROFILES[name], faults).run(hours)
    return _TRACES[key]


def ac_changes(trace: Trace, entity: str) -> list[tuple[int, bool]]:
    """(minute, new state) for each change of the AC's mode between off and cool."""
    out = []
    previous = trace.rows[0].modes[entity] == "cool"
    for row in trace.rows[1:]:
        now_on = row.modes[entity] == "cool"
        if now_on != previous:
            out.append((row.minute, now_on))
            previous = now_on
    return out


def heater_changes(trace: Trace) -> list[int]:
    out = []
    previous = trace.rows[0].heater_on
    for row in trace.rows[1:]:
        if row.heater_on != previous:
            out.append(row.minute)
            previous = row.heater_on
    return out


def any_on(row) -> bool:
    return row.heater_on or any(mode == "cool" for mode in row.modes.values())


@pytest.mark.parametrize("name", NAMES)
async def test_no_tick_raises(hass, freezer, name):
    trace = await traced(hass, freezer, name)
    assert len(trace.ticks) == 24 * 12 + 1
    assert trace.updates == len(trace.ticks)  # one coordinator update per tick
    failed = [t for t in trace.ticks if not t[1]]
    assert failed == []
    assert trace.cool_above_c == COMFORT_C


@pytest.mark.parametrize("name", NAMES)
async def test_no_ac_short_cycling(hass, freezer, name):
    trace = await traced(hass, freezer, name)
    profile = trace.profile
    for entity, _spec in profile.acs:
        changes = ac_changes(trace, entity)
        previous_minute = None
        for minute, now_on in changes:
            if previous_minute is not None:
                held = minute - previous_minute
                minimum = profile.min_off_min if now_on else profile.min_on_min  # a change to on ends an off period
                assert held >= minimum, f"{entity} changed to {'on' if now_on else 'off'} at minute {minute} after {held} min"
            previous_minute = minute


@pytest.mark.parametrize("name", NAMES)
async def test_ac_mode_writes_per_hour_are_bounded(hass, freezer, name):
    trace = await traced(hass, freezer, name)
    profile = trace.profile
    bound = 2 * math.ceil(60 / (profile.min_on_min + profile.min_off_min))
    assert bound == 8
    for entity, _spec in profile.acs:
        per_hour: dict[int, int] = {}
        for call in trace.calls:
            if call.service == "set_hvac_mode" and call.entity_id == entity:
                per_hour[call.minute // 60] = per_hour.get(call.minute // 60, 0) + 1
        assert max(per_hour.values(), default=0) <= bound, per_hour


@pytest.mark.parametrize("name", NAMES)
async def test_heater_switch_changes_per_hour_are_bounded(hass, freezer, name):
    trace = await traced(hass, freezer, name)
    per_hour: dict[int, int] = {}
    for minute in heater_changes(trace):
        per_hour[minute // 60] = per_hour.get(minute // 60, 0) + 1
    writes: dict[int, int] = {}
    for call in trace.calls:
        if call.domain == "switch":
            writes[call.minute // 60] = writes.get(call.minute // 60, 0) + 1
    assert max(per_hour.values(), default=0) <= 12, per_hour
    assert max(writes.values(), default=0) <= 12, writes


@pytest.mark.parametrize("name", NAMES)
async def test_heater_and_ac_never_run_together(hass, freezer, name):
    trace = await traced(hass, freezer, name)
    both = [r.minute for r in trace.rows if r.heater_on and any(m == "cool" for m in r.modes.values())]
    assert both == []


COMFORT_NAMES = [
    pytest.param(
        n,
        marks=pytest.mark.xfail(
            strict=True,
            reason="An 8 kW unit moves the 3e5 J/K air node 1.6 K/min. The compressor minimum run of 3 min and the 10 min minimum on "
            "time carry the room to 19.8 C, 3.7 K below the 23.5 C stop point, each cycle. The controller has no way to modulate the unit.",
        ),
    )
    if n == "grossly_oversized_ac"
    else n
    for n in NAMES
    if n != "undersized_ac"
]


@pytest.mark.parametrize("name", COMFORT_NAMES)
async def test_room_stays_within_the_comfort_band(hass, freezer, name):
    trace = await traced(hass, freezer, name)
    rows = [r for r in trace.rows if r.minute >= WARMUP_MIN]
    inside = sum(1 for r in rows if abs(r.air_c - COMFORT_C) <= COMFORT_BAND_K)
    assert inside / len(rows) >= COMFORT_SHARE, f"{inside / len(rows):.3f} of minutes inside the band"


async def test_undersized_ac_runs_continuously_at_the_peak(hass, freezer):
    trace = await traced(hass, freezer, "undersized_ac")
    peak = [r for r in trace.rows if PEAK_HOURS[0] <= r.hour < PEAK_HOURS[1]]
    entity = trace.profile.acs[0][0]
    assert all(r.modes[entity] == "cool" for r in peak)
    assert sum(r.compressors[entity] for r in peak) / len(peak) >= 0.95
    assert min(r.air_c for r in peak) > COMFORT_C + 0.5  # the room is out of reach, which is why it runs on


async def test_fleet_large_unit_runs_less_than_the_small_unit_over_the_mild_hours(hass, freezer):
    trace = await traced(hass, freezer, "two_ac_fleet")
    small, large = (entity for entity, _spec in trace.profile.acs)
    mild = [r for r in trace.rows if r.outdoor_c < 28.0]
    small_mild = sum(r.modes[small] == "cool" for r in mild)
    large_mild = sum(r.modes[large] == "cool" for r in mild)
    assert large_mild < small_mild, f"mild minutes on: small {small_mild}, large {large_mild}"


@pytest.mark.xfail(
    strict=True,
    reason="The fitted rate of the small unit falls from 19 to 0.4 K/h per kW over the day (the fit is one node, the room has two). "
    "Economy dispatch then covers every gap with the large unit and the small unit never runs at the peak.",
)
async def test_fleet_both_units_run_at_the_peak(hass, freezer):
    trace = await traced(hass, freezer, "two_ac_fleet")
    peak = [r for r in trace.rows if PEAK_HOURS[0] <= r.hour < PEAK_HOURS[1]]
    for entity, _spec in trace.profile.acs:
        assert sum(r.modes[entity] == "cool" for r in peak) / len(peak) >= 0.5, entity


@pytest.mark.parametrize("fault", ["stuck", "dropout"])
@pytest.mark.parametrize("name", NAMES)
async def test_failed_room_sensors_turn_every_device_off_and_keep_them_off(hass, freezer, name, fault):
    trace = await traced(hass, freezer, name, fault)
    assert all(t[1] for t in trace.ticks)
    before = [r for r in trace.rows if FAULT_START_MIN - 60 <= r.minute < FAULT_START_MIN]
    assert any(any_on(r) for r in before), "no device ran before the fault, so the test would pass without a stop"
    deadline = FAULT_START_MIN + STALL_WINDOW_MIN + TICK_MIN
    held_off = [r for r in trace.rows if deadline <= r.minute <= FAULT_END_MIN]
    still_on = [(r.minute, r.modes, r.heater_on) for r in held_off if any_on(r)]
    assert still_on == [], still_on[:3]


async def test_stall_stop_does_not_wait_for_the_minimum_on_time(hass, freezer):
    """A 30 min minimum on time must not keep the AC running past the stall window plus one tick.

    A first run without a fault finds a start of the AC after hour 10. The second run is the same house with the
    room sensors stuck from the tick after that start, so the stall is found while the AC is inside its minimum on time.
    """
    profile = replace(PROFILES["hot_day"], name="hot_day_long_minimum_times", min_on_min=30.0, min_off_min=10.0)
    entity = profile.acs[0][0]
    first = await Harness(hass, freezer, profile).run(14)
    start = next(minute for minute, now_on in ac_changes(first, entity) if now_on and minute >= 10 * 60)
    fault_start = start + TICK_MIN
    fault_end = fault_start + 60
    trace = await Harness(hass, freezer, profile, [SensorFault(fault_start, fault_end, "stuck")]).run(14)
    deadline = fault_start + STALL_WINDOW_MIN + TICK_MIN
    on = [r.minute for r in trace.rows if deadline <= r.minute <= fault_end and r.modes[entity] == "cool"]
    assert on == [], f"AC started at minute {start}, fault from {fault_start}, still on at minutes {on[:3]}"
