"""The thermostat contract against real climate and switch entities.

The devices are from tests/fakes.py, so Home Assistant refuses a mode the AC does not list. Each test reads only what
a user sees: the thermostat's state and attributes and the commands the devices received.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.hvac_horizon.const import DOMAIN
from tests.fakes import ALL_MODES, FakeAc, FakeSwitch, add_devices

AC = "climate.contract_ac"
HEATER = "switch.contract_heater"
ROOM = "sensor.contract_room"
OUTDOOR = "sensor.contract_outdoor"
POWER = "sensor.contract_power"
TEMP = {"unit_of_measurement": "°C", "device_class": "temperature"}


class Room:
    def __init__(self, hass, freezer) -> None:
        self.hass, self.freezer = hass, freezer
        self.ac: FakeAc | None = None
        self.heater: FakeSwitch | None = None
        self.thermostat = ""
        self.entry: MockConfigEntry | None = None

    def weather(self, room_c: float, outdoor_c: float) -> None:
        self.hass.states.async_set(ROOM, str(room_c), TEMP, force_update=True)
        self.hass.states.async_set(OUTDOOR, str(outdoor_c), TEMP, force_update=True)
        running = self.ac is not None and self.ac.mode in ("cool", "heat", "dry")
        self.hass.states.async_set(POWER, "900" if running else "0", {"unit_of_measurement": "W", "device_class": "power"})

    async def start(self, *, room_c: float, outdoor_c: float, ac_modes: tuple[str, ...] | None = ALL_MODES, heater: bool = True, control: bool = True, **settings) -> None:
        self.freezer.move_to("2026-11-20T10:00:00+00:00")
        devices = []
        data = {"room_sensors": [ROOM], "outdoor": OUTDOOR, "control": control, "min_off_min": 0, **settings}
        if ac_modes is not None:
            self.ac = FakeAc(AC, modes=ac_modes)
            devices.append(self.ac)
            data.update({"climate": AC, "power": POWER})
        if heater:
            self.heater = FakeSwitch(HEATER)
            devices.append(self.heater)
            data["heater_switch"] = HEATER
        await add_devices(self.hass, *devices)
        self.weather(room_c, outdoor_c)
        self.entry = MockConfigEntry(domain=DOMAIN, title="Contract room", data=data, unique_id=AC)
        self.entry.add_to_hass(self.hass)
        assert await self.hass.config_entries.async_setup(self.entry.entry_id)
        await self.hass.async_block_till_done()
        registry = er.async_get(self.hass)
        self.thermostat = next(e.entity_id for e in er.async_entries_for_config_entry(registry, self.entry.entry_id) if e.domain == "climate")

    async def run(self, minutes: int, room_c: float, outdoor_c: float) -> None:
        for _ in range(minutes):
            self.freezer.tick(timedelta(minutes=1))
            self.weather(room_c, outdoor_c)
            async_fire_time_changed(self.hass)
            await self.hass.async_block_till_done()

    async def set_mode(self, mode: str) -> None:
        await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": self.thermostat, "hvac_mode": mode}, blocking=True)
        await self.hass.async_block_till_done()

    @property
    def state(self):
        return self.hass.states.get(self.thermostat)


@pytest.fixture
def room(hass, enable_custom_integrations, freezer) -> Room:
    return Room(hass, freezer)


@pytest.mark.parametrize(
    ("ac_modes", "heater", "settings", "offered"),
    [
        (ALL_MODES, False, {}, ["off", "cool", "auto", "dry", "fan_only"]),
        (ALL_MODES, True, {}, ["off", "cool", "heat", "auto", "dry", "fan_only"]),
        (ALL_MODES, False, {"ac_heat": True}, ["off", "cool", "heat", "auto", "dry", "fan_only"]),
        (("off", "cool"), False, {"ac_heat": True, "mpc_heat_plan": True}, ["off", "cool", "auto"]),  # a cool-only AC cannot heat
        (("off", "cool"), True, {}, ["off", "cool", "heat", "auto"]),
        (("off", "cool", "dry"), False, {}, ["off", "cool", "auto", "dry"]),
        (("off", "cool"), False, {"dehumidifier": "switch.contract_dehumidifier"}, ["off", "cool", "auto", "dry"]),
        (None, True, {}, ["off", "heat"]),  # a room with heating only
    ],
)
async def test_the_thermostat_offers_only_the_modes_the_devices_have(room, ac_modes, heater, settings, offered):
    await room.start(room_c=23.0, outdoor_c=20.0, ac_modes=ac_modes, heater=heater, **settings)
    assert room.state.attributes["hvac_modes"] == offered


@pytest.mark.parametrize(("room_c", "outdoor_c"), [(29.0, 34.0), (16.0, 2.0)])
async def test_observe_only_sends_no_command_to_any_device(room, room_c, outdoor_c):
    await room.start(room_c=room_c, outdoor_c=outdoor_c, control=False, ac_heat=True)
    await room.run(30, room_c, outdoor_c)
    assert room.ac.calls == [] and room.heater.calls == []
    assert room.state.state == "off" and room.state.attributes["control"] == "observe only"


@pytest.mark.parametrize(("room_c", "outdoor_c"), [(29.0, 34.0), (16.0, 2.0)])
async def test_fan_mode_starts_no_compressor_and_no_heater(room, room_c, outdoor_c):
    await room.start(room_c=room_c, outdoor_c=outdoor_c)
    await room.set_mode("fan_only")
    room.ac.calls.clear()
    room.heater.calls.clear()
    await room.run(30, room_c, outdoor_c)
    assert room.ac.mode == "fan_only"
    assert not [c for c in room.ac.calls if c[0] == "set_hvac_mode"]
    assert ("turn_on", None) not in room.heater.calls
    assert room.state.attributes["hvac_action"] == "fan"


async def test_dry_mode_never_cools_a_hot_room(room):
    await room.start(room_c=29.0, outdoor_c=34.0)
    room.ac.calls.clear()
    await room.set_mode("dry")
    await room.run(30, 29.0, 34.0)
    assert room.ac.mode == "dry"
    assert ("set_hvac_mode", "cool") not in room.ac.calls and ("set_hvac_mode", "heat") not in room.ac.calls
    assert room.state.attributes["hvac_action"] == "drying"


async def test_off_holds_through_ticks_and_a_restart_and_turn_on_returns_to_the_mode(room):
    await room.start(room_c=29.0, outdoor_c=34.0)
    await room.set_mode("cool")
    await room.run(6, 29.0, 34.0)
    assert room.ac.mode == "cool"
    await room.set_mode("off")
    assert room.ac.mode == "off"
    await room.run(20, 29.0, 34.0)
    assert await room.hass.config_entries.async_unload(room.entry.entry_id)
    assert await room.hass.config_entries.async_setup(room.entry.entry_id)
    await room.hass.async_block_till_done()
    await room.run(20, 29.0, 34.0)
    assert room.ac.mode == "off" and room.state.state == "off"
    await room.hass.services.async_call("climate", "turn_on", {"entity_id": room.thermostat}, blocking=True)
    await room.run(6, 29.0, 34.0)
    assert room.state.state == "cool" and room.ac.mode == "cool"


async def test_the_setpoint_sent_to_the_ac_stays_inside_the_range_the_ac_accepts(room):
    """The room target is 31 C and the AC accepts 16 to 30. The AC gets 30, and Home Assistant refuses nothing."""
    await room.start(room_c=33.0, outdoor_c=36.0, comfort_c=30.0)
    await room.set_mode("cool")
    await room.hass.services.async_call("climate", "set_temperature", {"entity_id": room.thermostat, "temperature": 31.0}, blocking=True)
    await room.run(10, 33.0, 36.0)
    setpoints = [value for service, value in room.ac.calls if service == "set_temperature"]
    assert setpoints and all(16.0 <= value <= 30.0 for value in setpoints)
    assert room.entry.runtime_data._command_failures == []
