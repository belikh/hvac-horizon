"""The basics of a room thermostat, tested from the outside.

Each test uses the room thermostat as a person does: it sets a mode and a target, lets time pass and looks at the heater
switch, the AC and the thermostat's own state. No test reads the coordinator or its data. The house is simulated: the
heater and the AC answer service calls as real devices do, and the room warms and cools from them and from outdoors.

A failure here means the integration breaks a rule every thermostat user expects, whatever the internal tests say.
"""

from __future__ import annotations

import random
from datetime import timedelta

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.hvac_horizon.const import DOMAIN

AC = "climate.basics_ac"
AC_POWER = "sensor.basics_ac_power"
ROOM_1 = "sensor.basics_room_1"
ROOM_2 = "sensor.basics_room_2"
OUTDOOR = "sensor.basics_outdoor"
HEATER = "switch.basics_heater"
WINDOW_ACTUATOR = "switch.basics_window_actuator"

TARGET_C = 22.0
COLD_ROOM_C = 19.0  # 3 K below the target
HOT_ROOM_C = 25.0  # 3 K above the target
OUTDOORS_C = (5.0, 15.0, 21.0, 25.0, 35.0)  # a frosty day to a hot day
HEATER_K_PER_H = 5.0
AC_K_PER_H = 5.0
WINDOW_LOSS_PER_H = 1.0  # the same share with the window open
LOSS_PER_H = 0.15  # share of the indoor to outdoor gap the room loses per hour
TEMP = {"unit_of_measurement": "°C", "device_class": "temperature"}


class House:
    """One room with a heater switch and an AC, driven one minute at a time."""

    def __init__(self, hass, freezer, *, room_c: float, outdoor_c: float, thermal: bool = False) -> None:
        self.hass = hass
        self.freezer = freezer
        self.room_c = room_c
        self.outdoor_c = outdoor_c
        self.thermal = thermal  # False holds the room at room_c, True lets the devices and outdoors move it
        self.sensors_alive = True
        self.gain_k_per_h = 0.0  # heat from the sun and from people
        self.noise_k = 0.0  # standard deviation of the error each room sensor adds to its reading
        self.rng = random.Random(0)
        self.window_opened = False  # True once the window actuator has been open
        self.thermostat = ""
        self.unconfirmed = ""  # the Unconfirmed commands sensor
        self.entry: MockConfigEntry | None = None
        self.log: list[tuple[float, bool, bool]] = []  # per minute: room temperature, heater on, AC cooling

    @property
    def heater_on(self) -> bool:
        return self.hass.states.get(HEATER).state == "on"

    @property
    def ac_mode(self) -> str:
        return self.hass.states.get(AC).state

    @property
    def window_open(self) -> bool:
        return self.hass.states.get(WINDOW_ACTUATOR).state == "on"

    @property
    def state(self):
        return self.hass.states.get(self.thermostat)

    def _publish(self) -> None:
        for sensor in (ROOM_1, ROOM_2):
            room = f"{self.room_c + self.rng.gauss(0.0, self.noise_k):.2f}" if self.sensors_alive else "unavailable"
            self.hass.states.async_set(sensor, room, TEMP)
        self.hass.states.async_set(OUTDOOR, f"{self.outdoor_c:.2f}", TEMP)
        watts = "1000" if self.ac_mode in ("cool", "dry", "heat") else "0"
        self.hass.states.async_set(AC_POWER, watts, {"unit_of_measurement": "W", "device_class": "power"})

    def _register_devices(self) -> None:
        hass = self.hass
        services = hass.services.async_services_internal()["climate"]
        real = {name: services[name] for name in ("set_hvac_mode", "set_temperature")}

        def target_of(call) -> str:
            target = call.data["entity_id"]
            return target[0] if isinstance(target, list) else target

        def climate_handler(name):
            async def handler(call):
                target = target_of(call)
                if target != AC:
                    return await hass.async_run_hass_job(real[name].job, call)  # the room thermostat itself
                state = hass.states.get(AC)
                now = dt_util.utcnow().timestamp()
                if name == "set_hvac_mode":
                    hass.states.async_set(AC, call.data["hvac_mode"], dict(state.attributes), timestamp=now)
                else:
                    hass.states.async_set(AC, state.state, {**state.attributes, "temperature": call.data["temperature"]}, timestamp=now)

            return handler

        def switch_handler(new_state):
            async def handler(call):
                hass.states.async_set(target_of(call), new_state, timestamp=dt_util.utcnow().timestamp())

            return handler

        for name in real:
            hass.services.async_register("climate", name, climate_handler(name), schema=services[name].schema)
        hass.services.async_register("switch", "turn_on", switch_handler("on"))
        hass.services.async_register("switch", "turn_off", switch_handler("off"))

    async def start(self, **settings) -> None:
        """Install the entry with control on and a heater switch, as a user does, and find the room thermostat."""
        self.hass.states.async_set(AC, "off", {"temperature": 24.0, "friendly_name": "Basics AC", "target_temp_step": 1.0, "hvac_modes": ["off", "cool", "dry", "fan_only"]})
        self.hass.states.async_set(HEATER, "off")
        self.hass.states.async_set(WINDOW_ACTUATOR, "off")
        self._publish()
        self._register_devices()
        data = {"climate": AC, "power": AC_POWER, "room_sensors": [ROOM_1, ROOM_2], "outdoor": OUTDOOR, "heater_switch": HEATER, "control": True, **settings}
        self.entry = MockConfigEntry(domain=DOMAIN, title="Basics AC", data=data, unique_id=AC)
        self.entry.add_to_hass(self.hass)
        assert await self.hass.config_entries.async_setup(self.entry.entry_id)
        await self.hass.async_block_till_done()
        registry = er.async_get(self.hass)
        self.thermostat = next(e.entity_id for e in er.async_entries_for_config_entry(registry, self.entry.entry_id) if e.domain == "climate")
        self.unconfirmed = next(e.entity_id for e in er.async_entries_for_config_entry(registry, self.entry.entry_id) if e.unique_id.endswith("_commands_unconfirmed"))

    async def restart(self) -> None:
        """Unload and set up the entry again, as a restart of Home Assistant does."""
        assert await self.hass.config_entries.async_unload(self.entry.entry_id)
        await self.hass.async_block_till_done()
        assert await self.hass.config_entries.async_setup(self.entry.entry_id)
        await self.hass.async_block_till_done()

    async def run(self, minutes: int) -> None:
        """Let `minutes` pass. The heater and the AC never run against each other at any minute."""
        for _ in range(minutes):
            if self.thermal:
                rate = (self.outdoor_c - self.room_c) * (WINDOW_LOSS_PER_H if self.window_open else LOSS_PER_H)
                rate += self.gain_k_per_h + (HEATER_K_PER_H if self.heater_on else 0.0)
                rate -= AC_K_PER_H if self.ac_mode == "cool" else 0.0
                self.room_c += rate / 60.0
            self.freezer.tick(timedelta(minutes=1))
            self._publish()
            async_fire_time_changed(self.hass)
            await self.hass.async_block_till_done()
            self.log.append((self.room_c, self.heater_on, self.ac_mode == "cool"))
            self.window_opened = self.window_opened or self.window_open
            assert not (self.heater_on and self.ac_mode == "cool"), "the heater and the AC ran against each other"
            if self.sensors_alive and self.unconfirmed:
                assert self.hass.states.get(self.unconfirmed).state == "[]", "a device that obeys every command was reported as unconfirmed"

    async def set_mode(self, mode: str) -> None:
        await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": self.thermostat, "hvac_mode": mode}, blocking=True)
        await self.hass.async_block_till_done()

    async def set_target(self, target_c: float) -> None:
        await self.hass.services.async_call("climate", "set_temperature", {"entity_id": self.thermostat, "temperature": target_c}, blocking=True)
        await self.hass.async_block_till_done()

    def heated(self, last_minutes: int) -> bool:
        return any(heater for _, heater, _ in self.log[-last_minutes:])

    def cooled(self, last_minutes: int) -> bool:
        return any(cooling for _, _, cooling in self.log[-last_minutes:])

    def rooms(self, last_minutes: int) -> list[float]:
        return [room for room, _, _ in self.log[-last_minutes:]]


@pytest.fixture
async def house(hass, enable_custom_integrations, freezer):
    freezer.move_to("2026-06-15T02:00:00+00:00")

    async def build(*, room_c: float, outdoor_c: float, mode: str, target_c: float = TARGET_C, thermal: bool = False, **settings) -> House:
        made = House(hass, freezer, room_c=room_c, outdoor_c=outdoor_c, thermal=thermal)
        await made.start(**settings)
        await made.set_target(target_c)
        await made.set_mode(mode)
        return made

    return build


def _mode_rows():
    """mode, room, outdoors, heater runs, AC cools.

    Heat and cool obey the target at every outdoor temperature. Auto has one outdoor limit in a room with a heater and
    an AC: it does not heat when outdoors is above 22 C.
    """
    rows = []
    for outdoor_c in OUTDOORS_C:
        rows += [
            ("heat", COLD_ROOM_C, outdoor_c, True, False),
            ("heat", HOT_ROOM_C, outdoor_c, False, False),
            ("cool", HOT_ROOM_C, outdoor_c, False, True),
            ("cool", COLD_ROOM_C, outdoor_c, False, False),
            ("auto", COLD_ROOM_C, outdoor_c, outdoor_c <= 22.0, False),
            ("auto", HOT_ROOM_C, outdoor_c, False, True),
            ("off", COLD_ROOM_C, outdoor_c, False, False),
            ("off", HOT_ROOM_C, outdoor_c, False, False),
        ]
    return rows


@pytest.mark.parametrize(("mode", "room_c", "outdoor_c", "heats", "cools"), _mode_rows())
async def test_the_mode_and_the_target_decide_what_runs(house, mode, room_c, outdoor_c, heats, cools):
    """Heat warms a room below the target. Cool cools a room above it. Neither does the other's job. Off runs nothing."""
    home = await house(room_c=room_c, outdoor_c=outdoor_c, mode=mode)
    await home.run(30)
    assert home.heated(30) is heats
    assert home.cooled(30) is cools


async def test_auto_cools_with_the_window_on_a_cold_day_and_leaves_the_ac_off(house):
    """A room with a window actuator, 10 C outdoors: auto opens the window, the room cools and the AC stays off."""
    home = await house(room_c=HOT_ROOM_C, outdoor_c=10.0, mode="auto", thermal=True, window_actuator=WINDOW_ACTUATOR)
    await home.run(180)
    assert home.window_opened
    assert not home.cooled(180)
    assert max(home.rooms(60)) <= TARGET_C + 1.0


async def test_auto_runs_the_ac_when_the_open_window_does_not_cool_the_room(house):
    """The window opens and the room stays 3 K above the target. Auto closes the window and the AC cools, at 10 C outdoors too."""
    home = await house(room_c=HOT_ROOM_C, outdoor_c=10.0, mode="auto", window_actuator=WINDOW_ACTUATOR)
    await home.run(60)
    assert home.window_opened
    assert home.ac_mode == "cool"
    assert not home.window_open


async def test_auto_runs_the_ac_when_the_open_window_cools_the_room_too_little(house):
    """A sunny room, 20 C outdoors. The open window holds the room 0.8 K above the target and no lower, so the AC takes over."""
    home = await house(room_c=23.5, outdoor_c=20.0, mode="auto", thermal=True, window_actuator=WINDOW_ACTUATOR)
    home.gain_k_per_h = 2.8
    await home.run(240)
    assert home.window_opened
    assert home.cooled(240)
    assert max(home.rooms(60)) <= TARGET_C + 1.0


@pytest.mark.parametrize(
    ("mode", "start_c", "outdoor_c"),
    [("heat", 17.0, 5.0), ("heat", 17.0, 18.0), ("cool", 28.0, 33.0), ("cool", 28.0, 24.0), ("auto", 17.0, 5.0), ("auto", 28.0, 33.0)],
)
async def test_the_room_reaches_the_target_and_stays_there(house, mode, start_c, outdoor_c):
    """After 4 hours the room is within 1 K of the target, and it stays within 1 K for the next 2 hours."""
    home = await house(room_c=start_c, outdoor_c=outdoor_c, mode=mode, thermal=True)
    await home.run(360)
    held = home.rooms(120)
    assert TARGET_C - 1.0 <= min(held) and max(held) <= TARGET_C + 1.0, (min(held), max(held))


@pytest.mark.parametrize(("start_c", "outdoor_c", "idle"), [(17.0, 5.0, "ac"), (17.0, 14.0, "ac"), (28.0, 33.0, "heater"), (28.0, 26.0, "heater")])
async def test_auto_does_not_take_turns_between_the_heater_and_the_ac(house, start_c, outdoor_c, idle):
    """On a cold day auto heats and the AC stays off for the whole day. On a hot day it cools and the heater stays off."""
    home = await house(room_c=start_c, outdoor_c=outdoor_c, mode="auto", thermal=True)
    await home.run(480)
    if idle == "ac":
        assert home.heated(480) and not home.cooled(480)
    else:
        assert home.cooled(480) and not home.heated(480)


async def test_raising_the_target_starts_the_heater_and_lowering_it_stops_the_heater(house):
    home = await house(room_c=21.0, outdoor_c=10.0, mode="heat", target_c=20.0)
    await home.run(20)
    assert not home.heated(20)
    await home.set_target(24.0)
    await home.run(10)
    assert home.heater_on
    await home.set_target(19.0)
    await home.run(2)
    assert not home.heater_on


async def test_lowering_the_target_starts_the_ac_and_raising_it_stops_the_ac(house):
    home = await house(room_c=24.0, outdoor_c=30.0, mode="cool", target_c=26.0)
    await home.run(20)
    assert not home.cooled(20)
    await home.set_target(21.0)
    await home.run(10)
    assert home.ac_mode == "cool"
    await home.set_target(27.0)
    await home.run(15)
    assert home.ac_mode == "off"


@pytest.mark.parametrize("mode", ["heat", "cool", "auto"])
async def test_the_thermostat_shows_the_target_you_set(house, mode):
    home = await house(room_c=22.0, outdoor_c=20.0, mode=mode, target_c=23.5)
    await home.run(10)
    assert home.state.state == mode
    assert home.state.attributes["temperature"] == 23.5
    assert home.state.attributes["current_temperature"] == 22.0


async def test_off_stops_a_running_heater_at_once_and_nothing_starts_again(house):
    home = await house(room_c=COLD_ROOM_C, outdoor_c=5.0, mode="heat")
    await home.run(15)
    assert home.heater_on
    await home.set_mode("off")
    assert not home.heater_on
    await home.run(60)
    assert not home.heated(60) and not home.cooled(60)
    assert home.state.state == "off"


async def test_off_stops_a_running_ac_at_once_and_nothing_starts_again(house):
    home = await house(room_c=HOT_ROOM_C, outdoor_c=32.0, mode="cool")
    await home.run(15)
    assert home.ac_mode == "cool"
    await home.set_mode("off")
    assert home.ac_mode == "off"
    await home.run(60)
    assert not home.heated(60) and not home.cooled(60)


async def test_changing_from_cool_to_heat_stops_the_ac_at_once(house):
    home = await house(room_c=HOT_ROOM_C, outdoor_c=32.0, mode="cool")
    await home.run(15)
    assert home.ac_mode == "cool"
    await home.set_mode("heat")
    assert home.ac_mode == "off"
    await home.run(30)
    assert not home.cooled(30) and not home.heated(30)


async def test_changing_from_heat_to_cool_stops_the_heater_at_once(house):
    home = await house(room_c=COLD_ROOM_C, outdoor_c=5.0, mode="heat")
    await home.run(15)
    assert home.heater_on
    await home.set_mode("cool")
    assert not home.heater_on
    await home.run(30)
    assert not home.cooled(30) and not home.heated(30)


async def test_turn_on_returns_to_the_mode_before_off(house):
    home = await house(room_c=COLD_ROOM_C, outdoor_c=5.0, mode="heat")
    await home.set_mode("off")
    await home.hass.services.async_call("climate", "turn_on", {"entity_id": home.thermostat}, blocking=True)
    await home.run(15)
    assert home.state.state == "heat"
    assert home.heater_on


@pytest.mark.parametrize(
    ("mode", "room_c", "outdoor_c", "action"),
    [("heat", COLD_ROOM_C, 5.0, "heating"), ("heat", HOT_ROOM_C, 5.0, "idle"), ("cool", HOT_ROOM_C, 32.0, "cooling"), ("cool", COLD_ROOM_C, 32.0, "idle"), ("off", COLD_ROOM_C, 5.0, "off")],
)
async def test_the_thermostat_reports_what_the_devices_do(house, mode, room_c, outdoor_c, action):
    home = await house(room_c=room_c, outdoor_c=outdoor_c, mode=mode)
    await home.run(15)
    assert home.state.attributes["hvac_action"] == action


@pytest.mark.parametrize(("mode", "room_c", "outdoor_c"), [("heat", COLD_ROOM_C, 5.0), ("cool", HOT_ROOM_C, 32.0), ("off", COLD_ROOM_C, 5.0)])
async def test_the_mode_and_the_target_survive_a_restart(house, mode, room_c, outdoor_c):
    home = await house(room_c=room_c, outdoor_c=outdoor_c, mode=mode, target_c=22.5)
    await home.run(15)
    await home.restart()
    await home.run(15)
    assert home.state.state == mode
    assert home.state.attributes["temperature"] == 22.5
    assert home.heater_on is (mode == "heat")
    assert (home.ac_mode == "cool") is (mode == "cool")


@pytest.mark.parametrize(("mode", "room_c", "outdoor_c"), [("heat", COLD_ROOM_C, 5.0), ("cool", HOT_ROOM_C, 32.0)])
async def test_dead_room_sensors_turn_the_devices_off(house, mode, room_c, outdoor_c):
    """With no room temperature the thermostat cannot know when to end a run, so it ends the run."""
    home = await house(room_c=room_c, outdoor_c=outdoor_c, mode=mode)
    await home.run(15)
    assert home.heater_on or home.ac_mode == "cool"
    home.sensors_alive = False
    await home.run(30)
    assert not home.heater_on
    assert home.ac_mode == "off"



def _starts(flags: list[bool]) -> int:
    return sum(1 for before, after in zip(flags, flags[1:]) if after and not before)


@pytest.mark.parametrize("seed", range(12))
async def test_a_random_day_with_noisy_sensors_a_restart_and_a_new_target(house, seed):
    """Six hours from a random start: noisy sensors, one restart and one target change at random minutes.

    The rules hold for every seed: heat does not cool, cool does not heat, off runs nothing, the devices do not
    short-cycle, and the room ends within 1.5 K of the target on the side the mode controls.
    """
    rng = random.Random(seed)
    mode = rng.choice(["heat", "cool", "auto", "off"])
    start_c, outdoor_c = rng.uniform(16.0, 29.0), rng.uniform(0.0, 38.0)
    targets = [float(rng.randint(19, 25)), float(rng.randint(19, 25))]
    home = await house(room_c=start_c, outdoor_c=outdoor_c, mode=mode, target_c=targets[0], thermal=True)
    home.noise_k, home.rng = 0.15, rng
    restart_at, retarget_at = sorted(rng.sample(range(10, 240), 2))
    await home.run(restart_at)
    await home.restart()
    await home.run(retarget_at - restart_at)
    await home.set_target(targets[1])
    await home.run(360 - retarget_at)
    case = f"seed {seed}: {mode}, room {start_c:.1f}, outdoors {outdoor_c:.1f}, targets {targets}, restart at {restart_at}, target at {retarget_at}"
    assert home.state.state == mode, case
    if mode in ("cool", "off"):
        assert not home.heated(360), case
    if mode in ("heat", "off"):
        assert not home.cooled(360), case
    heater, cooling = [h for _, h, _ in home.log], [c for _, _, c in home.log]
    assert _starts(heater) <= 36, f"{case}: the heater started {_starts(heater)} times in 6 hours"
    assert _starts(cooling) <= 18, f"{case}: the AC started {_starts(cooling)} times in 6 hours"
    last_hour = home.rooms(60)
    if mode in ("heat", "auto"):
        heats = mode == "heat" or outdoor_c <= 22.0
        if heats:
            assert min(last_hour) >= targets[1] - 1.5, case
    if mode in ("cool", "auto"):
        assert max(last_hour) <= targets[1] + 1.5, case
