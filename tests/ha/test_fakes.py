"""The fake devices behave as Home Assistant entities do. These tests hold the fakes to that, so a test built on them means something."""

from __future__ import annotations

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from tests.fakes import FakeAc, FakeSwitch, add_devices

AC = "climate.fake_ac"
PLUG = "switch.fake_plug"


async def _call(hass, domain, service, **data):
    await hass.services.async_call(domain, service, data, blocking=True)
    await hass.async_block_till_done()


async def test_the_ac_state_carries_the_attributes_of_a_real_ac(hass):
    ac = FakeAc(AC, fan_modes=("auto", "low", "high"), swing_modes=("off", "vertical"))
    ac.room_c = 26.5
    await add_devices(hass, ac)
    state = hass.states.get(AC)
    assert state.state == "off"
    attributes = state.attributes
    assert attributes["hvac_modes"] == ["off", "cool", "heat", "dry", "fan_only"]
    assert (attributes["min_temp"], attributes["max_temp"], attributes["target_temp_step"]) == (16.0, 30.0, 1.0)
    assert attributes["temperature"] == 24.0 and attributes["current_temperature"] == 26.5
    assert attributes["hvac_action"] == "off"
    assert attributes["fan_modes"] == ["auto", "low", "high"] and attributes["fan_mode"] == "auto"
    assert attributes["swing_modes"] == ["off", "vertical"]


async def test_a_command_changes_the_ac_and_is_recorded(hass, freezer):
    ac = FakeAc(AC, fan_modes=("auto", "high"))
    await add_devices(hass, ac)
    freezer.tick(600)
    await _call(hass, "climate", "set_hvac_mode", entity_id=AC, hvac_mode="cool")
    await _call(hass, "climate", "set_temperature", entity_id=AC, temperature=22)
    await _call(hass, "climate", "set_fan_mode", entity_id=AC, fan_mode="high")
    state = hass.states.get(AC)
    assert state.state == "cool" and state.attributes["temperature"] == 22 and state.attributes["fan_mode"] == "high"
    assert state.attributes["hvac_action"] == "cooling"
    assert ac.calls == [("set_hvac_mode", "cool"), ("set_temperature", 22.0), ("set_fan_mode", "high")]
    changed = state.last_changed
    freezer.tick(60)
    await _call(hass, "climate", "set_temperature", entity_id=AC, temperature=23)
    assert hass.states.get(AC).last_changed == changed  # a new setpoint is not a new mode


async def test_the_ac_refuses_what_a_real_ac_refuses(hass):
    ac = FakeAc(AC, modes=("off", "cool"))
    await add_devices(hass, ac)
    with pytest.raises(ServiceValidationError):
        await _call(hass, "climate", "set_hvac_mode", entity_id=AC, hvac_mode="heat")
    with pytest.raises(ServiceValidationError):
        await _call(hass, "climate", "set_temperature", entity_id=AC, temperature=12)
    with pytest.raises(ServiceValidationError):
        await _call(hass, "climate", "set_temperature", entity_id=AC, temperature=31)
    with pytest.raises(ServiceValidationError):
        await _call(hass, "climate", "set_fan_mode", entity_id=AC, fan_mode="high")
    assert ac.calls == [] and hass.states.get(AC).state == "off"


async def test_an_idle_compressor_reads_idle_and_a_silent_ac_reports_nothing(hass):
    ac = FakeAc(AC, mode="cool")
    ac.running = False
    quiet = FakeAc("climate.quiet", mode="cool", reports_action=False, reports_temperature=False)
    await add_devices(hass, ac, quiet)
    assert hass.states.get(AC).attributes["hvac_action"] == "idle"
    assert "hvac_action" not in hass.states.get("climate.quiet").attributes
    assert hass.states.get("climate.quiet").attributes["current_temperature"] is None


async def test_the_three_faults(hass):
    ac, plug = FakeAc(AC), FakeSwitch(PLUG)
    await add_devices(hass, ac, plug)
    ac.ignore_commands = plug.ignore_commands = True
    await _call(hass, "climate", "set_hvac_mode", entity_id=AC, hvac_mode="cool")
    await _call(hass, "switch", "turn_on", entity_id=PLUG)
    assert hass.states.get(AC).state == "off" and hass.states.get(PLUG).state == "off"
    assert ac.calls == [("set_hvac_mode", "cool")] and plug.calls == [("turn_on", None)]
    ac.ignore_commands = plug.ignore_commands = False
    ac.fail_commands = plug.fail_commands = True
    with pytest.raises(HomeAssistantError):
        await _call(hass, "climate", "set_hvac_mode", entity_id=AC, hvac_mode="cool")
    with pytest.raises(HomeAssistantError):
        await _call(hass, "switch", "turn_on", entity_id=PLUG)
    ac.fail_commands = plug.fail_commands = False
    ac.available_now = plug.available_now = False
    ac.async_write_ha_state()
    plug.async_write_ha_state()
    assert hass.states.get(AC).state == "unavailable" and hass.states.get(PLUG).state == "unavailable"


async def test_the_switch_and_the_remote_control(hass):
    seen = []
    plug, ac = FakeSwitch(PLUG, on_change=seen.append), FakeAc(AC)
    await add_devices(hass, plug, ac)
    await _call(hass, "switch", "turn_on", entity_id=PLUG)
    assert hass.states.get(PLUG).state == "on" and seen == [True]
    plug.flip_by_hand(False)
    ac.press_remote(mode="cool", setpoint_c=21.0)
    assert hass.states.get(PLUG).state == "off" and seen == [True, False]
    assert hass.states.get(AC).state == "cool" and hass.states.get(AC).attributes["temperature"] == 21.0
    assert plug.calls == [("turn_on", None)] and ac.calls == []
