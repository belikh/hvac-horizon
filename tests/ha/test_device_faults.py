"""A device that fails must not take the rest of the room with it.

The AC and the heater plug are real entities from tests/fakes.py. One of them raises on every command, as a device
does when its integration has lost the connection. The tick still finishes, the other device is still driven, the
failure is reported, and the safety stop still reaches every other output.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from pytest_homeassistant.common import MockConfigEntry

from custom_components.hvac_horizon.const import DOMAIN
from tests.fakes import FakeAc, FakeSwitch, add_devices

AC = "climate.fault_ac"
HEATER = "switch.fault_heater"
FAN = "switch.fault_fan"
ROOM = "sensor.fault_room"
OUTDOOR = "sensor.fault_outdoor"
POWER = "sensor.fault_power"
TEMP = {"unit_of_measurement": "°C", "device_class": "temperature"}


async def _room(hass, freezer, *, room_c: float, outdoor_c: float, **settings):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    ac, heater, fan = FakeAc(AC), FakeSwitch(HEATER), FakeSwitch(FAN)
    await add_devices(hass, ac, heater, fan)
    hass.states.async_set(ROOM, str(room_c), TEMP)
    hass.states.async_set(OUTDOOR, str(outdoor_c), TEMP)
    hass.states.async_set(POWER, "0", {"unit_of_measurement": "W", "device_class": "power"})
    data = {"climate": AC, "power": POWER, "room_sensors": [ROOM], "outdoor": OUTDOOR, "heater_switch": HEATER, "control": True, "min_off_min": 0, **settings}
    entry = MockConfigEntry(domain=DOMAIN, title="Fault room", data=data, unique_id=AC)
    entry.add_to_hass(hass)
    return entry, ac, heater, fan


async def _tick(hass, freezer, entry, minutes=5):
    freezer.tick(timedelta(minutes=minutes))
    for sensor in (ROOM, OUTDOOR):
        state = hass.states.get(sensor)
        hass.states.async_set(sensor, state.state, state.attributes, force_update=True)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_an_ac_that_raises_does_not_end_the_tick_and_is_reported_once(hass, enable_custom_integrations, freezer, caplog):
    entry, ac, _, _ = await _room(hass, freezer, room_c=28.0, outdoor_c=32.0)
    ac.fail_commands = True
    caplog.set_level(logging.WARNING)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    for _ in range(3):
        await _tick(hass, freezer, entry)
        assert coordinator.last_update_success
    assert hass.states.get(AC).state == "off" and ac.calls == []
    assert coordinator.data["commands_unconfirmed"] == [f"{AC}: climate.set_hvac_mode failed: {AC}: no response from the device"]
    assert sum("Command failed" in record.getMessage() for record in caplog.records) == 1
    ac.fail_commands = False
    await _tick(hass, freezer, entry)
    assert hass.states.get(AC).state == "cool"  # the next tick sends the command again
    await _tick(hass, freezer, entry)
    assert coordinator.data["commands_unconfirmed"] == []


async def test_the_heater_still_runs_while_the_ac_raises(hass, enable_custom_integrations, freezer):
    """A cold room in auto. The AC is in dry from its remote and refuses the off command. The heater is driven all the same."""
    entry, ac, heater, _ = await _room(hass, freezer, room_c=17.0, outdoor_c=5.0)
    ac.press_remote(mode="dry")
    ac.fail_commands = True
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await _tick(hass, freezer, entry)
    assert entry.runtime_data.last_update_success
    assert any("climate.set_hvac_mode failed" in text for text in entry.runtime_data._command_failures)
    assert ("turn_on", None) in heater.calls


async def test_the_safety_stop_reaches_every_output_when_the_first_one_raises(hass, enable_custom_integrations, freezer):
    """The entry cools and ventilates. Unloading it with an AC that raises still turns the fan off."""
    hass.states.async_set("sensor.fault_co2", "1800", {"unit_of_measurement": "ppm"})
    entry, ac, _, fan = await _room(hass, freezer, room_c=28.0, outdoor_c=32.0, fan=FAN, co2="sensor.fault_co2")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await _tick(hass, freezer, entry)
    assert hass.states.get(AC).state == "cool" and hass.states.get(FAN).state == "on"
    ac.fail_commands = True
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(FAN).state == "off"
    assert hass.states.get(AC).state == "cool"  # the AC did not answer; nothing else was left on
