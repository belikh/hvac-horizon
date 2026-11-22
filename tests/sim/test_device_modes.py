"""Closed-loop runs of the device modes and device faults that the cooling profiles do not reach.

Each run is the real integration against the simulated house, with the AC as a real climate entity. The entity
refuses a mode it does not list and a setpoint outside its range, so a tick that sends one fails and the run shows it.

- Heat pump: the AC heats a room on a cold night, with no heater switch.
- Dry mode: the AC dries a humid room down to the humidity target and never cools.
- Fan mode: the AC runs its fan for the whole run and its compressor never starts.
- Rated power: an AC with no power sensor, read through its hvac_action.
- Own sensor: an AC whose sensor reads 2 K below the room still cools the room to the target.
- A cool-only AC never receives a mode it does not have, on a cold humid night with AC heat and dry mode enabled.
- Device faults: an AC that is unavailable, ignores commands or raises errors for an hour. Control goes on afterwards.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.sim.harness import COMFORT_C, DeviceFault, Harness
from tests.sim.house import AcSpec, Weather
from tests.sim.profiles import AC, COOL_NIGHT, HOT, MILD, NORMAL_AC, PROFILES, TIMBER, WARM
from tests.sim.harness import Profile

pytestmark = pytest.mark.sim

HUMID = Weather(mean_c=30.0, amplitude_c=4.0, noise_c=0.2, humidity_pct=70.0)  # dry mode can win: 0.3 x (70 - 57) + 1 is under 8 points per hour
COLD_HUMID = replace(COOL_NIGHT, humidity_pct=90.0)


def share(rows, test) -> float:
    return sum(1 for r in rows if test(r)) / len(rows)


def modes_seen(trace, entity=AC) -> set[str]:
    return {r.modes[entity] for r in trace.rows}


def all_ticks_ok(trace) -> bool:
    """Every tick finished and no device refused a command."""
    assert trace.command_failures == [], trace.command_failures[:3]
    return all(ok for _, ok, _ in trace.ticks)


async def test_heat_pump_holds_a_room_through_a_cold_night(hass, freezer):
    profile = Profile(
        "heat_pump", TIMBER, COOL_NIGHT, ((AC, NORMAL_AC),), start_hour=18.0, start_air_c=20.0, start_mass_c=20.0,
        mode="heat", settings=(("ac_heat", True),),
    )
    trace = await Harness(hass, freezer, profile).run(12)
    assert all_ticks_ok(trace)
    assert "heat" in modes_seen(trace) and "cool" not in modes_seen(trace)
    held = [r for r in trace.rows if r.minute >= 180]
    assert share(held, lambda r: abs(r.air_c - COMFORT_C) <= 1.5) >= 0.9, (min(r.air_c for r in held), max(r.air_c for r in held))
    starts = sum(1 for a, b in zip(trace.rows, trace.rows[1:]) if not a.compressors[AC] and b.compressors[AC])
    assert starts <= 6 * 12, starts


async def test_dry_mode_brings_a_humid_room_to_the_humidity_target_and_never_cools(hass, freezer):
    profile = Profile(
        "dry_mode", TIMBER, HUMID, ((AC, NORMAL_AC),), start_hour=9.0, start_air_c=25.0, start_mass_c=25.0, start_rh_pct=72.0,
        room_humidity=True, mode="dry",
    )
    trace = await Harness(hass, freezer, profile).run(8)
    assert all_ticks_ok(trace)
    assert "dry" in modes_seen(trace) and "cool" not in modes_seen(trace)
    assert min(r.rh_pct for r in trace.rows) <= 57.5  # 3 points below the default target of 60 %
    late = [r for r in trace.rows if r.minute >= 240]
    assert max(r.rh_pct for r in late) <= 63.0, max(r.rh_pct for r in late)  # held near the target once reached
    changes = sum(1 for a, b in zip(trace.rows, trace.rows[1:]) if a.modes[AC] != b.modes[AC])
    assert changes <= 12 * 8, changes


async def test_fan_mode_runs_the_fan_for_a_whole_hot_day_and_no_compressor(hass, freezer):
    profile = Profile("fan_mode", TIMBER, HOT, ((AC, NORMAL_AC),), heater_kw=2.0, mode="fan_only")
    trace = await Harness(hass, freezer, profile).run(12)
    assert all_ticks_ok(trace)
    assert all(r.modes[AC] == "fan_only" for r in trace.rows[1:])
    assert not any(r.compressors[AC] for r in trace.rows) and not any(r.heater_on for r in trace.rows)
    assert [c for c in trace.calls if c.service == "set_hvac_mode"] == [c for c in trace.calls if c.data.get("hvac_mode") == "fan_only"][:1]


async def test_an_ac_with_no_power_sensor_holds_the_comfort_band_from_its_action(hass, freezer):
    profile = replace(PROFILES["light_timber"], name="rated_power", power_sensor=False, rated_w=1100.0)
    trace = await Harness(hass, freezer, profile).run(24)
    assert all_ticks_ok(trace)
    held = [r for r in trace.rows if r.minute >= 180]
    assert share(held, lambda r: abs(r.air_c - COMFORT_C) <= 1.5) >= 0.9


async def test_an_ac_whose_sensor_reads_low_still_cools_the_room_to_the_target(hass, freezer):
    """The unit's sensor reads 2 K below the room, so at the plain setpoint its own thermostat stops 2 K early."""
    profile = Profile("cold_own_sensor", TIMBER, WARM, ((AC, AcSpec(cool_kw=3.5, sensor_bias_c=-2.0)),))
    trace = await Harness(hass, freezer, profile).run(24)
    assert all_ticks_ok(trace)
    held = [r for r in trace.rows if r.minute >= 180]
    assert share(held, lambda r: abs(r.air_c - COMFORT_C) <= 1.5) >= 0.9, (min(r.air_c for r in held), max(r.air_c for r in held))


async def test_a_cool_only_ac_never_receives_a_mode_it_does_not_have(hass, freezer):
    """A cold humid night with AC heat and AC dry enabled. The unit lists off and cool, and Home Assistant refuses anything else."""
    profile = Profile(
        "cool_only", TIMBER, COLD_HUMID, ((AC, NORMAL_AC),), start_hour=18.0, start_air_c=20.0, start_mass_c=20.0, start_rh_pct=75.0,
        ac_modes=("off", "cool"), room_humidity=True, settings=(("ac_heat", True), ("dry_for_humidity", True), ("mpc_heat_plan", True)),
    )
    trace = await Harness(hass, freezer, profile).run(12)
    assert all_ticks_ok(trace), [t for t in trace.ticks if not t[1]][:3]
    assert modes_seen(trace) <= {"off", "cool"}


@pytest.mark.parametrize("kind", ["unavailable", "ignores", "errors"])
async def test_an_ac_that_fails_for_an_hour_is_reported_and_control_goes_on_afterwards(hass, freezer, kind):
    """The AC fails from hour 13 to hour 14 of a hot day. Afterwards the room returns to the comfort band."""
    fault = DeviceFault(13 * 60, 14 * 60, AC, kind)
    trace = await Harness(hass, freezer, PROFILES["hot_day"], device_faults=[fault]).run(18)
    after = [r for r in trace.rows if r.minute >= 15 * 60 + 30]
    assert share(after, lambda r: abs(r.air_c - COMFORT_C) <= 1.5) >= 0.9, (min(r.air_c for r in after), max(r.air_c for r in after))
    assert all(ok for _, ok, _ in trace.ticks), "a failing device must not end a tick: the other devices and the sensors depend on it"
    during = [name for minute, names in trace.unconfirmed if 13 * 60 <= minute < 14 * 60 + 10 for name in names]
    if kind == "ignores":
        assert any(name.startswith(f"{AC}: expected") for name in during), trace.unconfirmed[:3]
    if kind == "errors":
        assert any("failed: " in name and name.startswith(AC) for name in during), trace.unconfirmed[:3]
        assert trace.command_failures
    else:
        assert trace.command_failures == []
