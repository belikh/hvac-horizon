import asyncio
"""Home Assistant test: config flow, setup, one refresh and unload.

Run with pytest-homeassistant-custom-component. This directory has no __init__.py,
so the standard-library unittest run does not collect it.
"""

import math
import random
import statistics
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest
from homeassistant import config_entries, data_entry_flow
from homeassistant.components import sensor
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, STATE_UNAVAILABLE
from homeassistant.const import EntityCategory
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from homeassistant.util import dt as dt_util

from custom_components.hvac_horizon.backfill import BacktestRun
from custom_components.hvac_horizon.model import Fit
from custom_components.hvac_horizon.devices import forecast_fit
from custom_components.hvac_horizon.features import room_devices
from custom_components.hvac_horizon import coordinator as coordinator_module
from custom_components.hvac_horizon import coordinator_learning as coordinator_learning_module
from custom_components.hvac_horizon.collector import Entry, running_power_kw
from custom_components.hvac_horizon.forecast import forecast
from custom_components.hvac_horizon.pid import Pid
from custom_components.hvac_horizon.tpi import GAIN_MAX, TpiFit
from custom_components.hvac_horizon.const import (
    DEFAULT_PRICE_CHEAP,
    DEFAULT_PRICE_EXPENSIVE,
    CONF_BOOST_C,
    CONF_AWAY_DELAY_MIN,
    CONF_COMFORT_C,
    CONF_ECO_C,
    CONF_HEATER_INVERTED,
    CONF_HEATER_KP,
    CONF_HEATER_SWITCH,
    CONF_CLIMATE_2,
    CONF_HEAT_BOOST_C,
    CONF_HEAT_ECO_C,
    CONF_POWER_2,
    CONF_OUTPUT_MAX,
    CONF_OUTPUT_MAX_ENTITY,
    CONF_OUTPUT_MIN,
    CONF_OUTPUT_MIN_ENTITY,
    CONF_CLIMATE,
    CONF_LOCK_PIN,
    CONF_SCHEDULE_BLOCKS,
    CONF_OUTDOOR,
    CONF_PRESENCE,
    CONF_VACATION,
    CONF_POWER,
    CONF_PRICE,
    CONF_ROOM_SENSORS,
    DOMAIN,
    PWM_WINDOW_MIN,
)

CLIMATE = "climate.test_ac"
POWER = "sensor.test_ac_power"
ROOM_A = "sensor.test_room_a"
ROOM_B = "sensor.test_room_b"
OUTDOOR = "sensor.test_outdoor"
PRICE = "sensor.test_price"

SENSOR_KEYS = ["minutes_to_setpoint", "energy_kwh", "cost", "status", "samples"]


def set_inputs(hass):
    hass.states.async_set(CLIMATE, "cool", {"temperature": 24.0, "friendly_name": "Test AC"})
    hass.states.async_set(POWER, "1100", {"unit_of_measurement": "W", "device_class": "power"})
    hass.states.async_set(ROOM_A, "26.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(ROOM_B, "26.2", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(OUTDOOR, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(
        PRICE,
        "0.28",
        {
            "unit_of_measurement": "$/kWh",
            "forecast": [
                {"time": "2026-11-20T18:10:00+10:00", "value": 0.28},
                {"time": "2026-11-20T18:15:00+10:00", "value": 0.27},
            ],
        },
    )


@pytest.fixture
def config_data():
    return {
        CONF_CLIMATE: CLIMATE,
        CONF_POWER: POWER,
        CONF_ROOM_SENSORS: [ROOM_A, ROOM_B],
        CONF_OUTDOOR: OUTDOOR,
        CONF_PRICE: PRICE,
    }


ROOM_KEYS = ("climate", "power", "room_sensors", "outdoor")


async def _start_flow(hass, config_data):
    """Open the flow and submit the room step. Returns the menu result."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] == "form" and result["step_id"] == "room"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {k: config_data[k] for k in ROOM_KEYS})
    assert result["type"] == "menu" and result["step_id"] == "menu"
    return result


async def _open_section(hass, flow_id, name):
    result = await hass.config_entries.flow.async_configure(flow_id, {"next_step_id": name})
    assert result["type"] == "form" and result["step_id"] == name
    return result


async def test_config_flow_short_path_creates_an_observe_only_entry(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    result = await _start_flow(hass, config_data)
    assert result["menu_options"][0] == "finish"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "finish"})
    assert result["type"] == "create_entry"
    assert result["data"][CONF_CLIMATE] == CLIMATE
    assert result["data"]["control"] is False
    assert CONF_PRICE not in result["data"]
    assert result["title"] == "Test AC"


async def test_config_flow_full_path_stores_the_fields_of_each_section(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    result = await _start_flow(hass, config_data)
    flow_id = result["flow_id"]
    await _open_section(hass, flow_id, "price")
    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_PRICE: PRICE, "price_cheap": 0.1, "price_expensive": 0.4})
    assert result["type"] == "menu"
    await _open_section(hass, flow_id, "comfort")
    blocks = [{"start_h": 22, "end_h": 6, "preset": "eco"}]
    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_COMFORT_C: 23.0, CONF_SCHEDULE_BLOCKS: blocks})
    assert result["type"] == "menu"
    result = await hass.config_entries.flow.async_configure(flow_id, {"next_step_id": "finish"})
    assert result["type"] == "create_entry"
    data = result["data"]
    assert data[CONF_PRICE] == PRICE and data["price_cheap"] == 0.1 and data["price_expensive"] == 0.4
    assert data[CONF_COMFORT_C] == 23.0 and data[CONF_SCHEDULE_BLOCKS] == blocks
    assert data[CONF_ROOM_SENSORS] == [ROOM_A, ROOM_B] and data["control"] is False


async def test_config_flow_section_can_be_submitted_empty(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    result = await _start_flow(hass, config_data)
    await _open_section(hass, result["flow_id"], "heater")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "menu"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "finish"})
    assert result["type"] == "create_entry"
    assert CONF_HEATER_SWITCH not in result["data"]


async def test_config_flow_turns_control_on_from_the_advanced_section(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    result = await _start_flow(hass, config_data)
    await _open_section(hass, result["flow_id"], "advanced")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"control": True})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "finish"})
    assert result["data"]["control"] is True


async def test_config_flow_rejects_a_duplicate_room(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {k: config_data[k] for k in ROOM_KEYS})
    assert result["type"] == "abort" and result["reason"] == "already_configured"


async def test_config_flow_rejects_cheap_price_above_expensive(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    result = await _start_flow(hass, config_data)
    await _open_section(hass, result["flow_id"], "price")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"price_cheap": 0.5, "price_expensive": 0.2})
    assert result["type"] == "form" and result["step_id"] == "price"
    assert result["errors"] == {"price_cheap": "price_order"}
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_config_flow_rejects_out_of_range_price(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    result = await _start_flow(hass, config_data)
    await _open_section(hass, result["flow_id"], "price")
    with pytest.raises(data_entry_flow.InvalidData):  # the number selector rejects 11 before the flow runs
        await hass.config_entries.flow.async_configure(result["flow_id"], {"price_expensive": 11})
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_config_flow_rejects_fleet_device_with_an_extra_key(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    fleet = [{"device_id": "a", "kind": "cool", "rated_w": 900, "colour": "red"}]
    result = await _start_flow(hass, config_data)
    await _open_section(hass, result["flow_id"], "devices")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"fleet": fleet})
    assert result["type"] == "form" and result["step_id"] == "devices"
    assert result["errors"] == {"fleet": "invalid_fleet"}
    assert "'colour'" in result["description_placeholders"]["fleet_problem"]
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_config_flow_rejects_a_malformed_schedule_on_the_comfort_section(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    result = await _start_flow(hass, config_data)
    await _open_section(hass, result["flow_id"], "comfort")
    bad = [{"start_h": 0.0, "end_h": 25.0, "preset": "eco"}]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_SCHEDULE_BLOCKS: bad})
    assert result["type"] == "form" and result["step_id"] == "comfort"
    assert result["errors"] == {CONF_SCHEDULE_BLOCKS: "invalid_schedule_blocks"}


async def test_config_flow_menu_shows_every_optional_section(hass, enable_custom_integrations, config_data):
    from custom_components.hvac_horizon import config_flow

    set_inputs(hass)
    result = await _start_flow(hass, config_data)
    assert result["menu_options"] == ["finish"] + config_flow.OPTIONAL_SECTIONS
    assert len(config_flow.OPTIONAL_SECTIONS) == 7


async def test_stored_invalid_price_threshold_loads_with_defaults_and_one_warning(hass, enable_custom_integrations, config_data, caplog):
    set_inputs(hass)
    data = {**config_data, "price_cheap": 0.9, "price_expensive": 0.1}
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    await coordinator.async_refresh()
    await coordinator.async_refresh()
    assert coordinator._price_thresholds() == (DEFAULT_PRICE_CHEAP, DEFAULT_PRICE_EXPENSIVE)
    assert sum("Invalid price thresholds" in r.message for r in caplog.records if r.levelname == "WARNING") == 1


async def test_stored_fleet_device_with_an_extra_key_is_kept_with_one_warning(hass, enable_custom_integrations, config_data, caplog):
    set_inputs(hass)
    fleet = [{"device_id": "a", "kind": "cool", "rated_w": 900, "colour": "red"}]
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data={**config_data, "fleet": fleet}, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert [d.device_id for d in entry.runtime_data._fleet()] == ["a"]
    entry.runtime_data._fleet()
    assert sum("unknown keys" in r.message for r in caplog.records if r.levelname == "WARNING") == 1


async def test_setup_publishes_sensors_and_refreshes(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entity_ids = [registry.async_get_entity_id(sensor.DOMAIN, DOMAIN, f"{entry.entry_id}_{k}") for k in SENSOR_KEYS]
    assert all(entity_ids), entity_ids

    coordinator = entry.runtime_data
    async_fire_time_changed(hass, datetime.now(timezone.utc) + timedelta(seconds=310))
    await hass.async_block_till_done()
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.data is not None
    assert coordinator.data["samples"] >= 1
    assert coordinator.data["status"] in {"learning", "reachable", "unreachable", "no running power yet"}

    for eid in entity_ids:
        assert hass.states.get(eid).state != STATE_UNAVAILABLE

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.NOT_LOADED


async def test_window_advice_open_when_outdoor_cooler(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set(OUTDOOR, "18.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(ROOM_A, "25.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(ROOM_B, "25.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert entry.runtime_data.data["window_advice"] == "open"


async def test_economizer_opens_windows_when_outdoor_is_cool(hass, enable_custom_integrations, config_data):
    set_inputs(hass)  # rooms 26.0 and 26.2 C, comfort defaults to 24.0 C
    hass.services.async_register("climate", "set_hvac_mode", lambda call: None)
    hass.services.async_register("climate", "set_temperature", lambda call: None)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set(OUTDOOR, "18.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert entry.runtime_data.data["economizer"] == "open_windows"


async def test_samples_survive_unload_and_setup(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    before = entry.runtime_data.data["samples"]
    assert before >= 1

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    after = entry.runtime_data.data["samples"]
    assert after >= before, (before, after)


async def test_reconfigure_keeps_entry_and_samples(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    before = entry.runtime_data.data["samples"]

    new_data = dict(config_data)
    new_data[CONF_PRICE] = PRICE
    await _reconfigure(hass, entry, new_data)
    assert entry.data[CONF_PRICE] == PRICE
    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert entry.runtime_data.data["samples"] >= before


async def test_control_off_sends_no_climate_calls(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    calls = []
    hass.services.async_register("climate", "set_hvac_mode", lambda call: calls.append(("mode", call.data)))
    hass.services.async_register("climate", "set_temperature", lambda call: calls.append(("temp", call.data)))
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert calls == []


async def test_control_on_sends_setpoint_when_room_is_hot(hass, enable_custom_integrations, config_data):
    set_inputs(hass)  # room 26.0 C, comfort defaults to 24.0 C
    calls = []

    async def record_mode(call):
        calls.append(("mode", call.data))

    async def record_temp(call):
        calls.append(("temp", call.data))

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    hass.services.async_register("climate", "set_temperature", record_temp)
    # Start from an off AC at 22 C, so the cool mode and the setpoint both have to be written.
    hass.states.async_set(CLIMATE, "off", {"temperature": 22.0, "friendly_name": "Test AC", "target_temp_step": 1.0})
    data = dict(config_data, control=True)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert ("mode", {"entity_id": CLIMATE, "hvac_mode": "cool"}) in calls
    # Price 0.28 $/kWh sits inside the 0.15 to 0.40 band, so the target shifts from 24.0 to 24.04 C, and the default device step of 1.0 C rounds it to 24.0.
    assert ("temp", {"entity_id": CLIMATE, "temperature": 24.0}) in calls


async def test_second_ac_gets_the_cool_decision(hass, enable_custom_integrations, config_data):
    set_inputs(hass)  # room 26.0 C, so the decision is cool
    calls = []

    async def record_mode(call):
        calls.append(("mode", dict(call.data)))

    async def record_temp(call):
        calls.append(("temp", dict(call.data)))

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    hass.services.async_register("climate", "set_temperature", record_temp)
    hass.states.async_set(CLIMATE, "cool", {"temperature": 22.0, "friendly_name": "Test AC"})
    hass.states.async_set("climate.test_ac_2", "off", {"temperature": 22.0})
    data = dict(config_data, control=True, climate_2="climate.test_ac_2")
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # The setup refresh is the control cycle. A second refresh would see the setpoint written by the first
    # (the fake handlers leave the state unchanged), read it as a manual change and inhibit control.
    assert entry.runtime_data.data["second_ac"] == "driven"
    assert ("mode", {"entity_id": "climate.test_ac_2", "hvac_mode": "cool"}) in calls


async def test_second_ac_waits_when_first_starts_in_the_same_cycle(hass, enable_custom_integrations, config_data):
    set_inputs(hass)  # room 26.0 C, so the decision is cool
    calls = []

    async def record_mode(call):
        calls.append(("mode", dict(call.data)))

    async def record_temp(call):
        calls.append(("temp", dict(call.data)))

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    hass.services.async_register("climate", "set_temperature", record_temp)
    hass.states.async_set(CLIMATE, "off", {"temperature": 22.0, "friendly_name": "Test AC"})
    hass.states.async_set("climate.test_ac_2", "off", {"temperature": 22.0})
    data = dict(config_data, control=True, climate_2="climate.test_ac_2")
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert ("mode", {"entity_id": CLIMATE, "hvac_mode": "cool"}) in calls
    assert ("mode", {"entity_id": "climate.test_ac_2", "hvac_mode": "cool"}) not in calls


async def test_compressor_group_staggers_starts_across_entries(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)  # room 26.0 C, so both entries decide cool
    calls = []

    async def record_mode(call):
        calls.append((call.data["entity_id"], call.data["hvac_mode"]))

    async def record_temp(call):
        return None

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    hass.services.async_register("climate", "set_temperature", record_temp)
    hass.states.async_set(CLIMATE, "off", {"temperature": 22.0, "friendly_name": "Test AC"})
    hass.states.async_set("climate.test_ac_b", "off", {"temperature": 22.0, "friendly_name": "Test AC B"})
    data = dict(config_data, control=True, compressor_group="garage")
    entry_a = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry_b = MockConfigEntry(domain=DOMAIN, title="Test AC B", data=dict(data, climate="climate.test_ac_b"), unique_id="climate.test_ac_b")
    for entry in (entry_a, entry_b):
        entry.add_to_hass(hass)
    # Both entries load in the same setup, so both want to start in the same tick.
    assert await hass.config_entries.async_setup(entry_a.entry_id)
    await hass.async_block_till_done()
    starts = [entity for entity, mode in calls if mode == "cool"]
    assert len(starts) == 1  # one AC starts, the other waits
    started, waiting = (CLIMATE, entry_b) if starts[0] == CLIMATE else ("climate.test_ac_b", entry_a)
    hass.states.async_set(started, "cool", {"temperature": 24.0, "friendly_name": "Test AC"})
    freezer.tick(timedelta(minutes=4))
    await refresh(hass, waiting)
    assert [entity for entity, mode in calls if mode == "cool"] == starts
    freezer.tick(timedelta(minutes=1))
    await refresh(hass, waiting)
    assert [entity for entity, mode in calls if mode == "cool"] == starts + [waiting.data["climate"]]


async def test_keep_alive_resends_last_setpoint(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    calls = []

    async def ignore_mode(call):
        pass

    async def record_temp(call):
        calls.append(call.data)

    hass.services.async_register("climate", "set_hvac_mode", ignore_mode)
    hass.services.async_register("climate", "set_temperature", record_temp)
    hass.states.async_set(CLIMATE, "cool", {"temperature": 24.0, "friendly_name": "Test AC"})
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data, control=True), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    calls.clear()
    coordinator._last_written_setpoint = 24.0
    coordinator._last_sent_min = 0.0
    # Drive the control step directly with the cycle clock 31 minutes after the last write.
    await coordinator._async_control(31 * 60.0, {})
    assert {"entity_id": CLIMATE, "temperature": 24.0} in calls


async def test_flow_accepts_control_options(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    result = await _start_flow(hass, config_data)
    flow_id = result["flow_id"]
    for section, fields in (("comfort", {"comfort_c": 24.0, "away_c": 27.0}), ("occupancy", {"window_delay_min": 10}), ("advanced", {"control": False})):
        await _open_section(hass, flow_id, section)
        result = await hass.config_entries.flow.async_configure(flow_id, fields)
    result = await hass.config_entries.flow.async_configure(flow_id, {"next_step_id": "finish"})
    assert result["type"] == "create_entry"
    assert result["data"]["window_delay_min"] == 10 and result["data"]["away_c"] == 27.0
    assert result["data"]["comfort_c"] == 24.0
    assert result["data"]["control"] is False


async def test_stalled_sensors_are_reported(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    stalled = entry.runtime_data._stalled_sensors(datetime.now(timezone.utc).timestamp() + 5000)
    assert set(stalled) >= {ROOM_A, ROOM_B, OUTDOOR}


async def test_repeated_sensor_value_is_not_a_stall(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data)
    freezer.tick(timedelta(minutes=20))
    # Same value and attributes: Home Assistant updates last_reported only.
    set_room(hass, "20.0")
    hass.states.async_set(OUTDOOR, "10.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    room = hass.states.get(ROOM_A)
    assert room.last_updated < room.last_reported
    await refresh(hass, entry)
    assert entry.runtime_data.data["sensors_stalled"] == []


async def test_sensor_without_a_report_for_15_minutes_is_a_stall(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data)
    freezer.tick(timedelta(minutes=15))
    await refresh(hass, entry)
    assert set(entry.runtime_data.data["sensors_stalled"]) == {ROOM_A, ROOM_B, OUTDOOR}


async def test_stall_window_option_sets_when_a_sensor_counts_as_stalled(hass, enable_custom_integrations, config_data, freezer):
    """A 300 s stall_window_s marks a sensor stalled after 5 minutes with no report; the default 900 s does not."""
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data, extra={"stall_window_s": 300.0})
    assert entry.runtime_data._stall_window_s() == 300.0
    freezer.tick(timedelta(minutes=6))
    await refresh(hass, entry)
    assert set(entry.runtime_data.data["sensors_stalled"]) == {ROOM_A, ROOM_B, OUTDOOR}


async def test_stall_window_defaults_to_900_seconds(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data)
    assert entry.runtime_data._stall_window_s() == 900.0
    freezer.tick(timedelta(minutes=6))
    await refresh(hass, entry)
    assert entry.runtime_data.data["sensors_stalled"] == []


@pytest.mark.parametrize("state", ["missing", "unavailable"])
async def test_missing_or_unavailable_sensor_is_not_a_stall(hass, enable_custom_integrations, config_data, freezer, state):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data)
    if state == "missing":
        hass.states.async_remove(ROOM_A)
    else:
        hass.states.async_set(ROOM_A, "unavailable", {"unit_of_measurement": "°C", "device_class": "temperature"})
    freezer.tick(timedelta(minutes=30))
    await refresh(hass, entry)
    assert ROOM_A not in entry.runtime_data.data["sensors_stalled"]


async def test_no_room_data_leaves_the_ac_alone(hass, enable_custom_integrations, config_data, freezer):
    # Inside the 900 s no-data window, _async_control returns without a climate action. The AC keeps its state.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data, room="unavailable", outdoor="30.0", climate="cool")
    modes = []

    async def record_mode(call):
        modes.append(call.data["hvac_mode"])

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    freezer.tick(timedelta(minutes=10))
    # Keep the outdoor sensor fresh, so only the room readings are missing.
    hass.states.async_set(OUTDOOR, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, entry)
    assert modes == []


async def test_all_rooms_unavailable_for_16_minutes_turns_the_ac_off(hass, enable_custom_integrations, config_data, freezer):
    # No room has reported a valid reading for longer than the 900 s stall window. The AC must stop.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data, room="unavailable", outdoor="30.0", climate="cool")
    modes = []

    async def record_mode(call):
        modes.append(call.data["hvac_mode"])

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    freezer.tick(timedelta(minutes=16))
    hass.states.async_set(OUTDOOR, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, entry)
    assert "off" in modes
    assert "cool" not in modes


async def test_one_valid_room_keeps_the_ac_running(hass, enable_custom_integrations, config_data, freezer):
    # Room A reports a hot reading at each step. Room B stays unavailable. The no-data clock never starts.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data, room="26.0", outdoor="30.0", climate="cool")
    hass.states.async_set(ROOM_B, "unavailable", {"unit_of_measurement": "°C", "device_class": "temperature"})
    modes = []

    async def record_mode(call):
        modes.append(call.data["hvac_mode"])

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    for _ in range(4):
        freezer.tick(timedelta(minutes=5))
        hass.states.async_set(ROOM_A, "26.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
        hass.states.async_set(OUTDOOR, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
        await refresh(hass, entry)
    assert "off" not in modes


async def test_motion_sensor_publishes_home_preset(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    hass.states.async_set("binary_sensor.test_motion", "on")

    async def ignore(call):
        return None

    hass.services.async_register("climate", "set_hvac_mode", ignore)
    hass.services.async_register("climate", "set_temperature", ignore)
    data = dict(config_data, motion="binary_sensor.test_motion")
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert entry.runtime_data.data["motion_preset"] == "home"


async def test_fan_follows_air_quality(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    hass.states.async_set("switch.test_fan", "off")
    hass.states.async_set("sensor.test_co2", "1200", {"unit_of_measurement": "ppm"})
    calls = []

    async def record(call):
        calls.append(call.service)

    hass.services.async_register("switch", "turn_on", record)
    hass.services.async_register("switch", "turn_off", record)

    async def ignore(call):
        return None

    hass.services.async_register("climate", "set_hvac_mode", ignore)
    hass.services.async_register("climate", "set_temperature", ignore)
    # Actuation only runs with control on. Observe-only sends no commands.
    data = dict(config_data, fan="switch.test_fan", co2="sensor.test_co2", control=True)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert "turn_on" in calls


async def test_humid_room_uses_ac_dry_mode(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    hass.states.async_set("sensor.test_humidity", "72", {"unit_of_measurement": "%"})
    # Room below the 24 C comfort point, so no cooling is needed and only drying applies.
    hass.states.async_set(ROOM_A, "23.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(ROOM_B, "23.2", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(CLIMATE, "off", {"temperature": 24.0, "friendly_name": "Test AC"})
    calls = []

    async def record_mode(call):
        calls.append(call.data)

    async def ignore(call):
        return None

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    hass.services.async_register("climate", "set_temperature", ignore)
    data = dict(config_data, room_humidity="sensor.test_humidity", control=True, dry_for_humidity=True)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert {"entity_id": CLIMATE, "hvac_mode": "dry"} in calls


async def test_ac_dry_mode_for_humidity_is_off_by_default(hass, enable_custom_integrations, config_data):
    """Dry mode on the AC makes a lot of water, so it runs only after the user opts in."""
    calls = []

    async def record_mode(call):
        calls.append(dict(call.data))

    async def ignore(call):
        return None

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    hass.services.async_register("climate", "set_temperature", ignore)
    data = dict(config_data, room_humidity="sensor.test_humidity", control=True)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert {"entity_id": CLIMATE, "hvac_mode": "dry"} not in calls


async def test_humidity_band_reads_room_sensor_not_outdoor(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    hass.states.async_set("sensor.test_room_humidity", "70", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.test_outdoor_humidity", "40", {"unit_of_measurement": "%"})
    hass.states.async_set("switch.test_dehumidifier", "off")

    async def ignore(call):
        return None

    hass.services.async_register("switch", "turn_on", ignore)
    hass.services.async_register("switch", "turn_off", ignore)
    hass.services.async_register("climate", "set_hvac_mode", ignore)
    hass.services.async_register("climate", "set_temperature", ignore)
    data = dict(
        config_data,
        room_humidity="sensor.test_room_humidity",
        humidity="sensor.test_outdoor_humidity",
        dehumidifier="switch.test_dehumidifier",
        control=True,
    )
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    # 70 % room humidity is above the 60 % band; 40 % outdoor would sit at the low edge and report "none".
    assert entry.runtime_data.data["humidity"] == "70 % above 60 %"


async def test_humidifier_turns_on_when_air_is_dry(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    hass.states.async_set("switch.test_humidifier", "off")
    hass.states.async_set("sensor.test_humidity", "30", {"unit_of_measurement": "%"})
    calls = []

    async def record(call):
        calls.append((call.service, call.data["entity_id"]))

    async def ignore(call):
        return None

    hass.services.async_register("switch", "turn_on", record)
    hass.services.async_register("switch", "turn_off", record)
    hass.services.async_register("climate", "set_hvac_mode", ignore)
    hass.services.async_register("climate", "set_temperature", ignore)
    data = dict(config_data, humidifier="switch.test_humidifier", room_humidity="sensor.test_humidity", control=True)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert ("turn_on", "switch.test_humidifier") in calls


async def test_diagnostics_redacts_entity_ids(hass, enable_custom_integrations, config_data):
    from custom_components.hvac_horizon.diagnostics import async_get_config_entry_diagnostics

    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["config"]["climate"] != CLIMATE
    assert "samples" in diag


async def test_diagnostics_redacts_lock_pin_and_keeps_keys(hass, enable_custom_integrations, config_data):
    import json

    from custom_components.hvac_horizon.diagnostics import async_get_config_entry_diagnostics

    set_inputs(hass)
    data = dict(config_data, lock_pin="7391", fleet=[{"device_id": "dev_abc", "power_sensor": "sensor.fleet_w", "rated_w": 900}])
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, options={"lock_pin": "7391"}, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entry.runtime_data.data = dict(entry.runtime_data.data or {}, nested={"lock_pin": "7391", "items": [{"climate": CLIMATE}]})
    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = json.dumps(diag, default=str)
    assert "7391" not in text
    assert CLIMATE not in text
    assert "sensor.fleet_w" not in text
    assert diag["config"]["lock_pin"] == "**REDACTED**"
    assert diag["config"]["climate"] == "**REDACTED**"
    assert diag["options"]["lock_pin"] == "**REDACTED**"
    assert diag["status"]["nested"]["lock_pin"] == "**REDACTED**"
    assert diag["status"]["nested"]["items"][0]["climate"] == "**REDACTED**"
    assert diag["config"]["fleet"] == "**REDACTED**"


async def test_reset_learning_service_clears_samples(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    before = entry.runtime_data.data["samples"]
    assert before >= 2
    await hass.services.async_call(DOMAIN, "reset_learning", {"entry_id": entry.entry_id}, blocking=True)
    await refresh(hass, entry)
    assert entry.runtime_data.data["samples"] < before


async def test_lock_needs_pin_and_blocks_control(hass, enable_custom_integrations, config_data):
    set_inputs(hass)

    async def ignore(call):
        return None

    hass.services.async_register("climate", "set_hvac_mode", ignore)
    hass.services.async_register("climate", "set_temperature", ignore)
    data = dict(config_data, lock_pin="1234", control=True)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    assert coordinator.set_lock(True, None) is True
    await refresh(hass, entry)
    assert coordinator.data["action"] == "locked"
    assert coordinator.set_lock(False, "0000") is False
    assert coordinator.set_lock(False, "1234") is True
    await refresh(hass, entry)
    assert coordinator.data["action"] != "locked"


async def test_backfill_runs_and_reports_count(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    added = await entry.runtime_data.async_backfill_from_recorder(days=1.0)
    assert isinstance(added, int)
    assert added >= 0


async def test_export_csv_service_returns_csv_header(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    result = await hass.services.async_call(DOMAIN, "export_csv", {"entry_id": entry.entry_id}, blocking=True, return_response=True)
    assert result["csv"].splitlines()[0] == "time,room_c,outdoor_c,power_w"


async def test_export_samples_service_returns_readings(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    result = await hass.services.async_call(DOMAIN, "export_samples", {"entry_id": entry.entry_id}, blocking=True, return_response=True)
    assert len(result["samples"]) >= 1


async def test_stalled_sensor_raises_repair_issue(hass, enable_custom_integrations, config_data):
    from homeassistant.helpers import issue_registry as ir

    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    coordinator._report_stalls([ROOM_A])
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{entry.entry_id}_stalled") is not None
    coordinator._report_stalls([])
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{entry.entry_id}_stalled") is None


async def test_what_if_service_returns_forecast(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.services.async_call(
        DOMAIN, "what_if",
        {"entry_id": entry.entry_id, "room_c": 26.0, "target_c": 24.0, "outdoor_c": 30.0, "power_kw": 1.1},
        blocking=True, return_response=True,
    )
    assert result["status"] in ("learning", "reachable", "unreachable")


async def test_mode_mismatch_raises_and_clears_repair_issue(hass, enable_custom_integrations, config_data):
    from homeassistant.helpers import issue_registry as ir

    set_inputs(hass)

    async def ignore(call):
        pass

    hass.services.async_register("climate", "set_hvac_mode", ignore)
    hass.services.async_register("climate", "set_temperature", ignore)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data, control=True), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    issue_id = f"{entry.entry_id}_mode_mismatch"
    # Calls the helper directly; the update path writes modes itself and would overwrite the test state.
    coordinator._last_written_mode = "cool"
    coordinator._check_mode("off")
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    coordinator._check_mode("cool")
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_timed_preset_overrides_then_reverts(hass, enable_custom_integrations, config_data, freezer):
    set_inputs(hass)

    async def ignore(call):
        pass

    hass.services.async_register("climate", "set_hvac_mode", ignore)
    hass.services.async_register("climate", "set_temperature", ignore)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data, control=True), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    await hass.services.async_call(DOMAIN, "start_timed_preset", {"entry_id": entry.entry_id, "preset": "eco", "minutes": 30})
    await refresh(hass, entry)
    assert coordinator.data["preset"] == "eco"
    freezer.tick(timedelta(minutes=31))
    await refresh(hass, entry)
    assert coordinator.data["preset"] == "comfort"


async def _setup_with_gain_suggestion(hass, config_data):
    """Set up an entry whose learned heat gain (0.3) sits 50 % above the baseline (0.2), so a suggestion is published."""
    from custom_components.hvac_horizon import Fit

    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    coordinator._fit = Fit(k=0.3, c=1.5, g=0.0)
    coordinator._baseline_k = 0.2
    await refresh(hass, entry)
    assert "50 % more" in coordinator.data["learning_suggestion"]
    return entry


async def test_respond_suggestion_service_dismiss_hides_it_until_the_gain_drifts(hass, enable_custom_integrations, config_data):
    from custom_components.hvac_horizon import Fit

    entry = await _setup_with_gain_suggestion(hass, config_data)
    await hass.services.async_call(DOMAIN, "respond_suggestion", {"entry_id": entry.entry_id, "accept": False}, blocking=True)
    await refresh(hass, entry)
    assert entry.runtime_data.data["learning_suggestion"] is None
    entry.runtime_data._fit = Fit(k=0.4, c=1.5, g=0.0)  # the gain drifts more than 1 % from the dismissed value
    await refresh(hass, entry)
    assert "100 % more" in entry.runtime_data.data["learning_suggestion"]


async def test_respond_suggestion_service_accept_adopts_the_learned_gain(hass, enable_custom_integrations, config_data):
    from custom_components.hvac_horizon import Fit

    entry = await _setup_with_gain_suggestion(hass, config_data)
    await hass.services.async_call(DOMAIN, "respond_suggestion", {"entry_id": entry.entry_id, "accept": True}, blocking=True)
    await refresh(hass, entry)
    assert entry.runtime_data.data["learning_suggestion"] is None
    entry.runtime_data._fit = Fit(k=0.33, c=1.5, g=0.0)  # 10 % above the adopted 0.3, inside the 20 % tolerance
    await refresh(hass, entry)
    assert entry.runtime_data.data["learning_suggestion"] is None


async def test_heater_feedback_is_published(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    hass.states.async_set("switch.test_heater", "off")
    hass.services.async_register("switch", "turn_on", lambda call: None)
    hass.services.async_register("switch", "turn_off", lambda call: None)
    data = {**config_data, "heater_switch": "switch.test_heater"}
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    value = entry.runtime_data.data["heater_feedback"]
    print("heater_feedback =", value)
    assert value in {"not_configured", "unavailable", "match", "mismatch"}


async def test_heater_pwm_pattern_is_published(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    coordinator.set_heater_output(40.0)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert "heater_pwm_on" in coordinator.data


async def test_valve_opening_is_published_and_driven(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    hass.states.async_set("number.test_valve", "0")
    calls = []

    async def record_valve(call):
        calls.append(call.data)

    async def ignore(call):
        return None

    hass.services.async_register("number", "set_value", record_valve)
    hass.services.async_register("climate", "set_hvac_mode", ignore)
    hass.services.async_register("climate", "set_temperature", ignore)
    data = dict(config_data, control=True, valve="number.test_valve")
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert 0 <= coordinator.data["valve_opening_pct"] <= 100


async def test_cover_closes_on_high_solar_gain(hass, enable_custom_integrations, config_data):
    async def ignore(call):
        return None

    hass.services.async_register("cover", "open_cover", ignore)
    hass.services.async_register("cover", "close_cover", ignore)
    hass.services.async_register("climate", "set_hvac_mode", ignore)
    hass.services.async_register("climate", "set_temperature", ignore)
    hass.states.async_set("cover.test_blind", "open")
    hass.states.async_set("sensor.test_irradiance", "800", {"unit_of_measurement": "W/m²"})
    data = dict(
        config_data, control=True, cover="cover.test_blind", irradiance="sensor.test_irradiance", window_area_m2=2.0
    )
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert "cover_action" in coordinator.data
    assert coordinator.data["cover_action"] == "close"


async def test_weather_forecast_drives_heatwave(hass, enable_custom_integrations, config_data):
    async def ignore(call):
        return None

    async def forecasts(call):
        return {"weather.test": {"forecast": [{"temperature": 31.0}, {"temperature": 32.0}, {"temperature": 33.0}]}}

    hass.services.async_register("weather", "get_forecasts", forecasts, supports_response=SupportsResponse.ONLY)
    hass.services.async_register("climate", "set_hvac_mode", ignore)
    hass.services.async_register("climate", "set_temperature", ignore)
    hass.states.async_set("weather.test", "sunny")
    data = dict(config_data, weather="weather.test")
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.data["heatwave"] is True


async def test_power_shedding_plans_lowest_priority_load(hass, enable_custom_integrations, config_data):
    async def ignore(call):
        return None

    hass.services.async_register("switch", "turn_off", ignore)
    hass.services.async_register("climate", "set_hvac_mode", ignore)
    hass.services.async_register("climate", "set_temperature", ignore)
    hass.states.async_set("sensor.test_house_power", "5000", {"unit_of_measurement": "W"})
    hass.states.async_set("switch.test_load", "on")
    data = dict(
        config_data,
        control=True,
        whole_house_power="sensor.test_house_power",
        shed_max_w=4000,
        shed_loads=["switch.test_load|1000"],
    )
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.data["shed"] == ["switch.test_load"]


async def test_boiler_turns_on_when_cold_and_room_below_low_edge(hass, enable_custom_integrations, config_data):
    calls = []

    async def record_on(call):
        calls.append(("switch.turn_on", call.data["entity_id"]))

    async def record_off(call):
        calls.append(("switch.turn_off", call.data["entity_id"]))

    async def ignore(call):
        return None

    hass.services.async_register("switch", "turn_on", record_on)
    hass.services.async_register("switch", "turn_off", record_off)
    hass.services.async_register("climate", "set_hvac_mode", ignore)
    hass.services.async_register("climate", "set_temperature", ignore)
    set_inputs(hass)
    hass.states.async_set(OUTDOOR, "0.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(ROOM_A, "20.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(ROOM_B, "20.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set("switch.test_boiler", "off")
    data = dict(config_data, control=True, boiler="switch.test_boiler")
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.data["heat_source"] == "boiler"
    assert ("switch.turn_on", "switch.test_boiler") in calls


async def test_boiler_blocked_when_outdoor_above_heat_limit(hass, enable_custom_integrations, config_data):
    calls = []

    async def record_on(call):
        calls.append(("switch.turn_on", call.data["entity_id"]))

    async def record_off(call):
        calls.append(("switch.turn_off", call.data["entity_id"]))

    async def ignore(call):
        return None

    hass.services.async_register("switch", "turn_on", record_on)
    hass.services.async_register("switch", "turn_off", record_off)
    hass.services.async_register("climate", "set_hvac_mode", ignore)
    hass.services.async_register("climate", "set_temperature", ignore)
    set_inputs(hass)
    hass.states.async_set(OUTDOOR, "25.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(ROOM_A, "20.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(ROOM_B, "20.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set("switch.test_boiler", "off")
    data = dict(config_data, control=True, boiler="switch.test_boiler")
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert ("switch.turn_on", "switch.test_boiler") not in calls
    assert coordinator.data["heating_allowed"] is False


async def test_set_house_mode_service_stores_mode(hass, enable_custom_integrations, config_data):
    set_inputs(hass)

    async def ignore(call):
        pass

    hass.services.async_register("climate", "set_hvac_mode", ignore)
    hass.services.async_register("climate", "set_temperature", ignore)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data, control=True), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "set_house_mode", {"mode": "away"}, blocking=True)
    assert hass.data[DOMAIN]["house_mode"] == "away"


async def test_coil_dry_runs_after_cooling_stops(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    hass.states.async_set(ROOM_A, "23.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(ROOM_B, "23.2", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(CLIMATE, "cool", {"temperature": 24.0, "friendly_name": "Test AC"})
    calls = []

    async def record_mode(call):
        calls.append(("mode", call.data))

    async def record_temp(call):
        calls.append(("temp", call.data))

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    hass.services.async_register("climate", "set_temperature", record_temp)
    # coil_dry is opt-in: the AC's dry mode produces a lot of water, so the test turns it on explicitly.
    entry = MockConfigEntry(
        domain=DOMAIN, title="Test AC", data=dict(config_data, control=True, coil_dry=True), unique_id=CLIMATE
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set(CLIMATE, "off", {"temperature": 24.0, "friendly_name": "Test AC"})
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert ("mode", {"entity_id": CLIMATE, "hvac_mode": "dry"}) in calls



async def test_heater_pid_leaves_output_alone_with_control_off(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    hass.states.async_set("switch.test_heater", "off")
    switched = []
    hass.services.async_register("switch", "turn_on", lambda call: switched.append("turn_on"))
    hass.services.async_register("switch", "turn_off", lambda call: switched.append("turn_off"))
    data = {**config_data, "heater_switch": "switch.test_heater"}
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    coordinator.set_heater_output(40.0)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert switched == []
    assert "heater_pid_output" not in coordinator.data


async def test_heater_pid_publishes_output_with_control_on(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    # Outdoor 10 C: at 30 C heating_allowed is false and the gate keeps the PID idle.
    hass.states.async_set(OUTDOOR, "10.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set("switch.test_heater", "off")
    hass.services.async_register("switch", "turn_on", lambda call: None)
    hass.services.async_register("switch", "turn_off", lambda call: None)
    hass.services.async_register("climate", "set_hvac_mode", lambda call: None)
    hass.services.async_register("climate", "set_temperature", lambda call: None)
    data = {**config_data, "heater_switch": "switch.test_heater", "control": True}
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    out = coordinator.data["heater_pid_output"]
    assert 0.0 <= out <= 100.0


async def test_tpi_duty_drives_the_heater_switch(hass, enable_custom_integrations, config_data, freezer):
    # Room 20 C, target 24 C, outdoor 5 C. coef_int 0.1 and coef_ext 0.01 give 0.1 * 4 + 0.01 * 19 = 59 %.
    # The switch turns on at the window start and off at 59 % of the 10-minute window.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    extra = {"comfort_c": 24.0, "heater_algorithm": "tpi", "tpi_coef_int": 0.1, "tpi_coef_ext": 0.01}
    entry, calls = await setup_heater(hass, config_data, outdoor="5.0", room="20.0", extra=extra)
    coordinator = entry.runtime_data
    await refresh(hass, entry)
    expected = 100.0 * (0.1 * (24.0 - 20.0) + 0.01 * (24.0 - 5.0))
    assert coordinator.data["tpi_output_pct"] == pytest.approx(expected, abs=1.0)
    assert coordinator.data["heater_output_pct"] == pytest.approx(expected, abs=1.0)
    assert "heater_pid_output" not in coordinator.data
    assert "turn_on" in calls
    # The fake switch service does not change state, so mark the switch on to match the turn_on it received.
    hass.states.async_set(HEATER, "on")
    calls.clear()
    # The off edge falls at 59 % of the window: 5.9 minutes. The window must not end before 5.8 and must end by 6.0.
    freezer.tick(timedelta(minutes=5.8))
    await coordinator._pwm_timer(dt_util.utcnow())
    await hass.async_block_till_done()
    assert "turn_off" not in calls
    freezer.tick(timedelta(minutes=0.2))
    await coordinator._pwm_timer(dt_util.utcnow())
    await hass.async_block_till_done()
    # At least one turn_off: a real timer may fire too, because the freezer moves the loop clock.
    assert "turn_off" in calls


async def test_max_on_percent_caps_the_heater_switch_window(hass, enable_custom_integrations, config_data, freezer):
    # Raw TPI is 59 % (see test_tpi_duty_drives_the_heater_switch). A cap of 30 % turns the switch off at 3 minutes of the 10-minute window.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    extra = {"comfort_c": 24.0, "heater_algorithm": "tpi", "tpi_coef_int": 0.1, "tpi_coef_ext": 0.01, "max_on_percent": 30.0}
    entry, calls = await setup_heater(hass, config_data, outdoor="5.0", room="20.0", extra=extra)
    coordinator = entry.runtime_data
    await refresh(hass, entry)
    assert coordinator.data["tpi_output_pct"] == pytest.approx(30.0)
    assert "turn_on" in calls
    # The fake switch does not change state, so mark it on to match the turn_on it received.
    hass.states.async_set(HEATER, "on")
    calls.clear()
    freezer.tick(timedelta(minutes=2.9))
    await coordinator._pwm_timer(dt_util.utcnow())
    await hass.async_block_till_done()
    assert "turn_off" not in calls
    freezer.tick(timedelta(minutes=0.2))
    await coordinator._pwm_timer(dt_util.utcnow())
    await hass.async_block_till_done()
    assert "turn_off" in calls


async def test_tpi_threshold_forces_the_output_off_while_the_room_rises(hass, enable_custom_integrations, config_data, freezer):
    # Target 24 C, outdoor 5 C. Room 24.4 gives 15 %. Room 24.6 gives 13 % with no threshold.
    # With high 0.5 and low 0.3, a room rising to 24.6 is 0.6 above target, so the output is forced to 0.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    base = {"comfort_c": 24.0, "heater_algorithm": "tpi", "tpi_coef_int": 0.1, "tpi_coef_ext": 0.01}
    entry, _ = await setup_heater(hass, config_data, outdoor="5.0", room="24.4", extra={**base, "tpi_threshold_high": 0.5, "tpi_threshold_low": 0.3})
    await refresh(hass, entry)
    # The first tick has no previous room reading, so there is no trend and the threshold does not apply yet.
    assert entry.runtime_data.data["tpi_output_pct"] == pytest.approx(15.0, abs=0.5)
    set_room(hass, "24.6")
    await refresh(hass, entry)
    assert entry.runtime_data.data["tpi_output_pct"] == pytest.approx(0.0)


async def test_tpi_threshold_with_one_value_at_zero_has_no_effect(hass, enable_custom_integrations, config_data, freezer):
    # Same room as the test above. A low threshold of 0 disables the rule, as in VT, so the output stays at 13 %.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    base = {"comfort_c": 24.0, "heater_algorithm": "tpi", "tpi_coef_int": 0.1, "tpi_coef_ext": 0.01}
    entry, _ = await setup_heater(hass, config_data, outdoor="5.0", room="24.4", extra={**base, "tpi_threshold_high": 0.5, "tpi_threshold_low": 0.0})
    await refresh(hass, entry)
    set_room(hass, "24.6")
    await refresh(hass, entry)
    assert entry.runtime_data.data["tpi_output_pct"] == pytest.approx(13.0, abs=0.5)


def _climate_follows_setpoint(hass):
    """Fake set_temperature that moves the climate's reported setpoint. Without it the AC reads as changed by hand."""

    async def follow(call):
        state = hass.states.get(CLIMATE)
        hass.states.async_set(CLIMATE, state.state, {"temperature": call.data["temperature"], "target_temp_step": 1.0, "friendly_name": "Test AC"})

    return follow


def _record_climate_writes(hass):
    writes = []

    async def record_mode(call):
        writes.append(("mode", call.data["hvac_mode"]))

    async def record_setpoint(call):
        writes.append(("setpoint", call.data["temperature"]))

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    hass.services.async_register("climate", "set_temperature", record_setpoint)
    return writes


@pytest.mark.parametrize("flag", [False, True], ids=["flag_off", "flag_on"])
async def test_night_precool_plan_starts_the_ac_when_away(hass, enable_custom_integrations, config_data, freezer, flag):
    # Away with room 25 C: the away setback is 27 C, so with the flag off the AC stays off.
    # Outdoor 18 C is within the plan's 16 to 20 C band and the room is above comfort 24 C, so the plan says pre-cool.
    # With the flag on, the AC starts cooling to comfort 24 C.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data, outdoor="18.0", room="25.0", extra={"night_plan_act": flag}, temperature_handler=_climate_follows_setpoint(hass))
    writes = _record_climate_writes(hass)
    # The setup tick started a cool at 10:00. Ten minutes later the minimum on time has passed and the sensors are still fresh.
    freezer.tick(timedelta(minutes=10))
    await hass.services.async_call("hvac_horizon", "set_house_mode", {"mode": "away"}, blocking=True)
    await refresh(hass, entry)
    assert entry.runtime_data.data["night_plan"] == "pre-cool"
    # The device already reads 24 C from the setup tick, so the cool start writes only the mode.
    assert hass.states.get(CLIMATE).attributes["temperature"] == 24.0
    if flag:
        assert ("mode", "cool") in writes
    else:
        assert writes == []


@pytest.mark.parametrize("flag", [False, True], ids=["flag_off", "flag_on"])
async def test_night_hold_stops_a_cool_start_at_comfort(hass, enable_custom_integrations, config_data, freezer, flag):
    # Eco is set to 23 C, below comfort 24 C, so eco cools a 23.6 C room (above eco plus the 0.5 K band). The room is at or below comfort, so the plan says hold.
    # With the flag on, the hold stops the AC from starting. With the flag off, eco starts it.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    extra = {"comfort_c": 24.0, "eco_c": 23.0, "night_plan_act": flag}
    entry, _ = await setup_heater(hass, config_data, outdoor="18.0", room="23.6", extra=extra, temperature_handler=_climate_follows_setpoint(hass))
    writes = _record_climate_writes(hass)
    await hass.services.async_call("hvac_horizon", "start_timed_preset", {"entry_id": entry.entry_id, "preset": "eco", "minutes": 60}, blocking=True)
    await refresh(hass, entry)
    assert entry.runtime_data.data["night_plan"] == "hold"
    if flag:
        assert writes == []
    else:
        assert ("mode", "cool") in writes


@pytest.mark.parametrize("flag", [False, True], ids=["flag_off", "flag_on"])
async def test_night_hold_sets_a_running_ac_to_comfort(hass, enable_custom_integrations, config_data, freezer, flag):
    # Same room and eco setting, but the AC already cools. Eco would write 23 C. The hold writes comfort, 24 C.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    extra = {"comfort_c": 24.0, "eco_c": 23.0, "night_plan_act": flag}
    entry, _ = await setup_heater(hass, config_data, outdoor="18.0", room="23.5", extra=extra, climate="cool", temperature_handler=_climate_follows_setpoint(hass))
    writes = _record_climate_writes(hass)
    await hass.services.async_call("hvac_horizon", "start_timed_preset", {"entry_id": entry.entry_id, "preset": "eco", "minutes": 60}, blocking=True)
    await refresh(hass, entry)
    assert ("setpoint", 24.0 if flag else 23.0) in writes


def _window_actuator_fakes(hass):
    """Cover services that move the cover's state, so a second write only happens after a state change."""
    calls = []

    async def opened(call):
        calls.append(("open", call.data["entity_id"]))
        hass.states.async_set("cover.test_window", "open")

    async def closed(call):
        calls.append(("close", call.data["entity_id"]))
        hass.states.async_set("cover.test_window", "closed")

    hass.services.async_register("cover", "open_cover", opened)
    hass.services.async_register("cover", "close_cover", closed)
    hass.states.async_set("cover.test_window", "closed")
    return calls


async def test_window_actuator_opens_on_ventilation_advice_and_closes_when_it_ends(hass, enable_custom_integrations, config_data, freezer):
    # Room 24 C, outdoor 18 C: the advice is open windows, and outdoors is 6 K cooler. Outdoor 26 C ends the advice.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    calls = _window_actuator_fakes(hass)
    entry, _ = await setup_heater(hass, config_data, outdoor="18.0", room="26.0", extra={"window_actuator": "cover.test_window"})
    assert entry.runtime_data.data["window_advice"] == "open"
    assert calls == [("open", "cover.test_window")]
    await refresh(hass, entry)
    assert calls == [("open", "cover.test_window")]  # the state already reads open, so no second call
    hass.states.async_set(OUTDOOR, "26.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, entry)
    assert entry.runtime_data.data["window_advice"] == "close"
    assert calls == [("open", "cover.test_window"), ("close", "cover.test_window")]


async def test_window_actuator_stays_shut_on_a_cold_day_with_the_heater_on(hass, enable_custom_integrations, config_data, freezer):
    # Room 19 C, outdoor 5 C: outdoors is 14 K cooler but the room wants heat, so the window stays shut.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    calls = _window_actuator_fakes(hass)
    entry, _ = await setup_heater(hass, config_data, heater_state="on", outdoor="5.0", room="19.0", extra={"window_actuator": "cover.test_window"})
    await refresh(hass, entry)
    assert entry.runtime_data.data["window_advice"] == "close"
    assert ("open", "cover.test_window") not in calls


async def test_window_actuator_stays_shut_on_a_cold_day_with_the_heater_off(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    calls = _window_actuator_fakes(hass)
    entry, _ = await setup_heater(hass, config_data, outdoor="5.0", room="19.0", extra={"window_actuator": "cover.test_window"})
    await refresh(hass, entry)
    assert ("open", "cover.test_window") not in calls


async def test_window_actuator_opens_for_a_hot_room_with_cool_outdoor_air(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    calls = _window_actuator_fakes(hass)
    entry, _ = await setup_heater(hass, config_data, outdoor="20.0", room="27.0", extra={"window_actuator": "cover.test_window"})
    await refresh(hass, entry)
    assert ("open", "cover.test_window") in calls


async def test_window_actuator_stays_unused_without_the_option(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    calls = _window_actuator_fakes(hass)
    entry, _ = await setup_heater(hass, config_data, outdoor="18.0", room="26.0")
    assert entry.runtime_data.data["window_advice"] == "open"
    await refresh(hass, entry)
    assert calls == []


@pytest.mark.parametrize("gate", ["none", "control_off", "house_away", "locked", "stalled"])
async def test_window_actuator_obeys_its_gates(hass, enable_custom_integrations, config_data, freezer, gate):
    # Start with the advice closed (outdoor 26 C). Each gate then changes, and outdoor drops to 18 C to open the advice.
    # Only the no-gate case opens the window.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    calls = _window_actuator_fakes(hass)
    extra = {"window_actuator": "cover.test_window", "lock_pin": "1234"}
    if gate == "control_off":
        extra["control"] = False
    entry, _ = await setup_heater(hass, config_data, outdoor="26.0", room="26.0", extra=extra)
    if gate == "house_away":
        await hass.services.async_call("hvac_horizon", "set_house_mode", {"mode": "away"}, blocking=True)
    if gate == "locked":
        await hass.services.async_call("hvac_horizon", "set_lock", {"entry_id": entry.entry_id, "locked": True, "pin": "1234"}, blocking=True)
    if gate == "stalled":
        # The room sensors stop reporting for 16 minutes, so they read as stalled when the advice turns open.
        freezer.tick(timedelta(minutes=16))
        hass.states.async_set(OUTDOOR, "18.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(OUTDOOR, "18.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, entry)
    assert calls == ([("open", "cover.test_window")] if gate == "none" else [])


@pytest.mark.parametrize("gate", ["locked", "stalled"])
async def test_window_actuator_closes_after_a_gate_closes_when_the_advice_ends(hass, enable_custom_integrations, config_data, freezer, gate):
    # Outdoor 18 C opens the window. A gate then applies while the advice still says open, so the window stays open.
    # When outdoor reaches 26 C the advice ends, and the close is sent although the gate still applies.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    calls = _window_actuator_fakes(hass)
    extra = {"window_actuator": "cover.test_window", "lock_pin": "1234"}
    entry, _ = await setup_heater(hass, config_data, outdoor="18.0", room="26.0", extra=extra)
    assert calls == [("open", "cover.test_window")]
    if gate == "locked":
        await hass.services.async_call("hvac_horizon", "set_lock", {"entry_id": entry.entry_id, "locked": True, "pin": "1234"}, blocking=True)
    if gate == "stalled":
        freezer.tick(timedelta(minutes=16))
        hass.states.async_set(OUTDOOR, "18.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, entry)
    assert calls == [("open", "cover.test_window")]
    hass.states.async_set(OUTDOOR, "26.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, entry)
    assert entry.runtime_data.data["window_advice"] == "close"
    assert calls == [("open", "cover.test_window"), ("close", "cover.test_window")]


async def test_house_away_closes_a_window_the_advice_still_opens(hass, enable_custom_integrations, config_data, freezer):
    # Outdoor 18 C opens the window and the advice stays open. House away ends the open advice, so the close is sent.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    calls = _window_actuator_fakes(hass)
    entry, _ = await setup_heater(hass, config_data, outdoor="18.0", room="28.0", extra={"window_actuator": "cover.test_window"})
    assert calls == [("open", "cover.test_window")]
    await hass.services.async_call("hvac_horizon", "set_house_mode", {"mode": "away"}, blocking=True)
    await refresh(hass, entry)
    assert entry.runtime_data.data["window_advice"] == "open"
    assert calls == [("open", "cover.test_window"), ("close", "cover.test_window")]


async def test_inhibit_window_blocks_the_window_open_until_it_ends(hass, enable_custom_integrations, config_data, freezer):
    # Outdoor 26 C keeps the advice closed. A manual change arms the inhibit, then outdoor drops to 18 C and the advice says open.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    calls = _window_actuator_fakes(hass)
    entry, _ = await setup_heater(hass, config_data, outdoor="26.0", room="26.0", extra={"window_actuator": "cover.test_window"})
    coordinator = entry.runtime_data
    coordinator._inhibit_until_min = dt_util.utcnow().timestamp() / 60.0 + 30.0
    hass.states.async_set(OUTDOOR, "18.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, entry)
    assert coordinator.data["window_advice"] == "open"
    assert calls == [], "no open command inside the inhibit window"
    # The inhibit ends after 30 minutes. The sensors report again, so the stall gate does not block the open.
    freezer.tick(timedelta(minutes=31))
    set_room(hass, "26.0")
    hass.states.async_set(OUTDOOR, "18.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, entry)
    assert calls == [("open", "cover.test_window")]


async def test_inhibit_window_still_allows_the_window_close(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    calls = _window_actuator_fakes(hass)
    entry, _ = await setup_heater(hass, config_data, outdoor="18.0", room="26.0", extra={"window_actuator": "cover.test_window"})
    assert calls == [("open", "cover.test_window")]
    entry.runtime_data._inhibit_until_min = dt_util.utcnow().timestamp() / 60.0 + 30.0
    hass.states.async_set(OUTDOOR, "26.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, entry)
    assert entry.runtime_data.data["window_advice"] == "close"
    assert calls == [("open", "cover.test_window"), ("close", "cover.test_window")]


PEER_ROOM = "sensor.peer_room"
PEER_CLIMATE = "climate.peer_ac"
PEER_VALVE = "number.peer_valve"


async def _setup_peer_pair(hass, config_data, *, requester_room: str, requester_extra: dict | None = None):
    """Entry A (the requester, in ROOM_A and ROOM_B) and entry B (the peer, its own room and valve).

    A sets peer_entry to B. The valve number records set_value calls and moves its state, so repeat writes can show.
    Returns the two entries and the list of (entity_id, value) valve writes.
    """
    set_inputs(hass)
    _register_climate_fakes(hass)
    writes = []

    def record_valve(call):
        writes.append((call.data["entity_id"], call.data["value"]))
        hass.states.async_set(call.data["entity_id"], str(call.data["value"]))

    hass.services.async_register("number", "set_value", record_valve)
    hass.states.async_set(PEER_VALVE, "0")
    hass.states.async_set(PEER_CLIMATE, "off", {"temperature": 24.0, "friendly_name": "Peer AC", "target_temp_step": 1.0})
    hass.states.async_set(PEER_ROOM, "24.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    for entity in (ROOM_A, ROOM_B):
        hass.states.async_set(entity, requester_room, {"unit_of_measurement": "°C", "device_class": "temperature"})
    peer_data = dict(config_data, climate=PEER_CLIMATE, room_sensors=[PEER_ROOM], valve=PEER_VALVE, comfort_c=24.0, control=True)
    peer_data.pop(CONF_PRICE)
    peer = MockConfigEntry(domain=DOMAIN, title="Peer AC", data=peer_data, unique_id=PEER_CLIMATE)
    peer.add_to_hass(hass)
    assert await hass.config_entries.async_setup(peer.entry_id)
    await hass.async_block_till_done()
    requester_data = dict(config_data, peer_entry=peer.entry_id, **(requester_extra or {}))
    requester_data.pop(CONF_PRICE)
    requester = MockConfigEntry(domain=DOMAIN, title="Test AC", data=requester_data, unique_id=CLIMATE)
    requester.add_to_hass(hass)
    assert await hass.config_entries.async_setup(requester.entry_id)
    await hass.async_block_till_done()
    return requester, peer, writes


async def test_warmer_room_opens_the_peer_valve_fully(hass, enable_custom_integrations, config_data, freezer):
    # Room A 26.1 C, peer room 24.0 C: 2.1 K warmer, so the peer valve gets 100. The peer's own opening is 0 at 24 C.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    requester, peer, writes = await _setup_peer_pair(hass, config_data, requester_room="26.0")
    writes.clear()
    await refresh(hass, requester)
    await refresh(hass, peer)
    assert (PEER_VALVE, 100.0) in writes
    assert peer.runtime_data.data["valve_opening_pct"] == pytest.approx(100.0)


async def test_room_not_warmer_by_2_k_leaves_the_peer_valve_alone(hass, enable_custom_integrations, config_data, freezer):
    # Room A 25.0 C, peer room 24.0 C: 1 K warmer, so no request. The peer's own opening is 0, so it writes nothing.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    requester, peer, writes = await _setup_peer_pair(hass, config_data, requester_room="25.0")
    writes.clear()
    await refresh(hass, requester)
    await refresh(hass, peer)
    assert all(value != 100.0 for _, value in writes)
    assert writes == []


async def test_peer_request_ends_when_the_warm_room_cools(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    requester, peer, writes = await _setup_peer_pair(hass, config_data, requester_room="26.0")
    await refresh(hass, requester)
    await refresh(hass, peer)
    assert peer.runtime_data.data["valve_opening_pct"] == pytest.approx(100.0)
    for entity in (ROOM_A, ROOM_B):
        hass.states.async_set(entity, "24.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, requester)
    await refresh(hass, peer)
    assert peer.runtime_data.data["valve_opening_pct"] == pytest.approx(0.0)
    assert hass.states.get(PEER_VALVE).state == "0.0"


async def test_unloading_the_warm_room_withdraws_its_request(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    requester, peer, writes = await _setup_peer_pair(hass, config_data, requester_room="26.0")
    await refresh(hass, requester)
    assert await hass.config_entries.async_unload(requester.entry_id)
    await hass.async_block_till_done()
    # The valve reads 0 again, so a leftover request would write 100 on the peer's next tick.
    hass.states.async_set(PEER_VALVE, "0")
    writes.clear()
    await refresh(hass, peer)
    assert all(value != 100.0 for _, value in writes)


async def test_pid_algorithm_publishes_no_tpi_output(hass, enable_custom_integrations, config_data):
    # Same inputs as the TPI test. With heater_algorithm "pid" the PID sets the output and tpi_output_pct stays unset.
    extra = {"comfort_c": 24.0, "heater_algorithm": "pid"}
    entry, _ = await setup_heater(hass, config_data, outdoor="5.0", room="20.0", extra=extra)
    coordinator = entry.runtime_data
    await refresh(hass, entry)
    assert "tpi_output_pct" not in coordinator.data
    assert 0.0 <= coordinator.data["heater_pid_output"] <= 100.0
    assert coordinator.data["heater_output_pct"] == pytest.approx(coordinator.data["heater_pid_output"], abs=0.01)


async def test_accuracy_pairs_and_forecast_error_follow_the_backtest(hass, enable_custom_integrations, config_data, monkeypatch):
    # Known runs: the backtest is stubbed to return them, and the refit takes the real median of their errors.
    runs = [
        BacktestRun(0, 40.0, None, None),
        BacktestRun(10, 40.0, 50.0, 10.0),
        BacktestRun(20, 30.0, None, None),
        BacktestRun(30, 60.0, 52.0, -8.0),
        BacktestRun(40, 25.0, 31.0, 6.0),
    ]
    monkeypatch.setattr(coordinator_learning_module, "backtest", lambda samples, target_c, min_train, margin_c: runs)
    monkeypatch.setattr(coordinator_learning_module, "has_enough_cycles", lambda samples: True)
    monkeypatch.setattr(coordinator_learning_module, "fit", lambda samples: Fit(k=0.4, c=1.5, g=0.0))
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    coordinator._refit()
    await refresh(hass, entry)
    state = hass.states.get("sensor.hvac_horizon_test_ac_forecast_error")
    assert state is not None
    expected = [
        {"predicted_min": 50.0, "measured_min": 40.0, "error_min": 10.0},
        {"predicted_min": 52.0, "measured_min": 60.0, "error_min": -8.0},
        {"predicted_min": 31.0, "measured_min": 25.0, "error_min": 6.0},
    ]
    assert state.attributes["accuracy_pairs"] == expected
    assert float(state.state) == pytest.approx(8.0)
    assert statistics.median(abs(p["error_min"]) for p in state.attributes["accuracy_pairs"]) == float(state.state)


async def test_accuracy_view_returns_the_pairs_and_median_to_a_signed_in_user(hass, hass_client, hass_client_no_auth, enable_custom_integrations, config_data, monkeypatch):
    # The same known runs as the forecast error test: three pairs, median absolute error 8 minutes.
    runs = [
        BacktestRun(0, 40.0, None, None),
        BacktestRun(10, 40.0, 50.0, 10.0),
        BacktestRun(20, 30.0, None, None),
        BacktestRun(30, 60.0, 52.0, -8.0),
        BacktestRun(40, 25.0, 31.0, 6.0),
    ]
    monkeypatch.setattr(coordinator_learning_module, "backtest", lambda samples, target_c, min_train, margin_c: runs)
    monkeypatch.setattr(coordinator_learning_module, "has_enough_cycles", lambda samples: True)
    monkeypatch.setattr(coordinator_learning_module, "fit", lambda samples: Fit(k=0.4, c=1.5, g=0.0))
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entry.runtime_data._refit()
    await refresh(hass, entry)

    url = f"/api/hvac_horizon/accuracy/{entry.entry_id}"
    client = await hass_client()
    response = await client.get(url)
    assert response.status == 200
    body = await response.json()
    assert body["accuracy_pairs"] == [
        {"predicted_min": 50.0, "measured_min": 40.0, "error_min": 10.0},
        {"predicted_min": 52.0, "measured_min": 60.0, "error_min": -8.0},
        {"predicted_min": 31.0, "measured_min": 25.0, "error_min": 6.0},
    ]
    assert body["median_abs_error_min"] == pytest.approx(8.0)
    assert (await client.get("/api/hvac_horizon/accuracy/no-such-entry")).status == 404

    anonymous = await hass_client_no_auth()
    assert (await anonymous.get(url)).status == 401
    page = await anonymous.get("/hvac_horizon/accuracy.html")
    assert page.status == 200
    assert "Forecast accuracy" in await page.text()


async def test_heater_algorithm_defaults_to_pid_when_the_key_is_absent(hass, enable_custom_integrations, config_data):
    # An entry stored before heater_algorithm existed has no key. It runs the PID.
    entry, _ = await setup_heater(hass, config_data, outdoor="5.0", room="20.0", extra={"comfort_c": 24.0})
    coordinator = entry.runtime_data
    await refresh(hass, entry)
    assert "tpi_output_pct" not in coordinator.data
    assert "heater_pid_output" in coordinator.data


async def test_version_1_default_eco_of_25_is_dropped_on_migration(hass, enable_custom_integrations, config_data, freezer):
    # Version 1 stored eco = 25 C as the form default. Migration drops it, so an empty house uses away_c.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    hass.states.async_set("input_boolean.home", "off")
    data = dict(config_data, comfort_c=24.0, eco_c=25.0, away_c=27.0, presence=["input_boolean.home"], away_delay_min=0.0)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", version=1, data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.version == 2
    assert CONF_ECO_C not in entry.data
    await refresh(hass, entry)
    assert entry.runtime_data.data["cool_on_above_c"] == pytest.approx(27.0)


async def test_version_1_default_eco_is_dropped_whatever_the_comfort(hass, enable_custom_integrations, config_data, freezer):
    # The old default was 25 C, not comfort + 1. A comfort of 22 C with the stored 25 C default is dropped too.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    data = dict(config_data, comfort_c=22.0, eco_c=25.0)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", version=1, data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.version == 2
    assert CONF_ECO_C not in entry.data


async def test_version_1_eco_one_above_a_comfort_of_23_is_kept(hass, enable_custom_integrations, config_data, freezer):
    # 24 C is comfort + 1 at comfort 23, but the old default never followed comfort. The user set this value.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    data = dict(config_data, comfort_c=23.0, eco_c=24.0)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", version=1, data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.version == 2
    assert entry.data[CONF_ECO_C] == 24.0


async def test_version_1_user_eco_is_kept_on_migration(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    data = dict(config_data, comfort_c=24.0, eco_c=26.0)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", version=1, data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.version == 2
    assert entry.data[CONF_ECO_C] == 26.0


def test_every_conf_key_is_in_the_flow_schema():
    import re
    from pathlib import Path

    from custom_components.hvac_horizon import config_flow

    text = Path(config_flow.__file__).with_name("const.py").read_text()
    values = {m.group(2) for m in re.finditer(r'^(CONF_[A-Z0-9_]+)\s*=\s*"([^"]+)"', text, re.M)}
    schema_keys = {str(k) for k in config_flow._schema({}).schema}
    missing = sorted(values - schema_keys)
    assert not missing, f"CONF_* keys with no config flow field: {missing}"


async def enable_sensors(hass, entry, *keys):
    """Enable status sensors that are disabled by default, then reload the entry so they are added."""
    registry = er.async_get(hass)
    for key in keys:
        eid = registry.async_get_entity_id(sensor.DOMAIN, DOMAIN, f"{entry.entry_id}_{key}")
        assert eid is not None, key
        if registry.async_get(eid).disabled:
            registry.async_update_entity(eid, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


async def test_status_sensors_exist_with_control_off(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await enable_sensors(hass, entry, "band_action")  # disabled by default
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    for key in ("target_c", "preset", "day_type", "band_action"):
        eid = registry.async_get_entity_id(sensor.DOMAIN, DOMAIN, f"{entry.entry_id}_{key}")
        assert eid is not None, key
        assert eid.startswith("sensor.hvac_horizon_"), eid
        state = hass.states.get(eid)
        assert state is not None and state.state not in ("unavailable", "unknown"), (key, state)
    target = hass.states.get(registry.async_get_entity_id(sensor.DOMAIN, DOMAIN, f"{entry.entry_id}_target_c"))
    assert float(target.state) == 24.0, target.state


async def test_sensor_entities_split_into_primary_diagnostic_and_disabled(hass, enable_custom_integrations, config_data):
    from homeassistant.const import EntityCategory

    from custom_components.hvac_horizon.sensor import DIAGNOSTIC_DISABLED_KEYS, DIAGNOSTIC_ENABLED_KEYS, PRIMARY_KEYS

    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    sensors = [e for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id) if e.domain == sensor.DOMAIN]
    prefix = f"{entry.entry_id}_"
    primary = {e.unique_id.removeprefix(prefix) for e in sensors if e.entity_category is None and not e.disabled}
    diagnostic = {e.unique_id.removeprefix(prefix) for e in sensors if e.entity_category is EntityCategory.DIAGNOSTIC and not e.disabled}
    disabled = {e.unique_id.removeprefix(prefix) for e in sensors if e.entity_category is EntityCategory.DIAGNOSTIC and e.disabled}
    assert primary == PRIMARY_KEYS and len(primary) == 11
    assert diagnostic == DIAGNOSTIC_ENABLED_KEYS and len(diagnostic) == 28
    assert disabled == DIAGNOSTIC_DISABLED_KEYS and len(disabled) == 36
    assert len(sensors) == 75
    assert not any(e.disabled for e in sensors if e.entity_category is None)


async def test_device_echo_of_rounded_setpoint_is_not_a_manual_change(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    hass.services.async_register("climate", "set_hvac_mode", lambda call: None)
    written = []
    hass.services.async_register("climate", "set_temperature", lambda call: written.append(call.data["temperature"]))
    # Comfort 24.26 plus the price shift gives about 24.30, which the 1.0 C step device stores as 24.0.
    hass.states.async_set(CLIMATE, "cool", {"temperature": 22.0, "target_temp_step": 1.0, "friendly_name": "Test AC"})
    data = dict(config_data, control=True, comfort_c=24.26)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    # The setup refresh wrote the rounded setpoint. The device echoes it at its 1.0 C step.
    assert written == [24.0]
    hass.states.async_set(CLIMATE, "cool", {"temperature": 24.0, "target_temp_step": 1.0, "friendly_name": "Test AC"})
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert written == [24.0]
    assert not coordinator.data["action"].startswith("inhibited")


async def test_ac_own_reading_is_not_a_room_reading(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    hass.states.async_set(ROOM_A, "20.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(ROOM_B, "20.2", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(CLIMATE, "cool", {"temperature": 24.0, "current_temperature": 30, "friendly_name": "Test AC"})
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert sorted(entry.runtime_data._rooms()) == [20.0, 20.2]


async def test_flat_keys_set_up_as_one_room(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    config_data[CONF_HEATER_SWITCH] = "switch.test_heater"
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert room_devices(entry.data)["heating"] == ["switch.test_heater"]


HEATER = "switch.test_heater"
WINDOW = "binary_sensor.test_window"


async def setup_heater(hass, config_data, *, heater_state="off", outdoor="10.0", room="20.0", extra=None, climate="off", temperature_handler=None):
    """Set up a control-on entry with a heater switch and a cold room. The switch and climate services are fakes that record calls."""
    set_inputs(hass)
    hass.states.async_set(CLIMATE, climate, {"temperature": 22.0, "friendly_name": "Test AC", "target_temp_step": 1.0})
    hass.states.async_set(ROOM_A, room, {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(ROOM_B, room, {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(OUTDOOR, outdoor, {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(HEATER, heater_state)
    calls = []

    async def record_on(call):
        calls.append("turn_on")

    async def record_off(call):
        calls.append("turn_off")

    async def ignore(call):
        return None

    hass.services.async_register("switch", "turn_on", record_on)
    hass.services.async_register("switch", "turn_off", record_off)
    hass.services.async_register("climate", "set_hvac_mode", ignore)
    hass.services.async_register("climate", "set_temperature", temperature_handler or ignore)
    data = {**config_data, CONF_HEATER_SWITCH: HEATER, "control": True, **(extra or {})}
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, calls


async def refresh(hass, entry):
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_heater_turns_on_when_room_is_below_threshold(hass, enable_custom_integrations, config_data):
    entry, calls = await setup_heater(hass, config_data)
    await refresh(hass, entry)
    assert "turn_on" in calls
    assert "turn_off" not in calls
    assert entry.runtime_data.data["heater_pwm_on"] is True


async def test_heater_turns_off_with_window_open(hass, enable_custom_integrations, config_data):
    hass.states.async_set(WINDOW, "on")
    entry, calls = await setup_heater(hass, config_data, heater_state="on", extra={"window": WINDOW})
    await refresh(hass, entry)
    assert "turn_off" in calls
    assert "turn_on" not in calls
    assert entry.runtime_data.data["heater_pwm_on"] is False


async def test_heater_stays_off_inside_the_deadband(hass, enable_custom_integrations, config_data):
    # Comfort 24.0 C, deadband 0.5 C: 23.7 C is above the 23.5 C heat threshold.
    entry, calls = await setup_heater(hass, config_data, heater_state="on", room="23.7")
    await refresh(hass, entry)
    assert "turn_on" not in calls
    assert "turn_off" in calls


async def test_heater_and_ac_are_never_on_together(hass, enable_custom_integrations, config_data):
    entry, calls = await setup_heater(hass, config_data, climate="cool")
    await refresh(hass, entry)
    assert "turn_on" not in calls
    assert entry.runtime_data.data["heater_pwm_on"] is False


async def test_heater_turns_off_when_heating_not_allowed(hass, enable_custom_integrations, config_data):
    entry, calls = await setup_heater(hass, config_data, heater_state="on", outdoor="30.0")
    await refresh(hass, entry)
    assert entry.runtime_data.data["heating_allowed"] is False
    assert "turn_off" in calls
    assert "turn_on" not in calls


async def test_heater_turns_off_when_house_is_away(hass, enable_custom_integrations, config_data):
    entry, calls = await setup_heater(hass, config_data)
    await hass.services.async_call(DOMAIN, "set_house_mode", {"mode": "away"}, blocking=True)
    await hass.async_block_till_done()
    calls.clear()
    hass.states.async_set(HEATER, "on")
    await refresh(hass, entry)
    assert "turn_off" in calls
    assert "turn_on" not in calls


async def test_heater_turns_off_when_locked(hass, enable_custom_integrations, config_data):
    entry, calls = await setup_heater(hass, config_data, extra={"lock_pin": "1234"})
    assert entry.runtime_data.set_lock(True, None) is True
    hass.states.async_set(HEATER, "on")
    calls.clear()
    await refresh(hass, entry)
    assert "turn_off" in calls
    assert "turn_on" not in calls


async def test_heater_turns_off_when_a_sensor_is_stalled(hass, enable_custom_integrations, config_data, monkeypatch):
    from custom_components.hvac_horizon.coordinator import HorizonCoordinator

    monkeypatch.setattr(HorizonCoordinator, "_stalled_sensors", lambda self, now: [ROOM_A, ROOM_B])
    entry, calls = await setup_heater(hass, config_data, heater_state="on")
    await refresh(hass, entry)
    assert "turn_off" in calls
    assert "turn_on" not in calls
    assert entry.runtime_data.data["safety_output_pct"] == 0.0


async def test_heater_safety_step_runs_when_a_window_actuator_raises(hass, enable_custom_integrations, config_data, freezer):
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers.event import async_call_later

    freezer.move_to("2026-11-20T10:00:00+00:00")

    async def failing_open(call):
        raise HomeAssistantError("cover offline")

    hass.services.async_register("cover", "close_cover", failing_open)
    hass.states.async_set("cover.test_window", "unavailable")
    entry, calls = await setup_heater(hass, config_data, outdoor="18.0", room="26.0", extra={"window_actuator": "cover.test_window"})
    coordinator = entry.runtime_data
    coordinator._unschedule_refresh()
    # A heater that is on with a live PWM timer, as in the middle of a window.
    hass.states.async_set(HEATER, "on")
    coordinator._heater_gated = False
    coordinator._heater_commanded_on = True
    coordinator._pwm_cancel.append(async_call_later(hass, 120, coordinator._pwm_timer))
    # The heater is on, so the open advice has ended. The cover reads open and this integration opened it, so the actuator step calls close_cover and raises.
    coordinator._actuator_opened_by_us = True
    hass.states.async_set("cover.test_window", "open")
    calls.clear()
    await refresh(hass, entry)
    assert coordinator.last_update_success is False
    assert "turn_off" in calls
    assert "turn_on" not in calls
    assert coordinator._pwm_cancel == []
    assert coordinator._heater_gated is True


async def test_locked_entry_stops_a_cooling_ac_on_a_stall(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data, room="26.0", climate="cool", outdoor="30.0", extra={"lock_pin": "1234"})
    modes = []

    async def record_mode(call):
        modes.append(call.data["hvac_mode"])

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    assert entry.runtime_data.set_lock(True, None) is True
    # Both room sensors report nothing for longer than the stall window.
    freezer.tick(timedelta(minutes=20))
    hass.states.async_set(OUTDOOR, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, entry)
    assert modes == ["off"]
    assert entry.runtime_data.data["action"].startswith("locked")


async def test_locked_entry_without_a_stop_condition_sends_no_climate_command(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data, room="26.0", climate="cool", outdoor="30.0", extra={"lock_pin": "1234"})
    modes = []

    async def record_mode(call):
        modes.append(call.data["hvac_mode"])

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    assert entry.runtime_data.set_lock(True, None) is True
    await refresh(hass, entry)
    assert modes == []
    assert entry.runtime_data.data["action"] == "locked"


async def test_heater_switches_off_on_unload(hass, enable_custom_integrations, config_data):
    entry, calls = await setup_heater(hass, config_data)
    await refresh(hass, entry)
    hass.states.async_set(HEATER, "on")
    calls.clear()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert calls == ["turn_off"]


async def test_home_assistant_stop_turns_the_heater_off(hass, enable_custom_integrations, config_data):
    entry, calls = await setup_heater(hass, config_data)
    await refresh(hass, entry)
    hass.states.async_set(HEATER, "on")
    calls.clear()
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert calls == ["turn_off"]


async def test_home_assistant_stop_turns_a_running_fleet_device_off(hass, enable_custom_integrations, config_data):
    entry, calls = await _setup_fleet(hass, config_data, room="25.0", fleet=FLEET_AC_PAIR)
    hass.states.async_set(SMALL_AC, "cool", {"temperature": 24.0})
    calls.clear()
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert ("climate", "set_hvac_mode", SMALL_AC, "off") in calls


async def test_home_assistant_stop_turns_a_running_fleet_switch_off_while_locked(hass, enable_custom_integrations, config_data):
    fleet = [{"device_id": "ir", "kind": "heat", "entity_id": IR_HEATER, "rate_k_per_h_per_kw": 1.5, "rated_w": 1500.0}]
    entry, calls = await _setup_fleet(hass, config_data, room="25.0", fleet=fleet, pin="7391")
    entry.runtime_data.set_lock(True, "7391")
    hass.states.async_set(IR_HEATER, "on")
    calls.clear()
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert ("switch", "turn_off", IR_HEATER, None) in calls


async def test_unload_removes_the_stop_listener(hass, enable_custom_integrations, config_data):
    entry, calls = await setup_heater(hass, config_data)
    await refresh(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set(HEATER, "on")
    calls.clear()
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert calls == []


async def test_unload_turns_a_running_fleet_device_off(hass, enable_custom_integrations, config_data):
    entry, calls = await _setup_fleet(hass, config_data, room="25.0", fleet=FLEET_AC_PAIR)
    hass.states.async_set(SMALL_AC, "cool", {"temperature": 24.0})
    calls.clear()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert ("climate", "set_hvac_mode", SMALL_AC, "off") in calls


async def test_inverted_heater_is_commanded_with_inverted_polarity(hass, enable_custom_integrations, config_data):
    # With heater_inverted, "on" wiring means the heater is off, so a heating demand sends turn_off.
    entry, calls = await setup_heater(hass, config_data, heater_state="on", extra={"heater_inverted": True})
    await refresh(hass, entry)
    assert "turn_off" in calls
    assert "turn_on" not in calls


async def test_heater_feedback_is_set_after_one_driven_tick(hass, enable_custom_integrations, config_data):
    entry, calls = await setup_heater(hass, config_data)
    await refresh(hass, entry)
    assert entry.runtime_data.data["heater_feedback"] != "not_configured"
    assert "turn_on" in calls


async def test_heating_failure_publishes_and_raises_repair(hass, enable_custom_integrations, config_data):
    from homeassistant.helpers import issue_registry as ir

    entry, _ = await setup_heater(hass, config_data)
    coordinator = entry.runtime_data
    await refresh(hass, entry)
    assert coordinator.data["heating_failure"] is False
    coordinator._heater_on_min = 60.0
    await refresh(hass, entry)
    assert coordinator.data["heating_failure"] is True
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{entry.entry_id}_heating_failure") is not None
    hass.states.async_set(ROOM_A, "21.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(ROOM_B, "21.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, entry)
    assert coordinator.data["heating_failure"] is False
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{entry.entry_id}_heating_failure") is None


def test_reconfigure_schema_keeps_heater_inverted():
    from custom_components.hvac_horizon.config_flow import _schema

    for stored in (True, False):
        schema = _schema({"heater_inverted": stored})
        key = next(k for k in schema.schema if k == "heater_inverted")
        assert key.default() is stored


async def test_unload_turns_off_the_switch_it_drove_after_reconfigure(hass, enable_custom_integrations, config_data):
    entry, calls = await setup_heater(hass, config_data)
    await refresh(hass, entry)
    assert "turn_on" in calls
    hass.states.async_set(HEATER, "on")
    # Reconfigure with control off and no heater switch. Unload must still undo the last command.
    data = {k: v for k, v in entry.data.items() if k not in ("control", CONF_HEATER_SWITCH)}
    hass.config_entries.async_update_entry(entry, data=data)
    await hass.async_block_till_done()
    calls.clear()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert calls == ["turn_off"]


async def test_pwm_edges_follow_the_duty_between_ticks(hass, enable_custom_integrations, config_data, freezer):
    # 50 % of a 10 minute window: on at the window start, off after 5 minutes, next window at 10 minutes.
    entry, _ = await setup_heater(hass, config_data)
    coordinator = entry.runtime_data
    coordinator._unschedule_refresh()
    events = []

    async def record_on(call):
        events.append("on")
        hass.states.async_set(HEATER, "on")

    async def record_off(call):
        events.append("off")
        hass.states.async_set(HEATER, "off")

    hass.services.async_register("switch", "turn_on", record_on)
    hass.services.async_register("switch", "turn_off", record_off)
    freezer.move_to("2026-11-20T10:00:00+00:00")
    coordinator._heater_gated = False
    coordinator._pwm_pct_now = 50.0
    coordinator._pwm_step(dt_util.utcnow().timestamp())
    await coordinator._drive_heater_switch()
    await hass.async_block_till_done()
    assert events == ["on"]

    async def advance(seconds):
        freezer.tick(timedelta(seconds=seconds))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()

    await advance(299)
    assert events == ["on"]
    await advance(1)
    assert events == ["on", "off"]
    await advance(299)
    assert events == ["on", "off"]
    await advance(1)
    assert events == ["on", "off", "on"]


async def test_pwm_gate_and_zero_output_cancel_the_timers(hass, enable_custom_integrations, config_data):
    entry, _ = await setup_heater(hass, config_data)
    coordinator = entry.runtime_data
    coordinator._unschedule_refresh()
    now = dt_util.utcnow().timestamp()
    coordinator._heater_gated = False
    coordinator._pwm_pct_now = 50.0
    coordinator._pwm_step(now)
    assert len(coordinator._pwm_cancel) == 2
    coordinator._heater_gated = True
    coordinator._pwm_step(now + 10)
    assert coordinator._pwm_cancel == []
    assert coordinator._pwm_on_now is False
    coordinator._heater_gated = False
    coordinator._pwm_pct_now = 100.0
    coordinator._pwm_step(now + 20)
    assert coordinator._pwm_cancel == []
    assert coordinator._pwm_on_now is True


async def test_stale_heater_output_is_zeroed_when_outdoor_is_missing(hass, enable_custom_integrations, config_data):
    entry, calls = await setup_heater(hass, config_data, heater_state="on")
    coordinator = entry.runtime_data
    coordinator.set_heater_output(40.0)
    hass.states.async_set(OUTDOOR, "unavailable")
    calls.clear()
    await refresh(hass, entry)
    assert "turn_on" not in calls
    assert "turn_off" in calls


async def test_heater_output_is_held_during_inhibit(hass, enable_custom_integrations, config_data):
    entry, _ = await setup_heater(hass, config_data)
    coordinator = entry.runtime_data
    coordinator.set_heater_output(40.0)
    coordinator._inhibit_until_min = dt_util.utcnow().timestamp() / 60.0 + 60.0
    await refresh(hass, entry)
    assert coordinator.data["heater_output_pct"] == 40.0


async def test_inhibit_window_keeps_the_heater_switch_off(hass, enable_custom_integrations, config_data, freezer):
    # The held output is 40 %, so the PWM window is on. The inhibit must stop the turn_on.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, calls = await setup_heater(hass, config_data, outdoor="10.0", room="20.0")
    coordinator = entry.runtime_data
    calls.clear()
    hass.states.async_set(HEATER, "off")
    coordinator._inhibit_until_min = dt_util.utcnow().timestamp() / 60.0 + 30.0
    coordinator.set_heater_output(40.0)
    await refresh(hass, entry)
    assert "turn_on" not in calls


async def test_closed_gate_still_turns_the_heater_switch_off_during_inhibit(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, calls = await setup_heater(hass, config_data, outdoor="10.0", room="20.0", heater_state="on")
    coordinator = entry.runtime_data
    calls.clear()
    coordinator._inhibit_until_min = dt_util.utcnow().timestamp() / 60.0 + 30.0
    coordinator.set_heater_output(40.0)
    await hass.services.async_call(DOMAIN, "set_house_mode", {"mode": "away"}, blocking=True)
    await refresh(hass, entry)
    assert coordinator._heater_gated
    assert "turn_off" in calls


async def test_manual_change_inhibit_blocks_heat_on_but_not_the_room_cutoff(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, calls = await setup_heater(hass, config_data, outdoor="10.0", room="20.0", heater_state="off")
    coordinator = entry.runtime_data
    coordinator._unschedule_refresh()
    # A manual AC setpoint change arms the inhibit. The coordinator last wrote 24 C, and the device now reports 22 C.
    coordinator._last_written_setpoint = 24.0
    hass.states.async_set(CLIMATE, "off", {"temperature": 22.0, "friendly_name": "Test AC", "target_temp_step": 1.0})
    coordinator.set_heater_output(40.0)
    calls.clear()
    await refresh(hass, entry)
    assert coordinator.data["action"] == "inhibited after a manual change"
    assert coordinator.data["heater_pwm_on"] is True
    assert "turn_on" not in calls, "heat on must wait out the inhibit window"

    # The heater runs. The room passes the heat threshold inside the window, so the output drops to 0.
    hass.states.async_set(HEATER, "on")
    hass.states.async_set(ROOM_A, "25.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(ROOM_B, "25.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    calls.clear()
    await refresh(hass, entry)
    assert coordinator.data["heater_pwm_on"] is False
    assert "turn_off" in calls
    assert "turn_on" not in calls


async def test_inhibit_still_sends_the_heater_off_edge_of_the_pwm_window(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, calls = await setup_heater(hass, config_data, outdoor="10.0", room="20.0", heater_state="on")
    coordinator = entry.runtime_data
    coordinator._unschedule_refresh()
    calls.clear()
    coordinator._inhibit_until_min = dt_util.utcnow().timestamp() / 60.0 + 30.0
    coordinator._heater_gated = False
    coordinator._pwm_pct_now = 30.0
    coordinator._pwm_step(dt_util.utcnow().timestamp())
    await coordinator._drive_heater_switch()
    await hass.async_block_till_done()
    assert calls == []  # the heater is already on, so the on edge sends nothing

    window_s = PWM_WINDOW_MIN * 60.0
    freezer.tick(timedelta(seconds=0.3 * window_s))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert coordinator._pwm_on_now is False
    assert calls == ["turn_off"]
    assert "turn_on" not in calls


# The AC cools above these values. Eco and boost use the setpoints set in the entry; comfort is 24 C.
COOL_ABOVE_BY_PRESET = {"comfort": 24.0, "eco": 26.0, "boost": 27.0, "away": 27.0, "vacation": 27.0}
# Heat threshold per preset: heat target minus the 0.5 K deadband. Eco heats to comfort minus 1 (23 C) when heat_eco_c
# is unset. Boost heats to comfort plus 1 (25 C) when heat_boost_c is unset.
# Away and vacation heat only to the 16 C frost floor. Eco at home never heats to its 26 C cooling setpoint.
HEAT_ON_BY_PRESET = {"comfort": 23.5, "eco": 22.5, "boost": 24.5, "away": 15.5, "vacation": 15.5}


@pytest.mark.parametrize("scenario", ["comfort", "eco", "boost", "away", "vacation"])
async def test_heater_threshold_is_below_ac_threshold_on_every_preset(hass, enable_custom_integrations, config_data, scenario):
    # Boost cools above 27 C here. Its default heat setpoint is 25 C, so the heat threshold stays below the cool threshold.
    extra = {CONF_ECO_C: 26.0, CONF_BOOST_C: 27.0}
    if scenario == "vacation":
        extra[CONF_VACATION] = "input_boolean.vacation"
    entry, _ = await setup_heater(hass, config_data, extra=extra)
    # A price midway between the cheap and expensive limits leaves the comfort target unshifted at 24.0 C.
    hass.states.async_set(PRICE, "0.275", hass.states.get(PRICE).attributes)
    if scenario == "vacation":
        hass.states.async_set("input_boolean.vacation", "on")
    elif scenario != "comfort":
        await hass.services.async_call(DOMAIN, "start_timed_preset", {"entry_id": entry.entry_id, "preset": scenario, "minutes": 60})
    await refresh(hass, entry)
    heat_on = entry.runtime_data.data["heat_on_below_c"]
    cool_on = entry.runtime_data.data["cool_on_above_c"]
    assert heat_on < cool_on
    assert cool_on == pytest.approx(COOL_ABOVE_BY_PRESET[scenario])
    assert heat_on == pytest.approx(HEAT_ON_BY_PRESET[scenario])


async def test_eco_at_home_never_sends_the_eco_setpoint_as_heat(hass, enable_custom_integrations, config_data, freezer):
    # Eco at home cools above 26 C, but a 23 C room heats the AC to the heat threshold (23.5 C), never to 26 C.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    sent = []

    async def record_temperature(call):
        # The fake AC reports the setpoint it received, so the next tick does not read our write as a manual change.
        sent.append(call.data["temperature"])
        hass.states.async_set(CLIMATE, "off", {"temperature": call.data["temperature"], "target_temp_step": 1.0, "friendly_name": "Test AC"})

    entry, _ = await setup_heater(
        hass, config_data, room="23.0", extra={CONF_ECO_C: 26.0, CONF_HEATER_SWITCH: None}, temperature_handler=record_temperature
    )
    await refresh(hass, entry)
    sent.clear()
    freezer.tick(timedelta(minutes=10))  # the minimum off time after the first tick has passed
    await hass.services.async_call(DOMAIN, "start_timed_preset", {"entry_id": entry.entry_id, "preset": "eco", "minutes": 60})
    await refresh(hass, entry)
    # The AC ends on a heat setpoint near 23.5 C. Old code left it on the 26 C eco cooling setpoint.
    assert all(temperature < 25.0 for temperature in sent)
    assert hass.states.get(CLIMATE).attributes["temperature"] < 25.0


async def test_empty_house_heater_stays_off_above_the_frost_floor(hass, enable_custom_integrations, config_data):
    # Nobody home: the heat target is the 16 C frost floor, so a 20 C room does not start the heater.
    hass.states.async_set(PRESENCE, "off")
    entry, calls = await setup_heater(hass, config_data, room="20.0", extra={CONF_PRESENCE: [PRESENCE], CONF_AWAY_DELAY_MIN: 0})
    calls.clear()
    await refresh(hass, entry)
    assert "turn_on" not in calls


async def test_empty_house_heater_runs_below_the_frost_floor(hass, enable_custom_integrations, config_data):
    # Nobody home and a 15 C room: below the 15.5 C frost threshold, so the heater switch turns on.
    hass.states.async_set(PRESENCE, "off")
    entry, calls = await setup_heater(hass, config_data, room="15.0", extra={CONF_PRESENCE: [PRESENCE], CONF_AWAY_DELAY_MIN: 0})
    calls.clear()
    await refresh(hass, entry)
    assert "turn_on" in calls


async def test_heat_target_never_exceeds_the_cool_threshold(hass, enable_custom_integrations, config_data):
    """A heat setpoint above the cooling threshold would run the heater and the AC in one band."""
    entry, _ = await setup_heater(hass, config_data, extra={CONF_ECO_C: 26.0, CONF_BOOST_C: 22.0, "heat_boost_c": 25.0})
    hass.states.async_set(PRICE, "0.275", hass.states.get(PRICE).attributes)
    await hass.services.async_call(DOMAIN, "start_timed_preset", {"entry_id": entry.entry_id, "preset": "boost", "minutes": 60})
    await refresh(hass, entry)
    cool_on = entry.runtime_data.data["cool_on_above_c"]
    heat_on = entry.runtime_data.data["heat_on_below_c"]
    # The room is cold and heating is allowed, so the heat side wins: boost_c lifts to heat_boost_c plus the deadband.
    assert cool_on == pytest.approx(25.5)
    assert heat_on <= cool_on - 0.5 + 1e-9


async def test_boost_turns_the_heater_switch_on_in_a_cold_room(hass, enable_custom_integrations, config_data):
    # Boost heats to comfort (24 C here, no boost_c set): the threshold is 23.5 C, so a 22.9 C room gets heat.
    entry, calls = await setup_heater(hass, config_data, room="22.9")
    await hass.services.async_call(DOMAIN, "start_timed_preset", {"entry_id": entry.entry_id, "preset": "boost", "minutes": 60})
    calls.clear()
    await refresh(hass, entry)
    assert "turn_on" in calls


async def test_window_pauses_control_only_after_the_delay(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    hass.states.async_set(WINDOW, "on")
    entry, _ = await setup_heater(hass, config_data, extra={"window": WINDOW})
    coordinator = entry.runtime_data
    coordinator._unschedule_refresh()
    # Default window delay is 10 minutes. The window opened at 10:00.
    freezer.tick(timedelta(minutes=9))
    await refresh(hass, entry)
    assert coordinator._window_paused is False
    freezer.tick(timedelta(minutes=2))
    await refresh(hass, entry)
    assert coordinator._window_paused is True


async def test_heater_and_threshold_sensors_publish_state(hass, enable_custom_integrations, config_data):
    entry, _ = await setup_heater(hass, config_data)
    await enable_sensors(hass, entry, "heater_pid_output", "backtest_runs")  # disabled by default
    await refresh(hass, entry)
    registry = er.async_get(hass)
    for key in ("heating_failure", "heater_pid_output", "cool_on_above_c", "heat_on_below_c", "backtest_runs"):
        eid = registry.async_get_entity_id(sensor.DOMAIN, DOMAIN, f"{entry.entry_id}_{key}")
        assert eid is not None, key
        state = hass.states.get(eid)
        assert state is not None and state.state not in ("unavailable", "unknown"), (key, state)
    status = hass.states.get(registry.async_get_entity_id(sensor.DOMAIN, DOMAIN, f"{entry.entry_id}_status"))
    # No relay test has run, so the attribute is present and empty.
    assert "autotune" in status.attributes and status.attributes["autotune"] is None


async def test_status_attributes_show_autotune_and_malformed_shed_entries(hass, enable_custom_integrations, config_data):
    from types import SimpleNamespace

    entry, _ = await setup_heater(
        hass,
        config_data,
        extra={"whole_house_power": POWER, "shed_max_w": 500, "shed_loads": ["switch.heater_x|not-a-number"]},
    )
    entry.runtime_data._autotune_result = SimpleNamespace(kp=1.25, ki=2.5, kd=0.5)
    await refresh(hass, entry)
    assert entry.runtime_data.data["shed_skipped"] == "malformed load: switch.heater_x|not-a-number"
    registry = er.async_get(hass)
    status = hass.states.get(registry.async_get_entity_id(sensor.DOMAIN, DOMAIN, f"{entry.entry_id}_status"))
    assert status.attributes["autotune"] == {"kp": 1.25, "ki": 2.5, "kd": 0.5}
    assert status.attributes["shed_skipped"] == "malformed load: switch.heater_x|not-a-number"


async def _reconfigure_sections(hass, entry, submitted):
    """Open the reconfigure menu and submit every section with its fields from `submitted`.

    A field missing from `submitted` is left out of its section's submit, so the flow clears it.
    Returns the result of the last section, which is the menu unless a section reported an error.
    """
    from custom_components.hvac_horizon import config_flow

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] == "menu" and result["step_id"] == "reconfigure"
    for name in config_flow.SECTIONS:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": name})
        assert result["type"] == "form" and result["step_id"] == name
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {k: v for k, v in submitted.items() if k in config_flow.SECTIONS[name]}
        )
        if result["type"] == "form":
            return result
    return result


async def _reconfigure(hass, entry, submitted):
    result = await _reconfigure_sections(hass, entry, submitted)
    assert result["type"] == "menu"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "finish"})
    await hass.async_block_till_done()
    assert result["type"] == "abort" and result["reason"] == "reconfigure_successful", result


async def test_reconfigure_edits_one_section_and_leaves_the_others(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data={**config_data, CONF_COMFORT_C: 22.0}, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "price"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"price_cheap": 0.1, "price_expensive": 0.5})
    assert result["type"] == "menu"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "finish"})
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["price_cheap"] == 0.1 and CONF_PRICE not in entry.data  # the price section was submitted without a sensor
    assert entry.data[CONF_COMFORT_C] == 22.0 and entry.data[CONF_ROOM_SENSORS] == [ROOM_A, ROOM_B]


async def test_reconfigure_clears_a_field_left_out_of_the_submit(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert CONF_PRICE in entry.data
    submitted = {k: v for k, v in config_data.items() if k != CONF_PRICE}
    await _reconfigure(hass, entry, submitted)
    assert CONF_PRICE not in entry.data


async def test_reconfigure_keeps_inverted_heater_when_the_submit_omits_it(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    data = {**config_data, CONF_HEATER_INVERTED: True}
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await _reconfigure(hass, entry, dict(config_data))
    assert entry.data[CONF_HEATER_INVERTED] is True


async def test_reconfigure_keeps_the_lock_pin_when_the_box_is_blank(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    data = {**config_data, CONF_LOCK_PIN: "4321"}
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await _reconfigure(hass, entry, dict(config_data))
    assert entry.data[CONF_LOCK_PIN] == "4321"


async def test_stored_schedule_block_sets_the_preset(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    blocks = [{"start_h": 0.0, "end_h": 24.0, "preset": "eco"}]
    data = {**config_data, CONF_SCHEDULE_BLOCKS: blocks, "control": True}
    _register_climate_fakes(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert entry.runtime_data.data["preset"] == "eco"


async def test_manual_setpoint_is_overwritten_after_the_inhibit_ends(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    sent = []

    async def ignore_mode(call):
        pass

    async def record_temp(call):
        # The fake device takes the value it is sent, as a real AC does.
        sent.append(call.data["temperature"])
        hass.states.async_set(CLIMATE, "cool", {"temperature": call.data["temperature"], "friendly_name": "Test AC", "target_temp_step": 0.5})

    hass.services.async_register("climate", "set_hvac_mode", ignore_mode)
    hass.services.async_register("climate", "set_temperature", record_temp)
    # The room is 26 C and comfort is 24 C. A device at 22 C forces the first write of 24 C.
    hass.states.async_set(CLIMATE, "cool", {"temperature": 22.0, "friendly_name": "Test AC", "target_temp_step": 0.5})
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data, control=True), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    coordinator._unschedule_refresh()
    await refresh(hass, entry)
    assert sent == [24.0]

    # Someone sets 22 C by hand. The next cycle starts the 30 minute inhibition.
    hass.states.async_set(CLIMATE, "cool", {"temperature": 22.0, "friendly_name": "Test AC", "target_temp_step": 0.5})
    await refresh(hass, entry)
    assert sent == [24.0]
    assert coordinator.data["action"] == "inhibited after a manual change"

    # Inside the 30 minute window nothing is sent, not even the keep-alive.
    # The sensors report at each step, so no stall stops the AC here.
    freezer.tick(timedelta(minutes=20))
    set_room(hass, "26.0")
    hass.states.async_set(OUTDOOR, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, entry)
    assert sent == [24.0]

    freezer.tick(timedelta(minutes=11))
    set_room(hass, "26.0")
    hass.states.async_set(OUTDOOR, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, entry)
    assert sent == [24.0, 24.0], "the coordinator should write its setpoint again after the inhibition"

    # A second manual change starts a new 30 minute inhibition.
    hass.states.async_set(CLIMATE, "cool", {"temperature": 22.0, "friendly_name": "Test AC", "target_temp_step": 0.5})
    await refresh(hass, entry)
    assert sent == [24.0, 24.0]
    assert coordinator.data["action"] == "inhibited after a manual change"


async def test_stalled_sensor_turns_the_ac_off_inside_the_inhibit_window(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    sent, modes = [], []

    async def record_mode(call):
        modes.append(call.data["hvac_mode"])

    async def record_temp(call):
        sent.append(call.data["temperature"])
        hass.states.async_set(CLIMATE, "cool", {"temperature": call.data["temperature"], "friendly_name": "Test AC", "target_temp_step": 0.5})

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    hass.services.async_register("climate", "set_temperature", record_temp)
    hass.states.async_set(CLIMATE, "cool", {"temperature": 22.0, "friendly_name": "Test AC", "target_temp_step": 0.5})
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data, control=True), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    coordinator._unschedule_refresh()
    await refresh(hass, entry)
    assert sent == [24.0]

    # Manual change to 22 C starts the 30 minute inhibition.
    hass.states.async_set(CLIMATE, "cool", {"temperature": 22.0, "friendly_name": "Test AC", "target_temp_step": 0.5})
    await refresh(hass, entry)
    assert coordinator.data["action"] == "inhibited after a manual change"

    # No sensor reports for 16 minutes. The inhibition still holds, and the stall must still switch the AC off.
    freezer.tick(timedelta(minutes=16))
    await refresh(hass, entry)
    assert coordinator.data["action"] != "inhibited after a manual change"
    assert ROOM_A in coordinator.data["sensors_stalled"]
    assert "off" in modes
    assert sent == [24.0], "the inhibit window must not send a setpoint"


async def test_reconfigure_rejects_a_malformed_schedule_list(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    blocks = [{"start_h": 0.0, "end_h": 24.0, "preset": "eco"}]
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data={**config_data, CONF_SCHEDULE_BLOCKS: blocks}, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    bad = {**config_data, CONF_SCHEDULE_BLOCKS: [{"start_h": 0.0, "end_h": 25.0, "preset": "eco"}]}
    result = await _reconfigure_sections(hass, entry, bad)
    assert result["type"] == "form" and result["step_id"] == "comfort"
    assert result["errors"] == {CONF_SCHEDULE_BLOCKS: "invalid_schedule_blocks"}
    assert entry.data[CONF_SCHEDULE_BLOCKS] == blocks


async def test_reconfigure_stores_a_valid_schedule_list(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    blocks = [{"start_h": 22.0, "end_h": 6.0, "preset": "eco"}]
    await _reconfigure(hass, entry, {**config_data, CONF_SCHEDULE_BLOCKS: blocks})
    assert entry.data[CONF_SCHEDULE_BLOCKS] == blocks


async def test_house_away_from_the_service_turns_the_heater_off(hass, enable_custom_integrations, config_data):
    entry, calls = await setup_heater(hass, config_data, heater_state="on")
    await refresh(hass, entry)
    calls.clear()
    await hass.services.async_call(DOMAIN, "set_house_mode", {"mode": "away"}, blocking=True)
    await refresh(hass, entry)
    assert calls and set(calls) == {"turn_off"}


async def test_cooling_in_the_group_keeps_another_entries_heater_off(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    other = "climate.test_ac_a"
    hass.states.async_set(other, "cool", {"temperature": 24.0, "friendly_name": "Test AC A"})
    # Entry A cools and only observes. Entry B has a heater, an AC that is off and a cold room, all in one group.
    entry_a = MockConfigEntry(
        domain=DOMAIN, title="Test AC A", data=dict(config_data, climate=other, compressor_group="garage"), unique_id=other
    )
    entry_a.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry_a.entry_id)
    await hass.async_block_till_done()
    entry_b, calls = await setup_heater(hass, config_data, extra={"compressor_group": "garage"})
    await refresh(hass, entry_b)
    assert entry_b.runtime_data.data["house_cooling"] is True
    assert "turn_on" not in calls
    # Entry A stops cooling. Entry B's heater may run now.
    hass.states.async_set(other, "off", {"temperature": 24.0, "friendly_name": "Test AC A"})
    await refresh(hass, entry_a)
    await refresh(hass, entry_b)
    assert entry_b.runtime_data.data["house_cooling"] is False
    assert "turn_on" in calls


@pytest.mark.parametrize(
    "gate",
    [
        {"extra": {"window": WINDOW}, "setup": lambda hass: hass.states.async_set(WINDOW, "on")},
        {"extra": {"lock_pin": "1234"}, "setup": lambda hass: None, "lock": True},
        {"extra": {"control": False}, "setup": lambda hass: None},
    ],
    ids=["window_open", "locked", "control_off"],
)
async def test_relay_test_does_not_command_the_heater_behind_a_closed_gate(hass, enable_custom_integrations, config_data, gate):
    gate["setup"](hass)
    entry, calls = await setup_heater(hass, config_data, extra=gate["extra"])
    if gate.get("lock"):
        assert entry.runtime_data.set_lock(True, None) is True
    entry.runtime_data.start_autotune(24.0)
    calls.clear()
    await refresh(hass, entry)
    assert "turn_on" not in calls


async def test_pwm_timer_does_not_command_the_heater_during_a_relay_test(hass, enable_custom_integrations, config_data):
    entry, calls = await setup_heater(hass, config_data)
    coordinator = entry.runtime_data
    coordinator.start_autotune(24.0)
    await refresh(hass, entry)
    calls.clear()
    await coordinator._pwm_timer(dt_util.utcnow())
    assert calls == []


def set_room(hass, temp: str) -> None:
    hass.states.async_set(ROOM_A, temp, {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(ROOM_B, temp, {"unit_of_measurement": "°C", "device_class": "temperature"})


async def test_relay_test_gains_reach_the_heater_pid_and_survive_restart(hass, enable_custom_integrations, config_data, freezer):
    import math

    freezer.move_to("2026-11-20T10:00:00+00:00")
    # The relay obeys the heat-on threshold, so the comfort setpoint sits above the 23 to 25 C swing.
    entry, _ = await setup_heater(hass, config_data, extra={"comfort_c": 28.0})
    coordinator = entry.runtime_data
    coordinator.start_autotune(24.0)
    # One reading per 5 minute cycle, a 1 K swing with a 30 minute period. The test completes inside the 120 minute limit.
    # The outdoor sensor is refreshed too: a sensor that stops updating for 15 minutes closes the heater gate.
    # Home Assistant keeps last_updated when neither the state nor an attribute changes, so the tick is an attribute.
    for tick in range(120):
        set_room(hass, f"{24.0 + math.sin(2 * math.pi * tick / 6):.3f}")
        hass.states.async_set(OUTDOOR, "10.0", {"unit_of_measurement": "°C", "device_class": "temperature", "tick": tick})
        freezer.tick(timedelta(minutes=5))
        await refresh(hass, entry)
        if coordinator._autotune_result is not None:
            break
    tuned = coordinator._autotune_result
    assert tuned is not None
    assert coordinator.data["autotune"] == {"kp": pytest.approx(tuned.kp, abs=1e-3), "ki": pytest.approx(tuned.ki, abs=1e-3), "kd": pytest.approx(tuned.kd, abs=1e-3)}
    assert coordinator._heater_pid.kp == pytest.approx(tuned.kp)
    assert coordinator._heater_pid.ki == pytest.approx(tuned.ki)
    assert coordinator._heater_pid.kd == pytest.approx(tuned.kd)

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=5))
    await refresh(hass, entry)
    restarted = entry.runtime_data._heater_pid
    assert restarted.kp == pytest.approx(tuned.kp)
    assert restarted.ki == pytest.approx(tuned.ki)
    assert restarted.kd == pytest.approx(tuned.kd)


async def run_relay_ticks(hass, entry, freezer, minutes: int, room: str = "20.0") -> None:
    """Advance in 5 minute steps with fresh sensor reports, so no stall gate closes."""
    for tick in range(minutes // 5):
        set_room(hass, room)
        hass.states.async_set(OUTDOOR, "10.0", {"unit_of_measurement": "°C", "device_class": "temperature", "tick": tick})
        freezer.tick(timedelta(minutes=5))
        await refresh(hass, entry)


async def start_autotune_service(hass, entry, setpoint: float) -> None:
    await hass.services.async_call(DOMAIN, "start_autotune", {"entry_id": entry.entry_id, "setpoint": setpoint}, blocking=True)


async def test_relay_test_ends_at_the_time_limit_with_the_heater_off(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, calls = await setup_heater(hass, config_data)
    coordinator = entry.runtime_data
    await refresh(hass, entry)
    await start_autotune_service(hass, entry, 22.0)
    await run_relay_ticks(hass, entry, freezer, 60)
    assert coordinator.data["autotune_status"] == "running"
    assert "turn_on" in calls
    hass.states.async_set(HEATER, "on")
    calls.clear()
    for _ in range(13):
        await run_relay_ticks(hass, entry, freezer, 5)
        if coordinator.data["autotune_status"] != "running":
            break
    assert coordinator.data["autotune_status"] == "timed_out"
    assert calls[-1] == "turn_off"


async def test_cancel_autotune_turns_the_heater_off_and_clears_the_test(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, calls = await setup_heater(hass, config_data)
    coordinator = entry.runtime_data
    await refresh(hass, entry)
    await start_autotune_service(hass, entry, 22.0)
    await run_relay_ticks(hass, entry, freezer, 10)
    assert "turn_on" in calls
    hass.states.async_set(HEATER, "on")
    calls.clear()
    await hass.services.async_call(DOMAIN, "cancel_autotune", {"entry_id": entry.entry_id}, blocking=True)
    await hass.async_block_till_done()
    assert calls == ["turn_off"]
    await refresh(hass, entry)
    assert coordinator.data["autotune_status"] == "cancelled"


async def test_heating_failure_repair_fires_during_a_relay_test(hass, enable_custom_integrations, config_data, freezer):
    from homeassistant.helpers import issue_registry as ir

    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data)
    await refresh(hass, entry)
    await start_autotune_service(hass, entry, 22.0)
    await run_relay_ticks(hass, entry, freezer, 75)
    assert entry.runtime_data.data["autotune_status"] == "running"
    assert entry.runtime_data.data["heating_failure"] is True
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{entry.entry_id}_heating_failure") is not None


async def test_start_autotune_rejects_a_setpoint_1_k_above_the_room(hass, enable_custom_integrations, config_data):
    entry, _ = await setup_heater(hass, config_data)
    await refresh(hass, entry)
    with pytest.raises(ValueError, match="at least 2 K above the room"):
        await start_autotune_service(hass, entry, 21.0)
    assert entry.runtime_data.data["autotune_status"] != "running"


@pytest.mark.parametrize("setpoint", [14.0, 30.0])
async def test_start_autotune_rejects_a_setpoint_outside_the_range(hass, enable_custom_integrations, config_data, setpoint):
    entry, _ = await setup_heater(hass, config_data, room="10.0")
    await refresh(hass, entry)
    with pytest.raises(ValueError, match="15 to 26"):
        await start_autotune_service(hass, entry, setpoint)
    assert entry.runtime_data.data["autotune_status"] != "running"


async def test_relay_does_not_heat_at_the_heat_on_threshold_and_the_test_ends(hass, enable_custom_integrations, config_data):
    entry, calls = await setup_heater(hass, config_data)
    coordinator = entry.runtime_data
    await refresh(hass, entry)
    threshold = coordinator._heat_on_below_c
    set_room(hass, f"{threshold:.2f}")
    await refresh(hass, entry)
    await start_autotune_service(hass, entry, threshold + 2.0)
    calls.clear()
    await refresh(hass, entry)
    assert "turn_on" not in calls
    assert coordinator.data["autotune_status"] == "reached_cutoff"


async def test_relay_does_not_heat_while_the_ac_cools_and_the_test_ends(hass, enable_custom_integrations, config_data):
    entry, calls = await setup_heater(hass, config_data)
    coordinator = entry.runtime_data
    await refresh(hass, entry)
    await start_autotune_service(hass, entry, 22.0)
    hass.states.async_set(CLIMATE, "cool", {"temperature": 22.0, "friendly_name": "Test AC", "target_temp_step": 1.0})
    calls.clear()
    await refresh(hass, entry)
    assert "turn_on" not in calls
    assert coordinator.data["autotune_status"] == "reached_cutoff"


async def test_tuned_gains_apply_when_the_gain_fields_are_at_defaults(hass, enable_custom_integrations, config_data, freezer):
    # A fresh install stores no gain fields, so the fields sit at their defaults and the tuned gains apply.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data)
    coordinator = entry.runtime_data
    coordinator._heater_pid = None
    coordinator._heater_gains = {"kp": 7.0, "ki": 30.0, "kd": 0.2}
    await refresh(hass, entry)
    pid = coordinator._heater_pid
    assert (pid.kp, pid.ki, pid.kd) == pytest.approx((7.0, 30.0, 0.2))


async def test_finishing_a_tune_on_a_live_pid_keeps_the_user_kp(hass, enable_custom_integrations, config_data, freezer):
    # Kp 12 is set in the flow. The tune finishes while the PID runs and reports kp 7. The live PID must keep 12.
    import types

    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data, extra={CONF_HEATER_KP: 12.0})
    coordinator = entry.runtime_data
    assert coordinator._heater_pid is not None
    coordinator._tuner = types.SimpleNamespace(result=lambda: types.SimpleNamespace(kp=7.0, ki=30.0, kd=0.2))
    coordinator._finish_relay()
    pid = coordinator._heater_pid
    assert pid.kp == pytest.approx(12.0)
    assert (pid.ki, pid.kd) == pytest.approx((30.0, 0.2))


async def test_user_kp_after_a_tune_wins_over_the_tuned_kp(hass, enable_custom_integrations, config_data, freezer):
    # The tune stored kp 7. The user sets Kp 12 in reconfigure. Ki and Kd stay at their defaults, so the tuned values apply.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data)
    entry.runtime_data._heater_gains = {"kp": 7.0, "ki": 30.0, "kd": 0.2}
    new_data = dict(entry.data)
    new_data[CONF_HEATER_KP] = 12.0
    await _reconfigure(hass, entry, new_data)
    await refresh(hass, entry)
    pid = entry.runtime_data._heater_pid
    assert pid.kp == pytest.approx(12.0)
    assert (pid.ki, pid.kd) == pytest.approx((30.0, 0.2))


@pytest.mark.parametrize("room", ["26.0", "unavailable"], ids=["hot_room", "rooms_unavailable"])
async def test_stalled_sensor_turns_the_ac_off(hass, enable_custom_integrations, config_data, monkeypatch, freezer, room):
    from custom_components.hvac_horizon.coordinator import HorizonCoordinator

    monkeypatch.setattr(HorizonCoordinator, "_stalled_sensors", lambda self, now: [ROOM_A, ROOM_B])
    entry, _ = await setup_heater(hass, config_data, room=room, climate="cool", outdoor="30.0")
    modes = []

    async def record_mode(call):
        modes.append(call.data["hvac_mode"])

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    # The AC has run for a while, so the minimum on time does not hold the stop.
    freezer.tick(timedelta(minutes=10))
    await refresh(hass, entry)
    assert "off" in modes
    assert "cool" not in modes


async def _setup_ac_only(hass, config_data, room="20.0", outdoor="10.0", ac_heat=True):
    """A control-on entry with no heater switch, so the AC is the only heat source.

    The fake climate services record each call and report the new mode and setpoint on the entity,
    as a real AC does. Without that, the integration reads its own write as a manual change.
    """
    set_inputs(hass)
    hass.states.async_set(CLIMATE, "off", {"temperature": 22.0, "friendly_name": "Test AC", "target_temp_step": 1.0})
    hass.states.async_set(ROOM_A, room, {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(ROOM_B, room, {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(OUTDOOR, outdoor, {"unit_of_measurement": "°C", "device_class": "temperature"})
    modes, temps = [], []

    async def record_mode(call):
        modes.append(call.data["hvac_mode"])
        hass.states.async_set(CLIMATE, call.data["hvac_mode"], dict(hass.states.get(CLIMATE).attributes))

    async def record_temp(call):
        temps.append(call.data)
        state = hass.states.get(CLIMATE)
        hass.states.async_set(CLIMATE, state.state, {**state.attributes, "temperature": call.data["temperature"]})

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    hass.services.async_register("climate", "set_temperature", record_temp)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data, control=True, ac_heat=ac_heat), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, modes, temps


async def test_ac_heat_source_sends_heat_mode_below_the_heat_threshold(hass, enable_custom_integrations, config_data, freezer):
    entry, modes, temps = await _setup_ac_only(hass, config_data, room="20.0", outdoor="10.0")
    assert entry.runtime_data.data["heat_source"] == "ac"
    assert modes == ["heat"]
    assert temps == [{"entity_id": CLIMATE, "temperature": 24.0}]
    # The AC now reports heat at 24 C. The next tick writes nothing.
    freezer.tick(timedelta(minutes=10))
    await refresh(hass, entry)
    assert modes == ["heat"]
    assert temps == [{"entity_id": CLIMATE, "temperature": 24.0}]


def _set_ac_rooms(hass, temp: str) -> None:
    for entity in (ROOM_A, ROOM_B):
        hass.states.async_set(entity, temp, {"unit_of_measurement": "°C", "device_class": "temperature"})


async def test_heat_then_cool_switches_at_once_without_a_restart_delay(hass, enable_custom_integrations, config_data, freezer):
    # Heat starts at 10:00. Cool is wanted at 10:04. The compressor never stopped, so no restart delay applies.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, modes, _ = await _setup_ac_only(hass, config_data, room="20.0", outdoor="10.0")
    assert modes == ["heat"]
    # The outdoor sensor warms to 30 C, so heat is no longer allowed and cooling is.
    freezer.tick(timedelta(minutes=4))
    _set_ac_rooms(hass, "26.0")
    hass.states.async_set(OUTDOOR, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, entry)
    assert modes[-1] == "cool"


async def test_heat_stop_is_not_held(hass, enable_custom_integrations, config_data, freezer):
    # Heat starts at 10:00. The room reaches the dead band at 10:01, where neither heat nor cool is wanted. The stop goes out at once.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, modes, _ = await _setup_ac_only(hass, config_data, room="20.0", outdoor="10.0")
    assert modes == ["heat"]
    freezer.tick(timedelta(minutes=1))
    _set_ac_rooms(hass, "23.8")
    await refresh(hass, entry)
    assert modes[-1] == "off"


async def test_ac_does_not_heat_when_a_heater_switch_is_set(hass, enable_custom_integrations, config_data):
    # With a heater switch the switch is the heat source, so the AC must not heat as well.
    entry, calls = await setup_heater(hass, config_data, outdoor="10.0", room="20.0", climate="off")
    modes = []

    async def record_mode(call):
        modes.append(call.data["hvac_mode"])

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    await refresh(hass, entry)
    assert "heat" not in modes


@pytest.mark.parametrize(
    ("key", "entity_id", "value"),
    [
        ("mpc_plan", "sensor.hvac_horizon_test_ac_mpc_plan_steps", [True, True, False, False] + [False] * 12),
        ("shed", "sensor.hvac_horizon_test_ac_loads_shed", ["switch.test_load"]),
        ("sensors_stalled", "sensor.hvac_horizon_test_ac_stalled_sensors", [ROOM_A]),
    ],
)
async def test_list_status_sensors_show_their_content_not_a_count(hass, enable_custom_integrations, config_data, key, entity_id, value):
    import json

    entry, _ = await setup_heater(hass, config_data)
    await enable_sensors(hass, entry, key)  # mpc_plan and shed are disabled by default
    entry.runtime_data.data[key] = value
    entry.runtime_data.async_update_listeners()
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes["value"] == value
    assert state.state == json.dumps(value)


async def test_heater_pid_integral_is_frozen_while_the_band_forces_output_to_zero(hass, enable_custom_integrations, config_data, freezer):
    # A room at 23.7 C is above the 23.54 C heat threshold, so the band forces the heater output to 0.
    # The error is still positive, so an unfrozen integral would grow every tick.
    entry, calls = await setup_heater(hass, config_data, room="23.7")
    coordinator = entry.runtime_data
    before = coordinator._heater_pid._integral
    for tick in range(100):
        set_room(hass, "23.7")
        hass.states.async_set(OUTDOOR, "10.0", {"unit_of_measurement": "°C", "device_class": "temperature", "tick": tick})
        freezer.tick(timedelta(minutes=5))
        await refresh(hass, entry)
    # The band overrides the PID request in the drive step, so the switch output (_pwm_pct_now) is 0.
    assert "turn_on" not in calls
    assert coordinator._heater_pid._integral == pytest.approx(before)


def _register_climate_fakes(hass):
    async def ignore(call):
        return None

    hass.services.async_register("climate", "set_hvac_mode", ignore)
    hass.services.async_register("climate", "set_temperature", ignore)


async def test_comfort_energy_weight_moves_the_cooling_target(hass, enable_custom_integrations, config_data):
    # Comfort 24 C, eco 26 C, weight 1: the target is 26 C, so a room at 25 C is below it and the AC stays off.
    set_inputs(hass)
    _register_climate_fakes(hass)
    data = {k: v for k, v in config_data.items() if k != CONF_PRICE}  # no price, so the price shift leaves comfort alone
    data.update(control=True, eco_c=26.0, comfort_energy_weight=1.0)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    hass.states.async_set(ROOM_A, "25.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(ROOM_B, "25.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.data["target_c"] == 26.0


async def test_comfort_energy_weight_off_keeps_comfort_target(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    _register_climate_fakes(hass)
    data = {k: v for k, v in config_data.items() if k != CONF_PRICE}
    data.update(control=True, eco_c=26.0)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    _register_climate_fakes(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.data["target_c"] == 24.0


async def test_idle_valve_is_stepped_after_a_week_still(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    _register_climate_fakes(hass)
    calls = []

    async def record_valve(call):
        calls.append(call.data["value"])

    hass.services.async_register("number", "set_value", record_valve)
    data = dict(config_data, control=True, valve="number.test_valve")
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    opening = coordinator.data["valve_opening_pct"]
    hass.states.async_set("number.test_valve", str(opening))
    await refresh(hass, entry)
    assert calls == []  # the valve is already at its command, and it has not been still for a week

    freezer.tick(timedelta(days=7, minutes=1))
    await refresh(hass, entry)
    assert len(calls) == 1
    assert calls[0] != opening and abs(calls[0] - opening) == 10.0


async def test_trv_offset_corrects_the_valve_target(hass, enable_custom_integrations, config_data):
    # The TRV reads 1 K above the room median (26.1 C), so the valve aims at 24 + 1 = 25 C.
    set_inputs(hass)
    _register_climate_fakes(hass)
    hass.states.async_set("number.test_valve", "0")
    hass.states.async_set("sensor.test_trv", "27.1", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.services.async_register("number", "set_value", lambda call: None)
    data = dict(config_data, control=True, valve="number.test_valve", trv_temperature="sensor.test_trv")
    data.pop(CONF_PRICE)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    assert coordinator.data["trv_offset_k"] == pytest.approx(1.0, abs=0.01)
    from custom_components.hvac_horizon.features import valve_opening_pct

    assert coordinator.data["valve_opening_pct"] == pytest.approx(valve_opening_pct(26.1, 25.0))


async def _setup_valve(hass, config_data, room: str, trv: str | None, on_value=None):
    """A control-on entry with a valve. The TRV sensor is set only when trv is given. on_value records number.set_value calls."""
    set_inputs(hass)
    _register_climate_fakes(hass)
    hass.states.async_set("number.test_valve", "0")
    for entity in (ROOM_A, ROOM_B):
        hass.states.async_set(entity, room, {"unit_of_measurement": "°C", "device_class": "temperature"})
    data = dict(config_data, control=True, valve="number.test_valve")
    if trv is not None:
        hass.states.async_set("sensor.test_trv", trv, {"unit_of_measurement": "°C", "device_class": "temperature"})
        data["trv_temperature"] = "sensor.test_trv"
    hass.services.async_register("number", "set_value", on_value or (lambda call: None))
    data.pop(CONF_PRICE)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_trv_reading_one_kelvin_warmer_opens_the_valve_by_100_points(hass, enable_custom_integrations, config_data):
    # Room 24 C at comfort 24 C: without a TRV the valve stays closed. A TRV at 25 C gives offset +1 K, so the
    # target rises to 25 C, the error is 1 K, and the valve command goes to 100. The valve must open more.
    sent_without, sent_with = [], []
    without_trv = await _setup_valve(hass, dict(config_data, comfort_c=24.0), room="24.0", trv=None, on_value=lambda call: sent_without.append(call.data["value"]))
    assert without_trv.runtime_data.data["valve_opening_pct"] == pytest.approx(0.0)
    assert all(value == 0.0 for value in sent_without)
    with_trv = await _setup_valve(hass, dict(config_data, comfort_c=24.0), room="24.0", trv="25.0", on_value=lambda call: sent_with.append(call.data["value"]))
    assert with_trv.runtime_data.data["trv_offset_k"] == pytest.approx(1.0, abs=0.01)
    assert sent_with and sent_with[-1] == pytest.approx(100.0)


async def test_trv_offset_keeps_the_valve_between_0_and_100(hass, enable_custom_integrations, config_data):
    # Room 18 C with a TRV at 19 C: the target moves to 25 C, the error is 7 K, and the opening clamps to 100.
    low = await _setup_valve(hass, config_data, room="18.0", trv="19.0")
    assert low.runtime_data.data["valve_opening_pct"] == pytest.approx(100.0)
    # Room 26 C with a TRV at 22 C: offset -4 K, the target drops to 20 C, and the opening clamps to 0.
    # Offsets above 5 K in size are dropped as outliers by trv_offset.
    high = await _setup_valve(hass, config_data, room="26.0", trv="22.0")
    assert high.runtime_data.data["valve_opening_pct"] == pytest.approx(0.0)


async def test_radiator_residual_heat_closes_the_heater_gate_early(hass, enable_custom_integrations, config_data, freezer):
    # Room 23 C is below the heat threshold of 23.5 C. The heater stopped now, so the radiators still add 1 K.
    # The gate reads the room at 24 C and forces the output to zero. Without radiators the heater stays on.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data, outdoor="10.0", room="23.0", extra={"radiators": True})
    coordinator = entry.runtime_data
    now = dt_util.utcnow().timestamp()
    coordinator._pwm_output_pct = 50.0
    coordinator._heater_stopped_min = now / 60.0
    result: dict = {}
    await coordinator._async_heater_step(now, result)
    assert result["heater_pwm_on"] is False

    entry2, _ = await setup_heater(hass, dict(config_data), outdoor="10.0", room="23.0")
    other = entry2.runtime_data
    other._pwm_output_pct = 50.0
    other._heater_stopped_min = now / 60.0
    result2: dict = {}
    await other._async_heater_step(now, result2)
    assert result2["heater_pwm_on"] is True


async def test_second_ac_power_gives_a_two_device_room_fit(hass, enable_custom_integrations, config_data, freezer):
    import math

    from custom_components.hvac_horizon.collector import Entry

    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    _register_climate_fakes(hass)
    data = dict(config_data, climate_2="climate.test_ac2", power_2="sensor.test_ac2_power")
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    # Synthetic history: k 0.3, g 0.2, first AC 1.4 K/h per kW at 1 kW (cycle 6), second AC 0.9 at 1 kW (cycle 17), 5-minute steps.
    now = dt_util.utcnow().timestamp()
    temp, entries = 24.0, []
    for i in range(800):
        outdoor = 27.0 + 6.0 * math.sin(i / 9.0)
        ac1 = 1.0 if (i // 6) % 2 == 0 else 0.0
        ac2 = 1.0 if (i // 17) % 2 == 1 else 0.0
        entries.append(Entry(t=now - (800 - i) * 300, room_c=temp, outdoor_c=outdoor, power_w=1000 * ac1, power_2_w=1000 * ac2))
        temp += (0.3 * (outdoor - temp) + 0.2 - 1.4 * ac1 - 0.9 * ac2) * (5 / 60)
    coordinator._entries = entries
    coordinator._refit()
    await refresh(hass, entry)
    fit = entry.runtime_data.data["room_fit"]
    assert fit["ac_k_per_h_per_kw"] == pytest.approx(1.4, abs=0.05)
    assert fit["ac_2_k_per_h_per_kw"] == pytest.approx(0.9, abs=0.05)
    assert fit["samples"] > 0


async def test_heater_intervals_give_a_heater_coefficient_and_the_forecast_uses_the_three_term_fit(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    _register_climate_fakes(hass)
    data = dict(config_data, climate_2="climate.test_ac2", power_2="sensor.test_ac2_power", heater_switch="switch.test_heater")
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    # Synthetic history, 5-minute steps. Cooling: first AC 1.4 K/h per kW, second AC 0.9 K/h per kW. Heater: 2.0 K/h while on.
    # The heater runs in 11-step blocks, one block in three, so its on periods are not the AC's.
    now = dt_util.utcnow().timestamp()
    temp, entries = 24.0, []
    for i in range(800):
        outdoor = 27.0 + 6.0 * math.sin(i / 9.0)
        ac1 = 1.0 if (i // 6) % 2 == 0 else 0.0
        ac2 = 1.0 if (i // 17) % 2 == 1 else 0.0
        heat = 1.0 if (i // 11) % 3 == 1 else 0.0
        entries.append(
            Entry(
                t=now - (800 - i) * 300,
                room_c=temp,
                outdoor_c=outdoor,
                power_w=1000 * ac1,
                power_2_w=1000 * ac2,
                heat_pct=100.0 * heat,
            )
        )
        temp += (0.3 * (outdoor - temp) + 0.2 - 1.4 * ac1 - 0.9 * ac2 + 2.0 * heat) * (5 / 60)
    coordinator._entries = entries
    coordinator._refit()
    await refresh(hass, entry)
    fit = entry.runtime_data.data["room_fit"]
    assert fit["heater_k_per_h_on"] == pytest.approx(2.0, abs=0.1)
    assert fit["ac_k_per_h_per_kw"] == pytest.approx(1.4, abs=0.05)
    assert fit["ac_2_k_per_h_per_kw"] == pytest.approx(0.9, abs=0.05)
    # The forecast model is the three-term fit: its cooling terms come from the room model, and the heater is left out.
    room = coordinator._room_model
    assert room is not None and room.coef["heater"] > 0
    means = {
        "ac": running_power_kw(entries, now) or 0.0,
        "ac_2": running_power_kw(entries, now, field="power_2_w") or 0.0,
    }
    expected, total = forecast_fit(room, means)
    model, power = coordinator._forecast_model(coordinator._active_fit(), 1.0, now)
    assert model.k == pytest.approx(expected.k)
    assert model.c == pytest.approx(expected.c)
    assert model.g == pytest.approx(expected.g)
    assert power == pytest.approx(total)


async def test_forecast_day_counts_are_published_as_content(hass, enable_custom_integrations, config_data):
    async def ignore(call):
        return None

    async def forecasts(call):
        return {"weather.test": {"forecast": [{"temperature": 31.0}, {"temperature": 26.0}, {"temperature": 20.0}, {"temperature": 8.0}]}}

    hass.services.async_register("weather", "get_forecasts", forecasts, supports_response=SupportsResponse.ONLY)
    hass.services.async_register("climate", "set_hvac_mode", ignore)
    hass.services.async_register("climate", "set_temperature", ignore)
    hass.states.async_set("weather.test", "sunny")
    data = dict(config_data, weather="weather.test")
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await enable_sensors(hass, entry, "forecast_days")  # disabled by default
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert entry.runtime_data.data["forecast_days"] == {"hot": 1, "warm": 1, "mild": 1, "cool": 0, "cold": 1}
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_forecast_days")
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes["value"]["hot"] == 1


class _FakeRecorderState:
    def __init__(self, when, state):
        self.last_changed = when
        self.state = state


class _FakeRecorder:
    def __init__(self, histories):
        self._histories = histories

    async def async_add_executor_job(self, func):
        return func()


async def test_recorder_history_becomes_samples_at_startup(hass, enable_custom_integrations, config_data, monkeypatch):
    # Two hours of 5-minute recorder states for the room, outdoor and power sensors. Setup must turn them into readings
    # before the first refresh, so the model has data without waiting for live samples.
    from datetime import timedelta as _td

    from homeassistant.components import recorder
    from homeassistant.components.recorder import history

    set_inputs(hass)
    _register_climate_fakes(hass)
    now = dt_util.utcnow()
    histories = {}
    for entity, value in ((ROOM_A, 25.0), (ROOM_B, 25.2), (OUTDOOR, 30.0), (POWER, 1100.0)):
        histories[entity] = [
            _FakeRecorderState(now - _td(minutes=5 * k), str(value + (k % 3) * 0.1)) for k in range(24, 0, -1)
        ]

    def fake_significant_states(hass_arg, start, end, entity_ids, **kwargs):
        return {entity: histories[entity] for entity in entity_ids if entity in histories}

    monkeypatch.setattr(recorder, "get_instance", lambda hass_arg: _FakeRecorder(histories))
    monkeypatch.setattr(history, "get_significant_states", fake_significant_states)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    readings = entry.runtime_data._entries
    historic = [e for e in readings if e.t < now.timestamp() - 600]
    assert len(historic) >= 10
    assert all(30.0 <= e.outdoor_c <= 30.2 for e in historic)  # the fake states carry the recorder values, not live ones


async def test_learned_room_rate_coefficients_are_published_and_used(hass, enable_custom_integrations, config_data, freezer):
    import math

    from custom_components.hvac_horizon.collector import Entry

    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    _register_climate_fakes(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    now = dt_util.utcnow().timestamp()
    temp, readings = 24.0, []
    for i in range(800):
        outdoor = 27.0 + 6.0 * math.sin(i / 9.0)
        ac = 1.0 if (i // 6) % 2 == 0 else 0.0
        readings.append(Entry(t=now - (800 - i) * 300, room_c=temp, outdoor_c=outdoor, power_w=1000.0 * ac))
        temp += (0.3 * (outdoor - temp) + 0.2 - 1.4 * ac) * (5 / 60)
    coordinator._entries = readings
    coordinator._refit()
    await refresh(hass, entry)
    published = entry.runtime_data.data["room_rate_coefficients"]
    assert published["c_k_per_h_per_kw"] == pytest.approx(1.4, abs=0.05)
    assert entry.runtime_data.data["k"] == pytest.approx(published["k_per_h"])
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_status")
    attribute = hass.states.get(entity_id).attributes["room_rate_coefficients"]
    assert set(attribute) >= {"k_per_h", "g_k_per_h", "c_k_per_h_per_kw"}
    assert attribute["c_k_per_h_per_kw"] == pytest.approx(published["c_k_per_h_per_kw"])
    assert "tpi" not in hass.states.get(entity_id).attributes


async def test_fit_quality_and_forecast_range_are_published(hass, enable_custom_integrations, config_data):
    import math

    from custom_components.hvac_horizon.collector import Entry

    _register_climate_fakes(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    now = dt_util.utcnow().timestamp()
    rng = random.Random(4)
    temp, readings = 24.0, []
    for i in range(800):
        outdoor = 27.0 + 6.0 * math.sin(i / 9.0)
        ac = 1.0 if (i // 6) % 2 == 0 else 0.0
        readings.append(Entry(t=now - (800 - i) * 300, room_c=temp, outdoor_c=outdoor, power_w=1000.0 * ac))
        temp += (0.3 * (outdoor - temp) + 0.2 - 1.4 * ac) * (5 / 60) + rng.gauss(0, 0.01)
    coordinator._entries = readings
    coordinator._refit()
    await refresh(hass, entry)
    data = entry.runtime_data.data
    assert data["c_stderr"] > 0
    assert data["fit_confidence"] in ("low", "medium", "high")
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_status")
    attributes = hass.states.get(entity_id).attributes
    for key in ("fit_residual_k_per_h", "k_stderr", "c_stderr", "g_stderr", "fit_outliers_dropped", "fit_confidence"):
        assert key in attributes
    assert attributes["c_stderr"] == data["c_stderr"]
    assert attributes["fit_confidence"] == data["fit_confidence"]
    assert "minutes_low" in data and "minutes_high" in data


async def test_heat_and_dry_intervals_stay_out_of_the_cooling_fit_and_are_counted(hass, enable_custom_integrations, config_data, freezer):
    import math

    from custom_components.hvac_horizon.collector import Entry

    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    _register_climate_fakes(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    now = dt_util.utcnow().timestamp()

    def series(t0, n, mode, warm):
        temp, out = 24.0, []
        for i in range(n):
            outdoor = 27.0 + 6.0 * math.sin(i / 9.0)
            on = (i // 6) % 2 == 0
            out.append(Entry(t=t0 + i * 300, room_c=temp, outdoor_c=outdoor, power_w=1000.0 if on else 0.0, mode=mode if on else "off"))
            temp += (0.3 * (outdoor - temp) + 0.2 + ((2.0 if warm else -1.4) if on else 0.0)) * (5 / 60)
        return out

    start = now - 100000
    cool = series(start, 400, "cool", False)
    coordinator._entries = cool
    coordinator._refit()
    await refresh(hass, entry)
    base = entry.runtime_data.data["room_rate_coefficients"]
    assert entry.runtime_data.data["samples_heat"] == 0
    assert entry.runtime_data.data["samples_dry"] == 0

    coordinator._entries = cool + series(start + 200000, 200, "heat", True) + series(start + 400000, 200, "dry", True)
    coordinator._refit()
    await refresh(hass, entry)
    mixed = entry.runtime_data.data["room_rate_coefficients"]
    assert mixed["c_k_per_h_per_kw"] == pytest.approx(base["c_k_per_h_per_kw"], abs=1e-3)
    assert mixed["samples_on"] == base["samples_on"]
    assert entry.runtime_data.data["samples_heat"] > 0
    assert entry.runtime_data.data["samples_dry"] > 0


async def test_online_cool_gain_follows_a_weaker_ac_and_survives_reload(hass, enable_custom_integrations, config_data, freezer):
    # History fits c = 1.4 K/h per kW. The real AC then cools at 0.7, so each live interval warms faster than predicted.
    # The cool gain must fall, stay inside its clamp, publish on the status entity, and come back after a reload.
    import math

    from custom_components.hvac_horizon.collector import Entry
    from custom_components.hvac_horizon.tpi import GAIN_MIN, GAIN_STEP_MAX

    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    _register_climate_fakes(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    now = dt_util.utcnow().timestamp()
    temp, readings = 24.0, []
    for i in range(800):  # the last reading is at `now`, so the first live tick pairs with it
        outdoor = 27.0 + 6.0 * math.sin(i / 9.0)
        ac = 1.0 if (i // 6) % 2 == 0 else 0.0
        readings.append(Entry(t=now - (799 - i) * 300, room_c=temp, outdoor_c=outdoor, power_w=1000.0 * ac, setpoint_c=24.0, mode="cool" if ac else "off"))
        temp += (0.3 * (outdoor - temp) + 0.2 - 1.4 * ac) * (5 / 60)
    coordinator._entries = readings
    coordinator._refit()
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_status")

    gains = [1.0]
    room = temp
    for _ in range(12):
        outdoor = 30.0
        room = room + (0.3 * (outdoor - room) + 0.2 - 0.7 * 1.1) * (5 / 60)  # weaker AC, 1.1 kW
        hass.states.async_set(ROOM_A, f"{room:.4f}", {"unit_of_measurement": "°C", "device_class": "temperature"})
        hass.states.async_set(ROOM_B, f"{room:.4f}", {"unit_of_measurement": "°C", "device_class": "temperature"})
        hass.states.async_set(OUTDOOR, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
        hass.states.async_set(POWER, "1100", {"unit_of_measurement": "W", "device_class": "power"})
        freezer.tick(timedelta(seconds=300))
        await refresh(hass, entry)
        gains.append(hass.states.get(entity_id).attributes["auto_tpi_gains"]["cool"])

    assert gains[-1] < 1.0
    assert all(later <= earlier + 1e-9 for earlier, later in zip(gains, gains[1:]))  # moves one way
    assert all(earlier - later <= GAIN_STEP_MAX + 1e-9 for earlier, later in zip(gains, gains[1:]))  # bounded step
    assert min(gains) >= GAIN_MIN
    # The forecast uses the scaled cooling: the status c is the fitted c times the gain.
    published = coordinator.data["room_rate_coefficients"]["c_k_per_h_per_kw"]
    assert coordinator.data["c"] == pytest.approx(published * gains[-1], rel=1e-3)

    before = gains[-1]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["auto_tpi_gains"]["cool"] == pytest.approx(before)


async def test_presence_from_two_sources_uses_any_home_and_the_away_delay(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    _register_climate_fakes(hass)
    hass.states.async_set("binary_sensor.test_presence", "on")
    hass.states.async_set("person.test_partner", "not_home")
    data = dict(config_data, presence=["binary_sensor.test_presence", "person.test_partner"])
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    assert coordinator.data["occupancy"] == "home"

    # Both sources away: still home inside the 30 minute delay, away after it.
    hass.states.async_set("binary_sensor.test_presence", "off")
    freezer.tick(timedelta(minutes=10))
    await refresh(hass, entry)
    assert coordinator.data["occupancy"] == "home"
    freezer.tick(timedelta(minutes=25))
    await refresh(hass, entry)
    assert coordinator.data["occupancy"] == "away"

    # The person arrives. The second source alone makes the house home again.
    hass.states.async_set("person.test_partner", "home")
    await refresh(hass, entry)
    assert coordinator.data["occupancy"] == "home"
    assert coordinator.data["occupancy"] == "home"


async def test_single_presence_string_still_works(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    _register_climate_fakes(hass)
    hass.states.async_set("binary_sensor.test_presence", "off")
    data = dict(config_data, presence="binary_sensor.test_presence")
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.data["occupancy"] == "home"  # inside the away delay after setup
    freezer.tick(timedelta(minutes=31))
    await refresh(hass, entry)
    assert entry.runtime_data.data["occupancy"] == "away"  # no source was ever home, so the delay runs out


async def test_investigator_answers_why_the_ac_runs(hass, enable_custom_integrations, config_data):
    set_inputs(hass)  # room 26 C, outdoor 30 C, comfort 24 C: the AC cools
    _register_climate_fakes(hass)
    data = dict(config_data, control=True)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_status")
    attributes = hass.states.get(entity_id).attributes
    assert attributes["investigation"]["answer"].startswith("The AC is running: above")
    assert attributes["investigation_ai"] is None  # no conversation agent set, so no model call
    assert attributes["ai_calls_today"] == 0


async def test_model_calls_follow_mode_changes_and_stop_at_the_budget(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    _register_climate_fakes(hass)
    calls = []

    async def fake_agent(call):
        calls.append(call.data["text"])
        return {"response": {"speech": {"plain": {"speech": "The room is above the comfort target."}}}}

    hass.services.async_register("conversation", "process", fake_agent, supports_response=SupportsResponse.ONLY)
    data = dict(config_data, control=True, ai_conversation="conversation.test_agent", ai_budget_per_day=2)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # Each flip changes the AC mode. Four changes are made; the budget allows two calls.
    for room in ("20.0", "26.0", "20.0", "26.0"):
        hass.states.async_set(ROOM_A, room, {"unit_of_measurement": "°C", "device_class": "temperature"})
        hass.states.async_set(ROOM_B, room, {"unit_of_measurement": "°C", "device_class": "temperature"})
        await refresh(hass, entry)
    assert len(calls) == 2
    assert entry.runtime_data.data["ai_calls_today"] == 2
    # The last change (to cool) was blocked by the budget. The stored answer is cleared, not kept for the old mode.
    assert entry.runtime_data.data["investigation_ai"] is None
    assert entry.runtime_data.data["investigation_ai_mode"] is None


async def test_a_blocked_model_call_clears_the_answer_for_the_old_mode(hass, enable_custom_integrations, config_data):
    # Budget of one call. The first call answers the cool mode. The room then drops, the AC goes off,
    # and the budget is spent. The old cool answer must not show as the answer for off.
    set_inputs(hass)
    _register_climate_fakes(hass)

    async def fake_agent(call):
        return {"response": {"speech": {"plain": {"speech": "The room is above the comfort target."}}}}

    hass.services.async_register("conversation", "process", fake_agent, supports_response=SupportsResponse.ONLY)
    data = dict(config_data, control=True, ai_conversation="conversation.test_agent", ai_budget_per_day=1)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.data["investigation_ai_mode"] == "cool"
    assert entry.runtime_data.data["investigation_ai"] == "The room is above the comfort target."
    for room in ("20.0", "20.0"):
        hass.states.async_set(ROOM_A, room, {"unit_of_measurement": "°C", "device_class": "temperature"})
        hass.states.async_set(ROOM_B, room, {"unit_of_measurement": "°C", "device_class": "temperature"})
        await refresh(hass, entry)
    assert entry.runtime_data.data["ai_calls_today"] == 1
    assert entry.runtime_data.data["investigation_ai"] is None
    assert entry.runtime_data.data["investigation_ai_mode"] is None
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_status")
    attributes = hass.states.get(entity_id).attributes
    assert attributes["investigation_ai"] is None
    assert attributes["investigation_ai_mode"] is None


def _report_readings(now):
    from custom_components.hvac_horizon.collector import Entry

    day = [Entry(t=now - (3 - i) * 3600, room_c=25.0 + i, outdoor_c=30.0, power_w=1000.0, mode="cool") for i in range(3)]
    old = Entry(t=now - 25 * 3600, room_c=40.0, outdoor_c=30.0, power_w=0.0, mode="off")  # outside the 24 hour window
    return [old] + day


def _report_attributes(hass, entry):
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_status")
    return hass.states.get(entity_id).attributes


async def _generate_report(hass, entry):
    await hass.services.async_call(DOMAIN, "generate_investigation_report", {"entry_id": entry.entry_id}, blocking=True)
    await hass.async_block_till_done()


async def test_report_without_an_agent_is_rule_based_over_24_hours(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    _register_climate_fakes(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entry.runtime_data._entries = _report_readings(dt_util.utcnow().timestamp())
    await _generate_report(hass, entry)
    attributes = _report_attributes(hass, entry)
    assert attributes["investigation_report_source"] == "rule"
    assert "Room 25.0 to 27.0 C, last 27.0 C." in attributes["investigation_report"]  # the 40 C reading is 25 h old
    assert attributes["investigation_report_at"] is not None


async def test_report_with_an_agent_stores_the_model_answer(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    _register_climate_fakes(hass)
    calls = []

    async def fake_agent(call):
        calls.append(call.data["text"])
        return {"response": {"speech": {"plain": {"speech": "Model report."}}}}

    hass.services.async_register("conversation", "process", fake_agent, supports_response=SupportsResponse.ONLY)
    data = dict(config_data, ai_conversation="conversation.test_agent", ai_budget_per_day=2)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entry.runtime_data._entries = _report_readings(dt_util.utcnow().timestamp())
    before = len(calls)
    await _generate_report(hass, entry)
    assert len(calls) == before + 1
    assert calls[-1].startswith("Write a short report on this AC")
    attributes = _report_attributes(hass, entry)
    assert attributes["investigation_report"] == "Model report."
    assert attributes["investigation_report_source"] == "model"


async def test_report_with_a_zero_budget_does_not_call_the_agent(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    _register_climate_fakes(hass)
    calls = []

    async def fake_agent(call):
        calls.append(call.data["text"])
        return {"response": {"speech": {"plain": {"speech": "Model report."}}}}

    hass.services.async_register("conversation", "process", fake_agent, supports_response=SupportsResponse.ONLY)
    data = dict(config_data, ai_conversation="conversation.test_agent", ai_budget_per_day=0)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entry.runtime_data._entries = _report_readings(dt_util.utcnow().timestamp())
    before = len(calls)
    await _generate_report(hass, entry)
    assert len(calls) == before
    attributes = _report_attributes(hass, entry)
    assert attributes["investigation_report_source"] == "rule"


async def test_report_has_its_own_budget_after_the_mode_change_budget_is_spent(hass, enable_custom_integrations, config_data):
    set_inputs(hass)  # room 26 C, outdoor 30 C, comfort 24 C: the first setup call switches the AC to cool
    _register_climate_fakes(hass)
    calls = []

    async def fake_agent(call):
        calls.append(call.data["text"])
        speech = "Model report." if call.data["text"].startswith("Write a short report") else "The room is above the comfort target."
        return {"response": {"speech": {"plain": {"speech": speech}}}}

    hass.services.async_register("conversation", "process", fake_agent, supports_response=SupportsResponse.ONLY)
    data = dict(config_data, control=True, ai_conversation="conversation.test_agent", ai_budget_per_day=1)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.data["ai_calls_today"] == 1  # the mode-change budget is now spent
    before = len(calls)
    await _generate_report(hass, entry)
    assert len(calls) == before + 1
    attributes = _report_attributes(hass, entry)
    assert attributes["investigation_report"] == "Model report."
    assert attributes["investigation_report_source"] == "model"
    assert attributes["ai_calls_today"] == 1  # the report did not use the mode-change counter


async def test_target_uses_the_last_good_price_when_the_price_sensor_drops(hass, enable_custom_integrations, config_data, freezer):
    # 0.50 $/kWh is above the 0.40 band top, so the target shifts by the full 1 K: 24.0 to 25.0 C.
    entry, _, _ = await _setup_ac_only(hass, config_data, room="26.0")
    hass.states.async_set(PRICE, "0.50", {"unit_of_measurement": "$/kWh"})
    await refresh(hass, entry)
    assert entry.runtime_data.data["target_c"] == 25.0
    hass.states.async_set(PRICE, STATE_UNAVAILABLE, {"unit_of_measurement": "$/kWh"})
    await refresh(hass, entry)
    assert entry.runtime_data.data["price_used_per_kwh"] == 0.50
    assert entry.runtime_data.data["target_c"] == 25.0
    # After the 60 minute limit the last good price is no longer used, and the target returns to comfort.
    freezer.tick(timedelta(minutes=61))
    await refresh(hass, entry)
    assert entry.runtime_data.data["price_used_per_kwh"] is None
    assert entry.runtime_data.data["target_c"] == 24.0


async def test_second_ac_stop_is_not_held(hass, enable_custom_integrations, config_data, freezer):
    # The second AC starts at 10:00. A stop at 10:02 goes out at once.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)  # outdoor 30 C, so cooling is allowed
    second = "climate.test_ac_2"
    hass.states.async_set(CLIMATE, "cool", {"temperature": 22.0, "friendly_name": "Test AC", "target_temp_step": 1.0})
    hass.states.async_set(second, "off", {"temperature": 22.0})
    modes = []

    async def record_mode(call):
        modes.append((call.data["entity_id"], call.data["hvac_mode"]))
        state = hass.states.get(call.data["entity_id"])
        hass.states.async_set(call.data["entity_id"], call.data["hvac_mode"], dict(state.attributes))

    async def record_temp(call):
        state = hass.states.get(call.data["entity_id"])
        hass.states.async_set(call.data["entity_id"], state.state, {**state.attributes, "temperature": call.data["temperature"]})

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    hass.services.async_register("climate", "set_temperature", record_temp)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data, control=True, climate_2=second), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert (second, "cool") in modes

    freezer.tick(timedelta(minutes=2))
    _set_ac_rooms(hass, "22.0")  # below the 24 C comfort target, so the decision is off
    await refresh(hass, entry)
    assert (second, "off") in modes
    assert entry.runtime_data.data["second_ac"] == "driven"


async def test_stall_turns_the_second_ac_off_inside_its_minimum_on_time(hass, enable_custom_integrations, config_data, freezer, monkeypatch):
    from custom_components.hvac_horizon.coordinator import HorizonCoordinator

    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)  # outdoor 30 C, so cooling is allowed
    second = "climate.test_ac_2"
    hass.states.async_set(CLIMATE, "cool", {"temperature": 22.0, "friendly_name": "Test AC", "target_temp_step": 1.0})
    hass.states.async_set(second, "off", {"temperature": 22.0})
    modes = []

    async def record_mode(call):
        modes.append((call.data["entity_id"], call.data["hvac_mode"]))
        state = hass.states.get(call.data["entity_id"])
        hass.states.async_set(call.data["entity_id"], call.data["hvac_mode"], dict(state.attributes))

    async def ignore(call):
        return None

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    hass.services.async_register("climate", "set_temperature", ignore)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data, control=True, climate_2=second), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert (second, "cool") in modes

    freezer.tick(timedelta(minutes=2))  # inside the 5 minute minimum on time
    monkeypatch.setattr(HorizonCoordinator, "_stalled_sensors", lambda self, now: [ROOM_A, ROOM_B])
    await refresh(hass, entry)
    assert (second, "off") in modes


async def test_stall_output_turns_the_heater_off_even_without_the_gate(hass, enable_custom_integrations, config_data, monkeypatch):
    # The window, lock and away gates are open here. Only the stall can turn the heater off, through safety_output.
    from custom_components.hvac_horizon.coordinator import HorizonCoordinator

    monkeypatch.setattr(HorizonCoordinator, "_heater_gate_closed", lambda self, result: False)
    monkeypatch.setattr(HorizonCoordinator, "_stalled_sensors", lambda self, now: [ROOM_A, ROOM_B])
    entry, calls = await setup_heater(hass, config_data, heater_state="on", room="20.0")
    await refresh(hass, entry)
    assert entry.runtime_data.data["safety_output_pct"] == 0.0
    assert "turn_off" in calls
    assert "turn_on" not in calls


MOTION = "binary_sensor.test_motion"
PRESENCE = "binary_sensor.test_presence"


async def _motion_setup(hass, config_data, **extra):
    """Observe-only entry with a motion sensor, a presence source and an eco setpoint of 26 C."""
    set_inputs(hass)
    hass.states.async_set(PRESENCE, "on")
    hass.states.async_set(MOTION, "on")
    # No price sensor: a price shifts the target off 24.0 C, and these tests read the exact setpoint.
    base = {k: v for k, v in config_data.items() if k != CONF_PRICE}
    data = {**base, "motion": MOTION, "presence": PRESENCE, "eco_c": 26.0, "comfort_c": 24.0, "away_c": 27.0, **extra}
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_motion_on_makes_the_room_occupied_even_when_presence_is_away(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry = await _motion_setup(hass, config_data)
    # Presence goes away after its 30 minute delay. Motion stays on, so the room is still occupied.
    hass.states.async_set(PRESENCE, "off")
    freezer.tick(timedelta(minutes=31))
    hass.states.async_set(MOTION, "on")
    await refresh(hass, entry)
    assert entry.runtime_data.data["cool_on_above_c"] == 24.0  # the room holds comfort at 24 C; eco would be 26 C


async def test_motion_idle_past_the_delay_uses_eco(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry = await _motion_setup(hass, config_data)
    await refresh(hass, entry)
    assert entry.runtime_data.data["cool_on_above_c"] == 24.0
    hass.states.async_set(MOTION, "off")
    freezer.tick(timedelta(minutes=29))  # inside the 30 minute default delay
    await refresh(hass, entry)
    assert entry.runtime_data.data["motion_preset"] == "home"
    assert entry.runtime_data.data["cool_on_above_c"] == 24.0
    freezer.tick(timedelta(minutes=2))  # 31 minutes idle
    await refresh(hass, entry)
    assert entry.runtime_data.data["motion_preset"] == "away"
    assert entry.runtime_data.data["cool_on_above_c"] == 26.0


async def test_motion_idle_past_the_delay_uses_the_away_setback_without_eco(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry = await _motion_setup(hass, config_data, eco_c=None)
    hass.states.async_set(MOTION, "off")
    freezer.tick(timedelta(minutes=31))
    await refresh(hass, entry)
    assert entry.runtime_data.data["cool_on_above_c"] == 27.0


async def test_motion_away_delay_is_a_flow_field(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry = await _motion_setup(hass, config_data, motion_away_min=5)
    hass.states.async_set(MOTION, "off")
    freezer.tick(timedelta(minutes=6))
    await refresh(hass, entry)
    assert entry.runtime_data.data["cool_on_above_c"] == 26.0


async def _mould_delta(hass, entry, freezer=None):
    """cool_on_above_c with a dry room, then with a cold outdoor surface and humid room (mould risk)."""
    hass.states.async_set("sensor.test_room_humidity", "40", {"unit_of_measurement": "%"})
    await refresh(hass, entry)
    assert entry.runtime_data.data["mould_risk"] is False
    base = entry.runtime_data.data["cool_on_above_c"]
    heat_base = entry.runtime_data.data["heat_on_below_c"]
    hass.states.async_set("sensor.test_room_humidity", "70", {"unit_of_measurement": "%"})
    hass.states.async_set(OUTDOOR, "5.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, entry)
    assert entry.runtime_data.data["mould_risk"] is True
    assert entry.runtime_data.data["heat_on_below_c"] >= heat_base
    return base, entry.runtime_data.data["cool_on_above_c"]


@pytest.mark.parametrize("idle_min, extra, expected", [(0, {}, 24.0), (31, {}, 26.0), (31, {"eco_c": None}, 27.0)])
async def test_mould_risk_raises_the_cool_threshold_by_1k_in_every_preset(
    hass, enable_custom_integrations, config_data, freezer, idle_min, extra, expected
):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry = await _motion_setup(hass, config_data, room_humidity="sensor.test_room_humidity", **extra)
    if idle_min:
        hass.states.async_set(MOTION, "off")
        freezer.tick(timedelta(minutes=idle_min))
    base, with_mould = await _mould_delta(hass, entry)
    assert base == expected
    assert with_mould == pytest.approx(base + 1.0)


async def test_shed_loads_switch_back_on_highest_priority_first_below_90_percent(hass, enable_custom_integrations, config_data):
    # Limit 4000 W, restore ceiling 3600 W. The low load is 500 W and the high load 1000 W.
    set_inputs(hass)
    calls = []

    async def record_on(call):
        calls.append(("on", call.data["entity_id"]))
        hass.states.async_set(call.data["entity_id"], "on")

    async def record_off(call):
        calls.append(("off", call.data["entity_id"]))
        hass.states.async_set(call.data["entity_id"], "off")

    async def ignore(call):
        return None

    hass.services.async_register("switch", "turn_on", record_on)
    hass.services.async_register("switch", "turn_off", record_off)
    hass.services.async_register("climate", "set_hvac_mode", ignore)
    hass.services.async_register("climate", "set_temperature", ignore)
    hass.states.async_set("sensor.test_house_power", "5000", {"unit_of_measurement": "W"})
    hass.states.async_set("switch.test_low", "on")
    hass.states.async_set("switch.test_high", "on")
    data = dict(
        config_data,
        control=True,
        whole_house_power="sensor.test_house_power",
        shed_max_w=4000,
        shed_loads=["switch.test_low|500", "switch.test_high|1000"],  # lowest priority first
    )
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert calls == [("off", "switch.test_low"), ("off", "switch.test_high")]

    # 3800 W is under the limit but above 90 percent. Nothing comes back on.
    hass.states.async_set("sensor.test_house_power", "3800", {"unit_of_measurement": "W"})
    await refresh(hass, entry)
    assert calls[2:] == []

    # 2000 W: the high load fits (2000 + 1000 is under 3600 W), so it comes back first. One load per cycle.
    hass.states.async_set("sensor.test_house_power", "2000", {"unit_of_measurement": "W"})
    await refresh(hass, entry)
    assert calls[2:] == [("on", "switch.test_high")]

    # The low load comes back on the next cycle, when 3000 + 500 is under 3600 W.
    hass.states.async_set("sensor.test_house_power", "3000", {"unit_of_measurement": "W"})
    await refresh(hass, entry)
    assert calls[3:] == [("on", "switch.test_low")]


async def _fan_states(hass, config_data, freezer, co2: str, pm25: str | None = None, ticks: int = 12):
    """Run the control cycle every 5 minutes from 10:00 and return whether the fan is on after each tick."""
    set_inputs(hass)
    freezer.move_to("2026-11-20T10:00:00+00:00")

    async def record_on(call):
        hass.states.async_set("switch.test_fan", "on")

    async def record_off(call):
        hass.states.async_set("switch.test_fan", "off")

    async def ignore(call):
        return None

    hass.services.async_register("switch", "turn_on", record_on)
    hass.services.async_register("switch", "turn_off", record_off)
    hass.services.async_register("climate", "set_hvac_mode", ignore)
    hass.services.async_register("climate", "set_temperature", ignore)
    hass.states.async_set("switch.test_fan", "off")
    hass.states.async_set("sensor.test_co2", co2, {"unit_of_measurement": "ppm"})
    if pm25 is not None:
        hass.states.async_set("sensor.test_pm25", pm25, {"unit_of_measurement": "µg/m³"})
    data = dict(config_data, control=True, fan="switch.test_fan", co2="sensor.test_co2", pm25="sensor.test_pm25" if pm25 else None)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    states = [hass.states.get("switch.test_fan").state == "on"]
    for _ in range(ticks - 1):
        freezer.tick(timedelta(minutes=5))
        await refresh(hass, entry)
        states.append(hass.states.get("switch.test_fan").state == "on")
    return states


@pytest.mark.parametrize(("co2", "on_ticks"), [("900", 3), ("1000", 6), ("1100", 9)], ids=["25 percent", "50 percent", "75 percent"])
async def test_fan_duty_follows_the_co2_rate_over_the_60_minute_window(hass, enable_custom_integrations, config_data, freezer, co2, on_ticks):
    # 900, 1000 and 1100 ppm are rates 0.25, 0.5 and 0.75. With 12 ticks in the window, the fan runs for 3, 6 and 9 of them.
    states = await _fan_states(hass, config_data, freezer, co2=co2)
    assert sum(states) == on_ticks


async def test_fan_is_on_off_at_full_rate(hass, enable_custom_integrations, config_data, freezer):
    assert all(await _fan_states(hass, config_data, freezer, co2="1300"))


async def test_fan_stays_off_at_zero_rate(hass, enable_custom_integrations, config_data, freezer):
    assert not any(await _fan_states(hass, config_data, freezer, co2="700"))


async def test_pm25_still_forces_the_fan_on_during_the_off_half(hass, enable_custom_integrations, config_data, freezer):
    assert all(await _fan_states(hass, config_data, freezer, co2="1000", pm25="30"))


async def _fan_mode_run(hass, config_data, *, state="cool", room="27.0", fan_mode="auto", control=True, auto_fan=True):
    """Set up the AC with fan modes and return the set_fan_mode commands. The target is 24.04 C, so a 27 C room is a gap of about 3 K."""
    set_inputs(hass)
    _set_ac_rooms(hass, room)
    hass.states.async_set(
        CLIMATE,
        state,
        {
            "temperature": 24.0,
            "friendly_name": "Test AC",
            "target_temp_step": 1.0,
            "fan_modes": ["auto", "low", "high"],
            "fan_mode": fan_mode,
        },
    )
    commands = []

    async def record_fan(call):
        commands.append(call.data["fan_mode"])
        current = hass.states.get(CLIMATE)
        hass.states.async_set(CLIMATE, current.state, {**current.attributes, "fan_mode": call.data["fan_mode"]})

    async def ignore(call):
        return None

    hass.services.async_register("climate", "set_fan_mode", record_fan)
    hass.services.async_register("climate", "set_hvac_mode", ignore)
    hass.services.async_register("climate", "set_temperature", ignore)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data, control=control, auto_fan=auto_fan), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return commands


async def test_fan_goes_high_when_the_room_is_more_than_2_k_above_the_target(hass, enable_custom_integrations, config_data):
    assert await _fan_mode_run(hass, config_data, room="27.0", fan_mode="auto") == ["high"]


async def test_fan_goes_auto_when_the_room_is_within_half_a_kelvin_of_the_target(hass, enable_custom_integrations, config_data):
    assert await _fan_mode_run(hass, config_data, room="24.2", fan_mode="high") == ["auto"]


async def test_fan_keeps_its_mode_between_half_a_kelvin_and_2_k(hass, enable_custom_integrations, config_data):
    assert await _fan_mode_run(hass, config_data, room="24.8", fan_mode="low") == []


async def test_fan_is_left_alone_when_the_ac_is_not_cooling(hass, enable_custom_integrations, config_data):
    assert await _fan_mode_run(hass, config_data, state="off", room="27.0", fan_mode="auto") == []


async def test_fan_is_left_alone_with_control_off(hass, enable_custom_integrations, config_data):
    assert await _fan_mode_run(hass, config_data, room="27.0", fan_mode="auto", control=False) == []


async def _mpc_run(hass, config_data, monkeypatch, freezer, *, room="23.0", plan=None, no_prices=False, outdoor="30.0", extra=None, precool_now=True, schedule=None, away=False, power_kw=None, fit_c=0.5):
    """Control on, a fixed MPC plan and a pre-cool start now. Returns the entry and the AC mode commands.

    The price sensor stays set, so the target is 24.04 C and the cool threshold is 24.04 C. The
    deadband is 0.5 K, so a room at 23.0 C is below the idle limit of 23.54 C, and a room at 23.8 C is above it.
    """
    from custom_components.hvac_horizon import coordinator as coordinator_module
    from custom_components.hvac_horizon import coordinator_forecast as coordinator_forecast_module
    from custom_components.hvac_horizon.coordinator import HorizonCoordinator
    from custom_components.hvac_horizon.model import Fit

    # The price fixture starts at 08:10 UTC. Slots that have ended are dropped, so the clock sits before them.
    freezer.move_to("2026-11-20T08:00:00+00:00")
    monkeypatch.setattr(HorizonCoordinator, "_refit", lambda self: setattr(self, "_fit", Fit(k=0.05, c=fit_c, g=0.0)))
    if precool_now:
        monkeypatch.setattr(HorizonCoordinator, "_precool_start", lambda self, now, rooms: 0.0)
    if plan is not None:
        monkeypatch.setattr(coordinator_module, "mpc_plan", lambda *args, **kwargs: list(plan))
    if no_prices:
        monkeypatch.setattr(HorizonCoordinator, "_price_forecast", lambda self, now: [])
    if power_kw is not None:
        monkeypatch.setattr(coordinator_module, "running_power_kw", lambda entries, now, *a, **k: power_kw)
        monkeypatch.setattr(coordinator_forecast_module, "running_power_kw", lambda entries, now, *a, **k: power_kw)
    set_inputs(hass)
    _set_ac_rooms(hass, room)
    hass.states.async_set(OUTDOOR, outdoor, {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(CLIMATE, "off", {"temperature": 22.0, "friendly_name": "Test AC", "target_temp_step": 1.0})
    if schedule is not None:
        hass.states.async_set("sensor.test_schedule", "on", {"next_event": schedule})
        extra = dict(extra or {}, schedule="sensor.test_schedule")
    modes = []

    async def record_mode(call):
        modes.append(call.data["hvac_mode"])
        hass.states.async_set(CLIMATE, call.data["hvac_mode"], dict(hass.states.get(CLIMATE).attributes))

    async def record_temp(call):
        state = hass.states.get(CLIMATE)
        hass.states.async_set(CLIMATE, state.state, {**state.attributes, "temperature": call.data["temperature"]})

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    hass.services.async_register("climate", "set_temperature", record_temp)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data, control=True, **(extra or {})), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    if away:
        await hass.services.async_call(DOMAIN, "set_house_mode", {"mode": "away"}, blocking=True)
        await refresh(hass, entry)
    return entry, modes


async def test_mpc_idle_step_holds_the_ac_off_when_the_room_is_well_below_target(hass, enable_custom_integrations, config_data, monkeypatch, freezer):
    entry, modes = await _mpc_run(hass, config_data, monkeypatch, freezer, room="23.0", plan=[False] * 16)
    assert "cool" not in modes
    assert entry.runtime_data.data["action"].startswith("off: mpc idle")


async def test_mpc_idle_step_never_starts_heat_with_the_ac(hass, enable_custom_integrations, config_data, monkeypatch, freezer):
    # Pre-cool wants cool. The plan idles it. The room (23.0 C) is below the heat threshold (23.5 C).
    # Outdoor 18 C allows cooling (limit 16 C) and heating by the AC (up to 22 C). No heater switch, someone home.
    # Only the cool decision may be turned off. Outdoor 15 C would block cool before the plan runs.
    entry, modes = await _mpc_run(hass, config_data, monkeypatch, freezer, room="23.0", plan=[False] * 16, outdoor="18.0")
    assert entry.runtime_data.data["heat_source"] == "ac"
    assert "heat" not in modes
    assert entry.runtime_data.data["action"].startswith("off: mpc idle")


async def test_mpc_run_step_keeps_the_pre_cool_decision(hass, enable_custom_integrations, config_data, monkeypatch, freezer):
    entry, modes = await _mpc_run(hass, config_data, monkeypatch, freezer, room="23.0", plan=[True] * 16)
    assert modes == ["cool"]
    assert entry.runtime_data.data["action"] == "cool: pre-cool starts now"


async def test_mpc_idle_step_just_below_target_is_idled_and_reported(hass, enable_custom_integrations, config_data, monkeypatch, freezer):
    # Changed from the old "inside the deadband keeps the decision": the plan may now idle a room within the deadband.
    entry, modes = await _mpc_run(hass, config_data, monkeypatch, freezer, room="23.8", plan=[False] * 16)
    assert "cool" not in modes
    assert entry.runtime_data.data["mpc_status"] == "plan step 0 idle: changed the decision to idle"


async def test_mpc_idle_plan_does_not_stop_cooling_a_room_2k_above_target(hass, enable_custom_integrations, config_data, monkeypatch, freezer):
    entry, modes = await _mpc_run(hass, config_data, monkeypatch, freezer, room="26.1", plan=[False] * 16, precool_now=False)
    assert modes == ["cool"]
    assert entry.runtime_data.data["action"].startswith("cool")
    assert entry.runtime_data.data["mpc_status"] == "plan step 0 idle: decision unchanged"


async def test_mpc_idle_plan_idles_a_cool_decision_0_2k_above_target(hass, enable_custom_integrations, config_data, monkeypatch, freezer):
    entry, modes = await _mpc_run(hass, config_data, monkeypatch, freezer, room="24.24", plan=[False] * 16)
    assert "cool" not in modes
    assert entry.runtime_data.data["action"].startswith("off: mpc idle")
    assert entry.runtime_data.data["mpc_status"] == "plan step 0 idle: changed the decision to idle"


async def test_mpc_plan_with_a_run_step_soon_does_not_idle(hass, enable_custom_integrations, config_data, monkeypatch, freezer):
    entry, modes = await _mpc_run(hass, config_data, monkeypatch, freezer, room="24.24", plan=[False, False, True] + [False] * 13)
    assert modes == ["cool"]
    assert entry.runtime_data.data["mpc_status"] == "plan step 0 idle: decision unchanged"


async def test_mpc_idle_step_does_not_idle_a_night_pre_cool(hass, enable_custom_integrations, config_data, monkeypatch, freezer):
    # Away, room 25 C, outdoor 18 C: the night plan says pre-cool to comfort 24 C. The plan's step 0 is idle.
    # The idle limit uses comfort (23.5 C), not the away setback (27 C), so the room is not idle and the AC cools.
    entry, modes = await _mpc_run(hass, config_data, monkeypatch, freezer, room="25.0", plan=[False] * 16, outdoor="18.0", extra={"night_plan_act": True})
    await hass.services.async_call("hvac_horizon", "set_house_mode", {"mode": "away"}, blocking=True)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert entry.runtime_data.data["night_plan"] == "pre-cool"
    assert modes == ["cool"]
    assert entry.runtime_data.data["action"] == "cool: night pre-cool"


async def _heat_plan_run(
    hass,
    config_data,
    monkeypatch,
    freezer,
    *,
    room="19.0",
    outdoor="10.0",
    extra=None,
    heater=False,
    price_now="0.10",
    forecast=(("2026-11-20T08:10:00+00:00", 0.10), ("2026-11-20T08:40:00+00:00", 0.30)),
    ac_power="1100",
):
    """Control on, the real MPC plans (only the fit is set), a cheap price now and a dear one 30 minutes on by default.

    `price_now` and `forecast` (time, $/kWh pairs) set the price. `ac_power` sets the AC power reading in W.
    Returns the entry and the AC mode writes. The heater switch is set only with heater=True.
    """
    from custom_components.hvac_horizon.coordinator import HorizonCoordinator
    from custom_components.hvac_horizon.model import Fit

    freezer.move_to("2026-11-20T08:00:00+00:00")
    monkeypatch.setattr(HorizonCoordinator, "_refit", lambda self: setattr(self, "_fit", Fit(k=0.05, c=0.5, g=0.0)))
    set_inputs(hass)
    hass.states.async_set(
        PRICE,
        price_now,
        {
            "unit_of_measurement": "$/kWh",
            "forecast": [{"time": time, "value": value} for time, value in forecast],
        },
    )
    hass.states.async_set(POWER, ac_power, {"unit_of_measurement": "W", "device_class": "power"})
    _set_ac_rooms(hass, room)
    hass.states.async_set(OUTDOOR, outdoor, {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(CLIMATE, "off", {"temperature": 22.0, "friendly_name": "Test AC", "target_temp_step": 1.0})
    if heater:
        hass.states.async_set(HEATER, "off")
    modes = []

    async def record_mode(call):
        modes.append(call.data["hvac_mode"])
        hass.states.async_set(CLIMATE, call.data["hvac_mode"], dict(hass.states.get(CLIMATE).attributes))

    async def record_temp(call):
        state = hass.states.get(CLIMATE)
        hass.states.async_set(CLIMATE, state.state, {**state.attributes, "temperature": call.data["temperature"]})

    async def ignore_switch(call):
        return None

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    hass.services.async_register("climate", "set_temperature", record_temp)
    hass.services.async_register("switch", "turn_on", ignore_switch)
    hass.services.async_register("switch", "turn_off", ignore_switch)
    # The price band is 0 to 0.20. The 0.10 slot sits in its middle, so the price shift leaves the 24 C target alone.
    data = dict(config_data, control=True, comfort_c=24.0, price_cheap=0.0, **{"price_expensive": 0.20, **(extra or {})})
    if heater:
        data[CONF_HEATER_SWITCH] = HEATER
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, modes


async def test_heat_plan_starts_ac_heat_when_its_first_step_says_heat(hass, enable_custom_integrations, config_data, monkeypatch, freezer):
    entry, modes = await _heat_plan_run(hass, config_data, monkeypatch, freezer, extra={"mpc_heat_plan": True})
    assert entry.runtime_data.data["heat_on_below_c"] == 23.5  # 24 C comfort less the 0.5 K deadband
    assert entry.runtime_data.data["mpc_heat_steps"][0] is True
    assert "heat" in modes


async def test_without_the_heat_plan_the_ac_does_not_heat(hass, enable_custom_integrations, config_data, monkeypatch, freezer):
    entry, modes = await _heat_plan_run(hass, config_data, monkeypatch, freezer)
    assert "mpc_heat_steps" not in entry.runtime_data.data
    assert "heat" not in modes


async def test_heat_plan_does_not_heat_when_heating_is_not_allowed(hass, enable_custom_integrations, config_data, monkeypatch, freezer):
    entry, modes = await _heat_plan_run(hass, config_data, monkeypatch, freezer, outdoor="25.0", extra={"mpc_heat_plan": True})
    assert entry.runtime_data.data["heating_allowed"] is False
    assert entry.runtime_data.data["mpc_heat_steps"][0] is True
    assert "heat" not in modes


async def test_heat_plan_does_not_send_ac_heat_when_a_heater_switch_is_set(hass, enable_custom_integrations, config_data, monkeypatch, freezer):
    entry, modes = await _heat_plan_run(hass, config_data, monkeypatch, freezer, extra={"mpc_heat_plan": True}, heater=True)
    assert entry.runtime_data.data["mpc_heat_steps"][0] is True
    assert "heat" not in modes


# Room 23.4 C is just under the 23.5 C heat threshold. A price shifts the target, so each run sets its price band so the
# shift is 0 K. The dear run sits at the middle of a 0 to 0.56 band. The flat run uses the default 0 to 0.20 band.


async def test_heat_plan_waits_when_power_is_dear_now_and_cheap_later(hass, enable_custom_integrations, config_data, monkeypatch, freezer):
    entry, modes = await _heat_plan_run(
        hass,
        config_data,
        monkeypatch,
        freezer,
        room="23.4",
        price_now="0.28",
        forecast=(("2026-11-20T08:10:00+00:00", 0.28), ("2026-11-20T08:40:00+00:00", 0.05)),
        extra={"mpc_heat_plan": True, "price_expensive": 0.56},
    )
    assert entry.runtime_data.data["heat_on_below_c"] == 23.5
    assert entry.runtime_data.data["mpc_heat_steps"][0] is False
    assert "heat" not in modes


async def test_heat_plan_heats_at_a_flat_price_just_under_the_threshold(hass, enable_custom_integrations, config_data, monkeypatch, freezer):
    entry, modes = await _heat_plan_run(
        hass,
        config_data,
        monkeypatch,
        freezer,
        room="23.4",
        price_now="0.10",
        forecast=(("2026-11-20T08:10:00+00:00", 0.10), ("2026-11-20T08:40:00+00:00", 0.10)),
        extra={"mpc_heat_plan": True},
    )
    assert entry.runtime_data.data["heat_on_below_c"] == 23.5
    assert entry.runtime_data.data["mpc_heat_steps"][0] is True
    assert "heat" in modes


async def test_heat_plan_uses_the_kept_power_when_the_ac_has_not_run_for_two_hours(hass, enable_custom_integrations, config_data, monkeypatch, freezer):
    # The AC reads 0 W, so no running reading falls in the last two hours and the plan is not made.
    entry, modes = await _heat_plan_run(hass, config_data, monkeypatch, freezer, room="19.0", extra={"mpc_heat_plan": True}, ac_power="0")
    coordinator = entry.runtime_data
    assert "mpc_heat_steps" not in coordinator.data
    assert "heat" not in modes

    # Three hours earlier the AC ran at 1.1 kW. Those readings are in the kept history, so their mean stands in for the power.
    now = dt_util.utcnow().timestamp()
    coordinator._entries = [Entry(t=now - 3 * 3600 + i * 300, room_c=19.0, outdoor_c=10.0, power_w=1100.0) for i in range(12)]
    await refresh(hass, entry)
    assert coordinator.data["mpc_heat_steps"][0] is True
    assert "heat" in modes


async def test_no_mpc_plan_falls_back_to_the_current_decision(hass, enable_custom_integrations, config_data, monkeypatch, freezer):
    entry, modes = await _mpc_run(hass, config_data, monkeypatch, freezer, room="23.0", no_prices=True)
    assert "mpc_plan" not in entry.runtime_data.data
    assert modes == ["cool"]
    assert entry.runtime_data.data["mpc_status"] == "no plan: normal decision"


async def test_a_high_priority_load_too_big_to_return_does_not_block_a_smaller_one(hass, enable_custom_integrations, config_data):
    # Limit 4000 W, restore ceiling 3600 W. The high load is 3000 W and cannot return at 2000 W (5000 W is over the ceiling).
    # The low load is 500 W and fits (2500 W), so it comes back.
    set_inputs(hass)
    calls = []

    async def record_on(call):
        calls.append(("on", call.data["entity_id"]))
        hass.states.async_set(call.data["entity_id"], "on")

    async def record_off(call):
        calls.append(("off", call.data["entity_id"]))
        hass.states.async_set(call.data["entity_id"], "off")

    async def ignore(call):
        return None

    hass.services.async_register("switch", "turn_on", record_on)
    hass.services.async_register("switch", "turn_off", record_off)
    hass.services.async_register("climate", "set_hvac_mode", ignore)
    hass.services.async_register("climate", "set_temperature", ignore)
    hass.states.async_set("sensor.test_house_power", "5000", {"unit_of_measurement": "W"})
    hass.states.async_set("switch.test_low", "on")
    hass.states.async_set("switch.test_high", "on")
    data = dict(
        config_data,
        control=True,
        whole_house_power="sensor.test_house_power",
        shed_max_w=4000,
        shed_loads=["switch.test_low|500", "switch.test_high|3000"],
    )
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert calls == [("off", "switch.test_low"), ("off", "switch.test_high")]

    hass.states.async_set("sensor.test_house_power", "2000", {"unit_of_measurement": "W"})
    await refresh(hass, entry)
    assert calls[2:] == [("on", "switch.test_low")]
    assert entry.runtime_data.data["restored"] == "switch.test_low"


async def test_eco_heats_to_the_eco_heat_setpoint(hass, enable_custom_integrations, config_data):
    # Eco at home: heat_eco_c 23.0 gives an on threshold of 22.5 C. A 22.0 C room turns the heater on.
    entry, calls = await setup_heater(hass, config_data, room="22.0", extra={CONF_ECO_C: 26.0, CONF_HEAT_ECO_C: 23.0})
    calls.clear()
    await hass.services.async_call(DOMAIN, "start_timed_preset", {"entry_id": entry.entry_id, "preset": "eco", "minutes": 60})
    await refresh(hass, entry)
    assert entry.runtime_data.data["heat_on_below_c"] == pytest.approx(22.5)
    assert "turn_on" in calls


async def test_eco_without_heat_eco_c_heats_to_comfort_minus_one(hass, enable_custom_integrations, config_data):
    # Unset heat_eco_c: eco heats to 23.0 C, so the on threshold is 22.5 C. A 23.0 C room leaves the heater off.
    # Comfort at the same 23.0 C room has a threshold of 23.5 C and turns the heater on.
    entry, calls = await setup_heater(hass, config_data, room="23.0", extra={CONF_ECO_C: 26.0})
    calls.clear()
    await hass.services.async_call(DOMAIN, "start_timed_preset", {"entry_id": entry.entry_id, "preset": "eco", "minutes": 60})
    await refresh(hass, entry)
    assert entry.runtime_data.data["heat_on_below_c"] == pytest.approx(22.5)
    assert "turn_on" not in calls
    assert entry.runtime_data.data["heater_pwm_on"] is False


async def test_comfort_at_the_same_room_turns_the_heater_on(hass, enable_custom_integrations, config_data):
    entry, calls = await setup_heater(hass, config_data, room="23.0", extra={CONF_ECO_C: 26.0})
    calls.clear()
    await refresh(hass, entry)
    assert entry.runtime_data.data["heat_on_below_c"] == pytest.approx(23.5)
    assert "turn_on" in calls


async def test_boost_heats_to_the_boost_heat_setpoint(hass, enable_custom_integrations, config_data):
    # Boost cools above its 27 C setpoint, so a 23.0 C room is not cooled. Heat threshold 24.5 C from heat_boost_c 25.0.
    extra = {CONF_BOOST_C: 27.0, CONF_HEAT_BOOST_C: 25.0}
    entry, calls = await setup_heater(hass, config_data, room="23.0", extra=extra)
    calls.clear()
    await hass.services.async_call(DOMAIN, "start_timed_preset", {"entry_id": entry.entry_id, "preset": "boost", "minutes": 60})
    await refresh(hass, entry)
    assert entry.runtime_data.data["heat_on_below_c"] == pytest.approx(24.5)
    assert entry.runtime_data.data["cool_on_above_c"] == pytest.approx(27.0)
    assert "turn_on" in calls


async def test_motion_idle_past_the_delay_heats_to_the_eco_heat_setpoint(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry = await _motion_setup(hass, config_data, heat_eco_c=23.0)
    hass.states.async_set(MOTION, "off")
    freezer.tick(timedelta(minutes=31))
    await refresh(hass, entry)
    assert entry.runtime_data.data["motion_preset"] == "away"
    assert entry.runtime_data.data["heat_on_below_c"] == pytest.approx(22.5)


async def test_presence_away_keeps_the_frost_floor_with_heat_eco_c_set(hass, enable_custom_integrations, config_data):
    hass.states.async_set(PRESENCE, "off")
    entry, _ = await setup_heater(
        hass, config_data, room="20.0", extra={CONF_PRESENCE: [PRESENCE], CONF_AWAY_DELAY_MIN: 0, CONF_HEAT_ECO_C: 23.0}
    )
    await refresh(hass, entry)
    assert entry.runtime_data.data["heat_on_below_c"] == pytest.approx(15.5)


async def test_output_min_entity_holds_the_heater_output(hass, enable_custom_integrations, config_data, monkeypatch):
    # The PID asks for 5 percent. The min entity at 20 percent raises the output to 20. Each tick reads the entity again.
    monkeypatch.setattr(Pid, "update", lambda self, *args, **kwargs: 5.0)
    hass.states.async_set("number.test_min_output", "20")
    entry, _ = await setup_heater(hass, config_data, extra={CONF_OUTPUT_MIN_ENTITY: "number.test_min_output", CONF_OUTPUT_MIN: 0.0})
    await refresh(hass, entry)
    assert entry.runtime_data.data["heater_pid_output"] == 5.0
    assert entry.runtime_data.data["heater_output_pct"] == 20.0
    hass.states.async_set("number.test_min_output", "35")
    await refresh(hass, entry)
    assert entry.runtime_data.data["heater_output_pct"] == 35.0


async def test_output_limits_fall_back_to_config_when_the_entity_is_unavailable(hass, enable_custom_integrations, config_data, monkeypatch):
    monkeypatch.setattr(Pid, "update", lambda self, *args, **kwargs: 5.0)
    hass.states.async_set("number.test_min_output", "unavailable")
    hass.states.async_set("number.test_max_output", "unavailable")
    entry, _ = await setup_heater(
        hass,
        config_data,
        extra={
            CONF_OUTPUT_MIN_ENTITY: "number.test_min_output",
            CONF_OUTPUT_MAX_ENTITY: "number.test_max_output",
            CONF_OUTPUT_MIN: 30.0,
            CONF_OUTPUT_MAX: 80.0,
        },
    )
    await refresh(hass, entry)
    assert entry.runtime_data.data["heater_output_pct"] == 30.0


async def test_preheat_starts_the_heater_one_heat_up_time_before_comfort(hass, enable_custom_integrations, config_data, freezer, monkeypatch):
    # Eco 00:00 to 07:00 heats to 16 C. Comfort starts at 07:00. The learned room model needs 60 minutes to warm 19 C to 24 C.
    # Stub the learned model: k 0, g 0 and a heat rate of 5 K/h at full output, so the warm-up is 60 steps.
    monkeypatch.setattr(coordinator_learning_module, "learn_room_rates", lambda samples: TpiFit(k=0.0, g=0.0, c=1.0, samples_off=12, samples_on=12))
    monkeypatch.setattr(coordinator_learning_module, "learn_heat_rate", lambda pairs, k, g: 5.0)
    local = dt_util.get_time_zone(hass.config.time_zone)
    freezer.move_to(datetime(2026, 11, 20, 5, 55, tzinfo=local))
    extra = {CONF_SCHEDULE_BLOCKS: [{"start_h": 0, "end_h": 7, "preset": "eco"}], CONF_HEAT_ECO_C: 16.0}
    entry, calls = await setup_heater(hass, config_data, room="19.0", extra=extra)
    calls.clear()
    await refresh(hass, entry)
    assert entry.runtime_data.data["preheat"] == "waiting"  # 65 minutes to comfort, 60 needed
    assert "turn_on" not in calls

    freezer.tick(timedelta(minutes=5))  # 06:00, 60 minutes to comfort
    calls.clear()
    await refresh(hass, entry)
    assert entry.runtime_data.data["preheat"] == "active"
    assert entry.runtime_data.data["heat_on_below_c"] == pytest.approx(23.5)
    assert "turn_on" in calls


async def test_preheat_is_off_without_a_learned_heat_model(hass, enable_custom_integrations, config_data, freezer):
    local = dt_util.get_time_zone(hass.config.time_zone)
    freezer.move_to(datetime(2026, 11, 20, 6, 0, tzinfo=local))
    extra = {CONF_SCHEDULE_BLOCKS: [{"start_h": 0, "end_h": 7, "preset": "eco"}], CONF_HEAT_ECO_C: 16.0}
    entry, _ = await setup_heater(hass, config_data, room="19.0", extra=extra)
    await refresh(hass, entry)
    assert entry.runtime_data.data["preheat"] == "no heat model"
    assert entry.runtime_data.data["heat_on_below_c"] == pytest.approx(15.5)


async def test_heat_gain_learns_from_heating_and_survives_reload(hass, enable_custom_integrations, config_data, freezer, monkeypatch):
    # The learned model is stubbed: base heat rate 1 K/h, no passive drift. The room rises 0.25 K per tick,
    # which is faster than the base rate predicts at any heater output, so the heat gain rises each tick.
    monkeypatch.setattr(coordinator_learning_module, "learn_room_rates", lambda samples: TpiFit(k=0.0, g=0.0, c=1.0, samples_off=12, samples_on=12))
    monkeypatch.setattr(coordinator_learning_module, "learn_heat_rate", lambda pairs, k, g: 1.0)
    entry, _ = await setup_heater(hass, config_data, room="20.0")
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_status")
    # Setup arms the manual-change inhibit for an hour, and unchanged room sensors count as stalled. Wait it out with fresh readings.
    freezer.tick(timedelta(minutes=61))
    for entity in (ROOM_A, ROOM_B):
        hass.states.async_set(entity, "20.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(OUTDOOR, "10.5", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(POWER, "0", {"unit_of_measurement": "W", "device_class": "power"})  # the AC is off: heating intervals only
    await refresh(hass, entry)
    gains = [entry.runtime_data._auto_heat_gain]
    room = 20.0
    for i in range(12):
        room += 0.25
        hass.states.async_set(OUTDOOR, f"{10.5 + 0.01 * i:.2f}", {"unit_of_measurement": "°C", "device_class": "temperature"})  # a changing value is not stalled
        for entity in (ROOM_A, ROOM_B):
            hass.states.async_set(entity, f"{room:.4f}", {"unit_of_measurement": "°C", "device_class": "temperature"})
        freezer.tick(timedelta(seconds=300))
        await refresh(hass, entry)
        gains.append(entry.runtime_data._auto_heat_gain)
    assert gains[-1] > 1.0
    assert all(later >= earlier - 1e-9 for earlier, later in zip(gains, gains[1:]))
    assert max(gains) <= GAIN_MAX + 1e-9

    before = gains[-1]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["auto_tpi_gains"]["heat"] == pytest.approx(before, abs=1e-3)


async def test_forecast_uses_the_two_device_fit_when_a_second_ac_is_set(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    data = {**config_data, CONF_CLIMATE_2: "climate.test_ac2", CONF_POWER_2: "sensor.test_ac2_power"}
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    # Two ACs with different on periods, so both devices vary. Cooling per kW: 1.4 for the first, 0.8 for the second.
    now = dt_util.utcnow().timestamp()
    temp = 24.0
    readings = []
    for i in range(800):
        outdoor = 27.0 + 6.0 * math.sin(i / 9.0)
        ac1 = 1.0 if (i // 6) % 2 == 0 else 0.0
        ac2 = 1.0 if (i // 10) % 2 == 0 else 0.0
        readings.append(
            Entry(
                t=now - (799 - i) * 300,
                room_c=temp,
                outdoor_c=outdoor,
                power_w=1000.0 * ac1,
                power_2_w=800.0 * ac2,
                setpoint_c=24.0,
                mode="cool" if (ac1 or ac2) else "off",
            )
        )
        temp += (0.3 * (outdoor - temp) + 0.2 - 1.4 * ac1 - 0.8 * ac2) * (5 / 60)
    coordinator._entries = readings
    coordinator._refit()

    for entity in (ROOM_A, ROOM_B):
        hass.states.async_set(entity, "26.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(OUTDOOR, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set("sensor.test_ac2_power", "800", {"unit_of_measurement": "W", "device_class": "power"})
    await refresh(hass, entry)
    published = coordinator.data["minutes"]
    assert published is not None
    # The single-device fit at the live 1.1 kW. On this data it cannot reach 24 C (its cooling is learned from the first AC alone),
    # while the combined fit reaches it. Either way the published value differs.
    single = forecast(coordinator._active_fit(), 26.0, 24.0, 30.0, 1.1, [])
    assert single.minutes is None
    assert published > 0


async def test_investigation_report_survives_unload_and_setup(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "generate_investigation_report", {"entry_id": entry.entry_id}, blocking=True)
    await hass.async_block_till_done()
    report = entry.runtime_data.data["investigation_report"]
    stamp = entry.runtime_data.data["investigation_report_at"]
    assert report and stamp

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.data["investigation_report"] == report
    assert entry.runtime_data.data["investigation_report_at"] == stamp
    assert entry.runtime_data.data["investigation_report_source"] == "rule"



async def test_ac_does_not_heat_by_default(hass, enable_custom_integrations, config_data):
    """The AC heat source writes heat only after the user sets ac_heat."""
    entry, modes, _ = await _setup_ac_only(hass, config_data, room="20.0", outdoor="10.0", ac_heat=False)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert "heat" not in modes


async def test_fan_mode_is_left_alone_by_default(hass, enable_custom_integrations, config_data):
    """The fan mode follows the room gap only after the user sets auto_fan."""
    assert await _fan_mode_run(hass, config_data, room="27.0", fan_mode="auto", auto_fan=False) == []


async def test_compressor_group_staggers_heat_starts_across_entries(hass, enable_custom_integrations, config_data, freezer):
    """A heat start runs the compressor, so two AC in one group do not start to heat in the same tick."""
    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    hass.states.async_set(ROOM_A, "20.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(ROOM_B, "20.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(OUTDOOR, "10.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    calls = []

    async def record_mode(call):
        calls.append((call.data["entity_id"], call.data["hvac_mode"]))

    async def record_temp(call):
        return None

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    hass.services.async_register("climate", "set_temperature", record_temp)
    hass.states.async_set(CLIMATE, "off", {"temperature": 22.0, "friendly_name": "Test AC"})
    hass.states.async_set("climate.test_ac_b", "off", {"temperature": 22.0, "friendly_name": "Test AC B"})
    data = dict(config_data, control=True, ac_heat=True, compressor_group="garage")
    entry_a = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry_b = MockConfigEntry(domain=DOMAIN, title="Test AC B", data=dict(data, climate="climate.test_ac_b"), unique_id="climate.test_ac_b")
    for entry in (entry_a, entry_b):
        entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry_a.entry_id)
    await hass.async_block_till_done()
    starts = [entity for entity, mode in calls if mode == "heat"]
    assert len(starts) == 1
    started, waiting_entry = (CLIMATE, entry_b) if starts[0] == CLIMATE else ("climate.test_ac_b", entry_a)
    hass.states.async_set(started, "heat", {"temperature": 22.0, "friendly_name": "Test AC"})
    freezer.tick(timedelta(minutes=4))
    await refresh(hass, waiting_entry)
    assert [entity for entity, mode in calls if mode == "heat"] == starts
    freezer.tick(timedelta(minutes=1))
    await refresh(hass, waiting_entry)
    assert [entity for entity, mode in calls if mode == "heat"] == starts + [waiting_entry.data["climate"]]


async def test_boost_with_stored_defaults_heats_a_cold_room_without_cooling_it(hass, enable_custom_integrations, config_data):
    """Stored defaults: boost_c 23 C. On a cold day the heat side wins, so a 23.2 C room gets heat and no cool write."""
    modes = []

    async def record_mode(call):
        modes.append(call.data["hvac_mode"])

    entry, calls = await setup_heater(hass, config_data, room="23.2", outdoor="10.0", extra={"boost_c": 23.0})
    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    await hass.services.async_call(DOMAIN, "start_timed_preset", {"entry_id": entry.entry_id, "preset": "boost", "minutes": 60})
    await refresh(hass, entry)
    assert "turn_on" in calls
    assert "cool" not in modes


async def test_boost_with_stored_defaults_does_not_heat_on_a_hot_day(hass, enable_custom_integrations, config_data):
    """Heating is not allowed at 30 C outdoors, so boost keeps the 23 C cap and a 23.2 C room gets no heat."""
    entry, calls = await setup_heater(hass, config_data, room="23.2", outdoor="30.0", extra={"boost_c": 23.0})
    await hass.services.async_call(DOMAIN, "start_timed_preset", {"entry_id": entry.entry_id, "preset": "boost", "minutes": 60})
    await refresh(hass, entry)
    assert "turn_on" not in calls
    assert entry.runtime_data.data["heat_on_below_c"] <= 22.5 + 1e-9



FAN = "switch.test_fan"


async def _fan_assist_run(hass, config_data, freezer, *, extra, house_away=False, locked=False):
    """Control on, a fan switch and an economizer in fan_assist.

    Outdoor 18 C is 8 K below the room at 28 C, above the 24 C comfort. The first tick starts the open-window clock.
    The ticks that follow reach fan_assist. Returns the entry and the fan switch calls from the last tick.
    """
    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    _set_ac_rooms(hass, "28.0")
    hass.states.async_set(OUTDOOR, "18.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(CLIMATE, "off", {"temperature": 22.0, "friendly_name": "Test AC", "target_temp_step": 1.0})
    hass.states.async_set(FAN, "off")
    calls = []

    async def record_on(call):
        calls.append(("turn_on", call.data["entity_id"]))
        hass.states.async_set(FAN, "on")

    async def record_off(call):
        calls.append(("turn_off", call.data["entity_id"]))
        hass.states.async_set(FAN, "off")

    async def record_mode(call):
        hass.states.async_set(CLIMATE, call.data["hvac_mode"], dict(hass.states.get(CLIMATE).attributes))

    async def echo_temp(call):
        # The device echoes its setpoint, so a later tick does not read our own write as a manual change.
        state = hass.states.get(CLIMATE)
        hass.states.async_set(CLIMATE, state.state, {**state.attributes, "temperature": call.data["temperature"]})

    hass.services.async_register("switch", "turn_on", record_on)
    hass.services.async_register("switch", "turn_off", record_off)
    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    hass.services.async_register("climate", "set_temperature", echo_temp)
    data = dict(config_data, control=True, **extra)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    if house_away:
        await hass.services.async_call("hvac_horizon", "set_house_mode", {"mode": "away"}, blocking=True)
    if locked:
        await hass.services.async_call("hvac_horizon", "set_lock", {"entry_id": entry.entry_id, "locked": True, "pin": "1234"}, blocking=True)
    calls.clear()
    # The phase reads the open minutes before the tick adds its own interval, so the 31 minute tick stays open_windows.
    # The next tick, 5 minutes on, reads 31 minutes and reaches fan_assist. The sensors change value so they do not stall.
    for step, (minutes, temp) in enumerate([(31, "28.1"), (5, "28.2")]):
        freezer.tick(timedelta(minutes=minutes))
        _set_ac_rooms(hass, temp)
        hass.states.async_set(OUTDOOR, "18.1" if step == 0 else "18.2", {"unit_of_measurement": "°C", "device_class": "temperature"})
        if step == 0:
            await refresh(hass, entry)
            calls.clear()
    await refresh(hass, entry)
    return entry, calls


async def test_fan_assist_turns_the_fan_on_when_the_option_and_switch_are_set(hass, enable_custom_integrations, config_data, freezer):
    entry, calls = await _fan_assist_run(hass, config_data, freezer, extra={"economizer_fan": True, "fan": FAN})
    assert entry.runtime_data.data["economizer"] == "fan_assist"
    assert calls == [("turn_on", FAN)]


async def test_fan_assist_without_a_fan_switch_makes_no_fan_call(hass, enable_custom_integrations, config_data, freezer):
    entry, calls = await _fan_assist_run(hass, config_data, freezer, extra={"economizer_fan": True})
    assert entry.runtime_data.data["economizer"] == "fan_assist"
    assert calls == []


async def test_fan_assist_stays_off_without_the_option(hass, enable_custom_integrations, config_data, freezer):
    entry, calls = await _fan_assist_run(hass, config_data, freezer, extra={"fan": FAN})
    assert entry.runtime_data.data["economizer"] == "fan_assist"
    assert calls == []


@pytest.mark.parametrize("gate", ["house_away", "locked"])
async def test_fan_assist_obeys_the_house_and_lock_gates(hass, enable_custom_integrations, config_data, freezer, gate):
    entry, calls = await _fan_assist_run(
        hass, config_data, freezer, extra={"economizer_fan": True, "fan": FAN, "lock_pin": "1234"},
        house_away=gate == "house_away", locked=gate == "locked",
    )
    assert entry.runtime_data.data["economizer"] == "fan_assist"
    assert ("turn_on", FAN) not in calls


async def test_warmer_room_lowers_the_peer_setpoint_by_one_kelvin_in_setpoint_mode(hass, enable_custom_integrations, config_data, freezer):
    # Requester room 26 C, peer room 24 C: 2 K warmer. The peer's cool setpoint is its comfort 24 C, so the write is 23 C.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    requester, peer, writes = await _setup_peer_pair(hass, config_data, requester_room="26.0", requester_extra={"peer_setpoint_mode": True})
    setpoints = []

    async def record_temp(call):
        setpoints.append((call.data["entity_id"], call.data["temperature"]))

    hass.services.async_register("climate", "set_temperature", record_temp)
    await refresh(hass, requester)
    await refresh(hass, peer)
    assert (PEER_CLIMATE, 23.0) in setpoints
    assert writes == []  # the valve is not driven in setpoint mode


async def test_peer_setpoint_stays_alone_without_the_setpoint_mode(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    requester, peer, writes = await _setup_peer_pair(hass, config_data, requester_room="26.0")
    setpoints = []

    async def record_temp(call):
        setpoints.append((call.data["entity_id"], call.data["temperature"]))

    hass.services.async_register("climate", "set_temperature", record_temp)
    await refresh(hass, requester)
    await refresh(hass, peer)
    assert [sp for sp in setpoints if sp[0] == PEER_CLIMATE] == []
    assert (PEER_VALVE, 100.0) in writes  # the valve request still works with the mode off


async def test_peer_setpoint_returns_to_its_earlier_value_when_the_request_ends(hass, enable_custom_integrations, config_data, freezer):
    # The peer AC is off at setpoint 24 C. Requester room 26 C lowers it to 23 C. Requester room 25 C ends the request,
    # and the peer AC is still off, so its setpoint goes back to 24 C.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    requester, peer, _ = await _setup_peer_pair(hass, config_data, requester_room="26.0", requester_extra={"peer_setpoint_mode": True})
    peer_setpoints = []

    async def record_temp(call):
        if call.data["entity_id"] != PEER_CLIMATE:
            return
        peer_setpoints.append(call.data["temperature"])
        state = hass.states.get(PEER_CLIMATE)
        hass.states.async_set(PEER_CLIMATE, state.state, {**state.attributes, "temperature": call.data["temperature"]})

    hass.services.async_register("climate", "set_temperature", record_temp)
    await refresh(hass, requester)
    await refresh(hass, peer)
    assert hass.states.get(PEER_CLIMATE).attributes["temperature"] == 23.0
    for entity in (ROOM_A, ROOM_B):
        hass.states.async_set(entity, "25.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, requester)
    await refresh(hass, peer)
    assert hass.states.get(PEER_CLIMATE).attributes["temperature"] == 24.0
    assert peer_setpoints == [23.0, 24.0]


ACCURACY_PANEL = "hvac-horizon-accuracy"


async def test_accuracy_panel_opens_the_page_with_the_entry_id_and_the_page_is_served(hass, hass_client_no_auth, enable_custom_integrations, config_data):
    from homeassistant.components.frontend import DATA_PANELS

    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data, accuracy_panel=True), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    panel = hass.data[DATA_PANELS][ACCURACY_PANEL]
    assert panel.component_name == "iframe"
    assert panel.sidebar_title == "HVAC Accuracy"
    url = panel.config["url"]
    assert url == f"/hvac_horizon/accuracy.html?entry_id={entry.entry_id}"
    anonymous = await hass_client_no_auth()
    page = await anonymous.get(url.split("?", 1)[0])
    assert page.status == 200
    assert "Forecast accuracy" in await page.text()


async def test_accuracy_panel_lists_the_entries_when_several_have_it(hass, hass_client, enable_custom_integrations, config_data):
    from homeassistant.components.frontend import DATA_PANELS

    set_inputs(hass)
    first = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data, accuracy_panel=True), unique_id=CLIMATE)
    first.add_to_hass(hass)
    assert await hass.config_entries.async_setup(first.entry_id)
    await hass.async_block_till_done()
    assert hass.data[DATA_PANELS][ACCURACY_PANEL].config["url"] == f"/hvac_horizon/accuracy.html?entry_id={first.entry_id}"
    second = MockConfigEntry(domain=DOMAIN, title="Bedroom", data=dict(config_data, accuracy_panel=True), unique_id="climate.bedroom_ac")
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()
    assert hass.data[DATA_PANELS][ACCURACY_PANEL].config["url"] == "/hvac_horizon/accuracy.html"
    client = await hass_client()
    listed = await (await client.get("/api/hvac_horizon/entries")).json()
    assert sorted((e["entry_id"], e["title"]) for e in listed) == sorted([(first.entry_id, "Test AC"), (second.entry_id, "Bedroom")])


async def test_accuracy_panel_is_removed_when_its_last_entry_unloads(hass, enable_custom_integrations, config_data):
    from homeassistant.components.frontend import DATA_PANELS

    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data, accuracy_panel=True), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert ACCURACY_PANEL in hass.data[DATA_PANELS]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert ACCURACY_PANEL not in hass.data[DATA_PANELS]


async def test_accuracy_panel_stays_off_without_the_option(hass, enable_custom_integrations, config_data):
    from homeassistant.components.frontend import DATA_PANELS

    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert ACCURACY_PANEL not in hass.data.get(DATA_PANELS, {})


async def test_boost_on_an_ac_only_entry_cools_to_boost_setpoint(hass, enable_custom_integrations, config_data):
    """No heat path (no heater switch, ac_heat and mpc_heat_plan off): boost must not lift the cool threshold."""
    set_inputs(hass)
    hass.states.async_set(ROOM_A, "24.5", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(ROOM_B, "24.5", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(OUTDOOR, "18.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(CLIMATE, "off", {"temperature": 22.0, "friendly_name": "Test AC", "target_temp_step": 1.0})
    modes = []

    async def record_mode(call):
        modes.append(call.data["hvac_mode"])
        hass.states.async_set(CLIMATE, call.data["hvac_mode"], dict(hass.states.get(CLIMATE).attributes))

    async def ignore(call):
        return None

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    hass.services.async_register("climate", "set_temperature", ignore)
    data = dict(config_data, control=True, boost_c=23.0)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "start_timed_preset", {"entry_id": entry.entry_id, "preset": "boost", "minutes": 60})
    await refresh(hass, entry)
    assert entry.runtime_data.data["cool_on_above_c"] == pytest.approx(23.0)
    assert "cool" in modes


async def test_one_stale_room_sensor_does_not_stop_the_ac_while_another_is_fresh(hass, enable_custom_integrations, config_data, freezer):
    """Per-sensor stall: a stale room sensor drops out of the median, and the AC keeps cooling on the fresh one."""
    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    hass.states.async_set(ROOM_A, "26.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(ROOM_B, "26.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(OUTDOOR, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(CLIMATE, "off", {"temperature": 22.0, "friendly_name": "Test AC", "target_temp_step": 1.0})
    modes = []

    async def record_mode(call):
        modes.append(call.data["hvac_mode"])
        hass.states.async_set(CLIMATE, call.data["hvac_mode"], dict(hass.states.get(CLIMATE).attributes))

    async def ignore(call):
        return None

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    hass.services.async_register("climate", "set_temperature", ignore)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data, control=True), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=16))
    # Room B reports now; room A keeps its old report time.
    hass.states.async_set(ROOM_B, "26.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(OUTDOOR, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, entry)
    assert ROOM_A in entry.runtime_data.data["sensors_stalled"]
    assert ROOM_B not in entry.runtime_data.data["sensors_stalled"]
    assert "cool" in modes
    assert "off" not in modes


async def test_stalled_outdoor_sensor_stops_the_ac_even_with_fresh_rooms(hass, enable_custom_integrations, config_data, monkeypatch, freezer):
    """The outdoor reading drives the cooling decision, so a stale outdoor sensor stops the AC whatever the rooms report."""
    from custom_components.hvac_horizon.coordinator import HorizonCoordinator

    monkeypatch.setattr(HorizonCoordinator, "_stalled_sensors", lambda self, now: [OUTDOOR])
    entry, _ = await setup_heater(hass, config_data, room="26.0", climate="cool", outdoor="30.0")
    modes = []

    async def record_mode(call):
        modes.append(call.data["hvac_mode"])

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    freezer.tick(timedelta(minutes=10))
    await refresh(hass, entry)
    assert "off" in modes


async def test_stale_room_sensor_leaves_the_median_so_its_value_is_not_used(hass, enable_custom_integrations, config_data, freezer):
    """Room A is stale at 30 C and room B is fresh at 22 C. The median uses room B only, so the AC does not cool."""
    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    hass.states.async_set(ROOM_A, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(ROOM_B, "22.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(OUTDOOR, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(CLIMATE, "off", {"temperature": 22.0, "friendly_name": "Test AC", "target_temp_step": 1.0})
    modes = []

    async def record_mode(call):
        # The fake AC reports the mode it was sent, so the integration's own write is not read as a manual change.
        modes.append(call.data["hvac_mode"])
        hass.states.async_set(CLIMATE, call.data["hvac_mode"], dict(hass.states.get(CLIMATE).attributes))

    async def record_temp(call):
        # Report the setpoint sent, so the integration's own write is not read as a manual change.
        hass.states.async_set(CLIMATE, hass.states.get(CLIMATE).state, dict(hass.states.get(CLIMATE).attributes, temperature=call.data["temperature"]))

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    hass.services.async_register("climate", "set_temperature", record_temp)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data, control=True), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # The setup tick at 10:00 saw both rooms fresh and cooled. Only later writes count.
    modes.clear()
    freezer.tick(timedelta(minutes=16))
    hass.states.async_set(ROOM_B, "22.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(OUTDOOR, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, entry)
    assert ROOM_A in entry.runtime_data.data["sensors_stalled"]
    # The AC cooled at setup on the 26 C median. With room A out, the median is 22 C, so the AC switches off.
    assert "off" in modes


async def test_stale_room_with_unavailable_room_stops_the_ac(hass, enable_custom_integrations, config_data, freezer):
    """Room A is stale and room B is unavailable: no fresh room reading, so the AC stops."""
    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    hass.states.async_set(ROOM_A, "26.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(ROOM_B, "26.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(OUTDOOR, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(CLIMATE, "cool", {"temperature": 22.0, "friendly_name": "Test AC", "target_temp_step": 1.0})
    modes = []

    async def record_mode(call):
        modes.append(call.data["hvac_mode"])

    async def ignore(call):
        return None

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    hass.services.async_register("climate", "set_temperature", ignore)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data, control=True), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=16))
    hass.states.async_set(ROOM_B, "unavailable")
    hass.states.async_set(OUTDOOR, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, entry)
    assert ROOM_A in entry.runtime_data.data["sensors_stalled"]
    assert "off" in modes


async def test_dispatch_publishes_the_devices_chosen_for_a_hot_room(hass, enable_custom_integrations, config_data):
    """Two AC units in one room: the dispatch result names the units chosen for the gap, observe only."""
    fleet = [
        {"device_id": "ac_small", "kind": "cool", "rate_k_per_h_per_kw": 2.0, "rated_w": 900.0},
        {"device_id": "ac_big", "kind": "cool", "rate_k_per_h_per_kw": 2.0, "rated_w": 3500.0},
    ]
    entry, _ = await setup_heater(hass, config_data, room="27.0", extra={"fleet": fleet, "dispatch_mode": "fast"})
    await refresh(hass, entry)
    dispatch = entry.runtime_data.data["dispatch"]
    assert dispatch["cool"][0] == "ac_big"
    assert dispatch["power_kw"] > 0


def _set_room(hass, value):
    for sensor_id in (ROOM_A, ROOM_B):
        hass.states.async_set(sensor_id, str(value), {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(OUTDOOR, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})


async def test_off_ac_does_not_start_while_the_room_alternates_at_the_target(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data, room="24.0", outdoor="30.0", climate="off", temperature_handler=_climate_follows_setpoint(hass))
    writes = _record_climate_writes(hass)
    for i in range(12):
        freezer.tick(timedelta(minutes=5))
        _set_room(hass, 24.1 if i % 2 == 0 else 24.0)
        await refresh(hass, entry)
    assert ("mode", "cool") not in writes


async def test_cooling_ac_stays_on_for_a_small_dip_below_target(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data, room="26.0", outdoor="30.0", climate="cool", temperature_handler=_climate_follows_setpoint(hass))
    writes = _record_climate_writes(hass)
    freezer.tick(timedelta(minutes=10))
    _set_room(hass, 23.9)
    await refresh(hass, entry)
    assert ("mode", "off") not in writes


async def test_cooling_ac_stops_when_the_room_is_below_the_band(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data, room="26.0", outdoor="30.0", climate="cool", temperature_handler=_climate_follows_setpoint(hass))
    writes = _record_climate_writes(hass)
    freezer.tick(timedelta(minutes=10))
    _set_room(hass, 23.4)
    await refresh(hass, entry)
    assert ("mode", "off") in writes


# Climate entity

ROOM_CLIMATE_UNIQUE = "{}_room"


def _record_ac_writes_only(hass):
    """Record the integration's writes to the AC. Calls to the room entity go to the real climate services."""
    writes = []
    services = hass.services.async_services_internal()["climate"]
    real = {name: services[name] for name in ("set_hvac_mode", "set_temperature")}

    def wrap(name, key, kind):
        async def handler(call):
            target = call.data["entity_id"]
            if CLIMATE in (target if isinstance(target, list) else [target]):
                writes.append((kind, call.data[key]))
                return None
            return await hass.async_run_hass_job(real[name].job, call)

        hass.services.async_register("climate", name, handler, schema=real[name].schema)

    wrap("set_hvac_mode", "hvac_mode", "mode")
    wrap("set_temperature", "temperature", "setpoint")
    return writes


async def _setup_room_climate(hass, config_data, ac_state="off", **extra):
    set_inputs(hass)
    writes = _record_ac_writes_only(hass)
    hass.states.async_set(CLIMATE, ac_state, {"temperature": 24.0, "friendly_name": "Test AC", "target_temp_step": 1.0})
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data, **extra), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id("climate", DOMAIN, ROOM_CLIMATE_UNIQUE.format(entry.entry_id))
    return entry, entity_id, writes


async def test_climate_entity_reports_room_temperature_and_target(hass, enable_custom_integrations, config_data):
    entry, entity_id, _ = await _setup_room_climate(hass, config_data)
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state.attributes["current_temperature"] == pytest.approx(26.1)
    assert state.attributes["temperature"] == 24.0
    assert state.attributes["target_temp_step"] == 0.5
    assert state.attributes["hvac_modes"] == ["off", "cool", "auto"]
    assert state.attributes["preset_modes"] == ["comfort", "eco", "boost", "away"]
    assert state.state == "off"  # control is off


async def test_climate_lists_heat_with_a_heater_switch(hass, enable_custom_integrations, config_data):
    _, entity_id, _ = await _setup_room_climate(hass, config_data, **{CONF_HEATER_SWITCH: "switch.heater"})
    assert hass.states.get(entity_id).attributes["hvac_modes"] == ["off", "cool", "heat", "auto"]


async def test_climate_set_temperature_sets_the_comfort_target(hass, enable_custom_integrations, config_data, freezer):
    entry, entity_id, writes = await _setup_room_climate(hass, config_data, control=True)
    freezer.tick(timedelta(minutes=10))  # past the minimum off time of the start at setup
    writes.clear()
    await hass.services.async_call("climate", "set_temperature", {"entity_id": entity_id, "temperature": 22}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["temperature"] == 22
    # The room is 26.1 C and the AC setpoint is 24, so the tick writes the new target.
    assert ("setpoint", 22.0) in writes


async def test_climate_hvac_mode_off_turns_the_ac_off(hass, enable_custom_integrations, config_data):
    entry, entity_id, writes = await _setup_room_climate(hass, config_data, ac_state="cool", control=True)
    writes.clear()
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": "off"}, blocking=True)
    await hass.async_block_till_done()
    assert ("mode", "off") in writes
    assert hass.states.get(entity_id).state == "off"
    # The hold stays: with the AC off and the room hot, later ticks do not start it.
    hass.states.async_set(CLIMATE, "off", {"temperature": 24.0, "friendly_name": "Test AC", "target_temp_step": 1.0})
    writes.clear()
    await refresh(hass, entry)
    assert ("mode", "cool") not in writes


async def test_climate_hvac_mode_cool_with_control_off_sends_nothing(hass, enable_custom_integrations, config_data):
    entry, entity_id, writes = await _setup_room_climate(hass, config_data)
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": "cool"}, blocking=True)
    await hass.async_block_till_done()
    assert writes == []


async def test_climate_preset_changes_the_preset_until_the_schedule_moves(hass, enable_custom_integrations, config_data):
    entry, entity_id, _ = await _setup_room_climate(hass, config_data, control=True)
    assert hass.states.get(entity_id).attributes["preset_mode"] == "comfort"
    await hass.services.async_call("climate", "set_preset_mode", {"entity_id": entity_id, "preset_mode": "eco"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["preset_mode"] == "eco"
    assert entry.runtime_data.data["preset"] == "eco"


async def test_locked_entry_rejects_climate_changes(hass, enable_custom_integrations, config_data):
    entry, entity_id, writes = await _setup_room_climate(hass, config_data, control=True, lock_pin="1234")
    assert entry.runtime_data.set_lock(True, None) is True
    for service, data in (
        ("set_temperature", {"temperature": 22}),
        ("set_hvac_mode", {"hvac_mode": "off"}),
        ("set_preset_mode", {"preset_mode": "eco"}),
    ):
        with pytest.raises(HomeAssistantError, match="locked"):
            await hass.services.async_call("climate", service, {"entity_id": entity_id, **data}, blocking=True)
    assert entry.runtime_data.comfort_c == 24.0


async def test_climate_target_survives_a_reload(hass, enable_custom_integrations, config_data):
    entry, entity_id, _ = await _setup_room_climate(hass, config_data)
    await hass.services.async_call("climate", "set_temperature", {"entity_id": entity_id, "temperature": 22.5}, blocking=True)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.comfort_c == 22.5
    assert hass.states.get(entity_id).attributes["temperature"] == 22.5


async def _restart(hass, entry):
    """Unload and set up the entry again. The house mode lives in hass.data, so it is cleared as a restart would."""
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    hass.data.get(DOMAIN, {}).pop("house_mode", None)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _stored_payload(hass, entry, hass_storage):
    """Queue a store write, unload the entry so the write lands, and return the stored data. The entry stays unloaded."""
    entry.runtime_data.save_soon()
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    return hass_storage[f"hvac_horizon.{entry.entry_id}"]["data"]


async def test_lock_survives_a_reload(hass, enable_custom_integrations, config_data):
    entry, _ = await setup_heater(hass, config_data, extra={"lock_pin": "1234"})
    await hass.services.async_call("hvac_horizon", "set_lock", {"entry_id": entry.entry_id, "locked": True, "pin": "1234"}, blocking=True)
    await _restart(hass, entry)
    coordinator = entry.runtime_data
    await refresh(hass, entry)
    assert coordinator.data["action"] == "locked"


async def test_house_mode_away_survives_a_reload(hass, enable_custom_integrations, config_data):
    entry, _ = await setup_heater(hass, config_data)
    await hass.services.async_call("hvac_horizon", "set_house_mode", {"mode": "away"}, blocking=True)
    await hass.async_block_till_done()
    await _restart(hass, entry)
    assert hass.data[DOMAIN]["house_mode"] == "away"


async def test_timed_preset_survives_a_reload_with_the_same_end(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data)
    await hass.services.async_call(DOMAIN, "start_timed_preset", {"entry_id": entry.entry_id, "preset": "away", "minutes": 120}, blocking=True)
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=30))
    await _restart(hass, entry)
    await refresh(hass, entry)
    assert entry.runtime_data.data["preset"] == "away"
    freezer.tick(timedelta(minutes=85))  # 115 minutes after the start: still inside the 120 minute window
    await refresh(hass, entry)
    assert entry.runtime_data.data["preset"] == "away"
    freezer.tick(timedelta(minutes=10))  # 125 minutes after the start
    await refresh(hass, entry)
    assert entry.runtime_data.data["preset"] == "comfort"


async def test_expired_timed_preset_does_not_return_after_a_reload(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data)
    await hass.services.async_call(DOMAIN, "start_timed_preset", {"entry_id": entry.entry_id, "preset": "away", "minutes": 20}, blocking=True)
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=30))
    await _restart(hass, entry)
    await refresh(hass, entry)
    assert entry.runtime_data.data["preset"] == "comfort"


async def test_expired_inhibit_is_dropped_and_a_running_one_is_kept(hass, enable_custom_integrations, config_data, freezer, hass_storage):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data)
    end = dt_util.utcnow().timestamp() / 60.0 + 30.0
    entry.runtime_data._inhibit_until_min = end
    entry.runtime_data.save_soon()
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=10))
    await _restart(hass, entry)
    assert (await _stored_payload(hass, entry, hass_storage))["inhibit_until_min"] == end
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=30))
    await _restart(hass, entry)
    assert (await _stored_payload(hass, entry, hass_storage))["inhibit_until_min"] is None


async def test_window_opened_by_us_is_closed_after_a_reload(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    calls = _window_actuator_fakes(hass)
    entry, _ = await setup_heater(hass, config_data, outdoor="18.0", room="26.0", extra={"window_actuator": "cover.test_window"})
    assert calls == [("open", "cover.test_window")]
    await _restart(hass, entry)
    hass.states.async_set(OUTDOOR, "26.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, entry)
    assert calls == [("open", "cover.test_window"), ("close", "cover.test_window")]


async def test_shed_loads_survive_a_reload(hass, enable_custom_integrations, config_data, hass_storage):
    entry, _ = await setup_heater(hass, config_data)
    entry.runtime_data._shed_loads.add("switch.test_load")
    entry.runtime_data.save_soon()
    await hass.async_block_till_done()
    await _restart(hass, entry)
    assert (await _stored_payload(hass, entry, hass_storage))["shed_loads"] == ["switch.test_load"]


async def test_store_payload_holds_no_pin_and_old_payloads_load(hass, enable_custom_integrations, config_data, hass_storage):
    entry, _ = await setup_heater(hass, config_data, extra={"lock_pin": "1234"})
    await hass.services.async_call("hvac_horizon", "set_lock", {"entry_id": entry.entry_id, "locked": True, "pin": "1234"}, blocking=True)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    stored = hass_storage[f"hvac_horizon.{entry.entry_id}"]
    assert stored["version"] == 4
    assert stored["data"]["locked"] is True
    assert "1234" not in str(stored)
    # A version 2 payload has none of the new keys. They take their defaults.
    hass_storage[f"hvac_horizon.{entry.entry_id}"] = {"version": 2, "minor_version": 1, "key": f"hvac_horizon.{entry.entry_id}", "data": {"entries": []}}
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await refresh(hass, entry)
    assert entry.runtime_data.data["action"] != "locked"
    payload = await _stored_payload(hass, entry, hass_storage)
    assert payload["locked"] is False
    assert payload["shed_loads"] == []
    assert payload["timed_preset"] is None


_SOON = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)  # 60 minutes after the frozen 08:00 UTC clock


async def _schedule_run(hass, config_data, monkeypatch, freezer, schedule, away=False, caplog=None):
    return await _mpc_run(
        hass, config_data, monkeypatch, freezer, room="27.0", outdoor="30.0", precool_now=False,
        schedule=schedule, away=away, power_kw=1.1, no_prices=True, fit_c=3.0,
    )


@pytest.mark.parametrize("form", ["datetime", "iso", "timestamp"])
async def test_schedule_next_event_starts_the_pre_cool(hass, enable_custom_integrations, config_data, monkeypatch, freezer, form):
    # Room 27 C, target 24 C, outdoor 30 C, deadline 60 minutes away. A real schedule helper stores a datetime.
    value = {"datetime": _SOON, "iso": _SOON.isoformat(), "timestamp": _SOON.timestamp()}[form]
    entry, modes = await _schedule_run(hass, config_data, monkeypatch, freezer, value)
    assert modes == ["cool"]
    assert entry.runtime_data.data["action"] == "cool: pre-cool starts now"
    assert entry.runtime_data.data["precool_status"] == "start in 0 min"


async def test_unparseable_next_event_gives_no_pre_cool_and_a_warning(hass, enable_custom_integrations, config_data, monkeypatch, freezer, caplog):
    entry, modes = await _schedule_run(hass, config_data, monkeypatch, freezer, "tomorrow morning")
    # Someone is home and the room is warm, so the AC cools anyway. The reason shows the pre-cool plan did not act.
    assert entry.runtime_data.data["action"] != "cool: pre-cool starts now"
    assert entry.runtime_data.data["precool_status"].startswith("no deadline: next_event 'tomorrow morning'")
    assert sum("Pre-cool ignored" in r.message for r in caplog.records if r.levelname == "WARNING") == 1
    await refresh(hass, entry)
    assert sum("Pre-cool ignored" in r.message for r in caplog.records if r.levelname == "WARNING") == 1


@pytest.mark.parametrize("form", ["datetime", "iso"])
async def test_schedule_next_event_starts_the_pre_cool_when_away(hass, enable_custom_integrations, config_data, monkeypatch, freezer, form):
    value = _SOON if form == "datetime" else _SOON.isoformat()
    entry, modes = await _schedule_run(hass, config_data, monkeypatch, freezer, value, away=True)
    assert modes == ["cool"]
    assert entry.runtime_data.data["action"] == "cool: pre-cool starts now"


SMALL_AC = "climate.small_ac"
BIG_AC = "climate.big_ac"
IR_HEATER = "switch.ir_heater"
FLEET_AC_PAIR = [
    {"device_id": "small", "kind": "cool", "entity_id": SMALL_AC, "rate_k_per_h_per_kw": 2.0, "rated_w": 900.0},
    {"device_id": "big", "kind": "cool", "entity_id": BIG_AC, "rate_k_per_h_per_kw": 2.0, "rated_w": 3500.0},
]


async def _setup_fleet(hass, config_data, *, room, fleet, outdoor="30.0", mode="economy", prepare=None, pin=None):
    """Set up a control-on entry whose cooling and heating are a fleet. Returns the entry and the service calls made."""
    set_inputs(hass)
    _set_room(hass, room)
    hass.states.async_set(OUTDOOR, outdoor, {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(CLIMATE, "off", {"temperature": 24.0, "friendly_name": "Test AC"})
    for device in fleet:
        if device["entity_id"].startswith("climate."):
            hass.states.async_set(device["entity_id"], "off", {"temperature": 30.0})
        else:
            hass.states.async_set(device["entity_id"], "off")
    calls = []

    def recorder(domain, service):
        async def record(call):
            calls.append((domain, service, call.data["entity_id"], call.data.get("hvac_mode")))

        hass.services.async_register(domain, service, record)

    for domain, service in (("climate", "set_hvac_mode"), ("climate", "set_temperature"), ("switch", "turn_on"), ("switch", "turn_off")):
        recorder(domain, service)
    data = {**config_data, "control": True, "fleet": fleet, "dispatch_mode": mode, **({"lock_pin": pin} if pin else {})}
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    if prepare is not None:
        prepare(entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, calls


def _turned_on(calls):
    return {c[2] for c in calls if (c[1] == "set_hvac_mode" and c[3] in ("cool", "heat")) or c[1] == "turn_on"}


async def test_fleet_runs_only_the_small_unit_when_the_room_is_a_little_above_target(hass, enable_custom_integrations, config_data):
    entry, calls = await _setup_fleet(hass, config_data, room="25.0", fleet=FLEET_AC_PAIR)
    assert _turned_on(calls) == {SMALL_AC}
    assert entry.runtime_data.data["dispatch"]["cool"] == ["small"]


async def test_fleet_adds_the_large_unit_when_the_room_is_far_above_target(hass, enable_custom_integrations, config_data):
    entry, calls = await _setup_fleet(hass, config_data, room="29.0", fleet=FLEET_AC_PAIR)
    assert _turned_on(calls) == {SMALL_AC, BIG_AC}


async def test_fleet_turns_off_a_running_unit_that_is_not_chosen(hass, enable_custom_integrations, config_data, freezer):
    entry, calls = await _setup_fleet(hass, config_data, room="25.0", fleet=FLEET_AC_PAIR)
    hass.states.async_set(BIG_AC, "cool", {"temperature": 24.0})
    freezer.tick(timedelta(minutes=6))
    calls.clear()
    await refresh(hass, entry)
    assert ("climate", "set_hvac_mode", BIG_AC, "off") in calls


async def test_fleet_heater_switch_turns_on_only_for_a_cold_room(hass, enable_custom_integrations, config_data):
    fleet = [
        {"device_id": "ac", "kind": "cool", "entity_id": SMALL_AC, "rate_k_per_h_per_kw": 2.0, "rated_w": 900.0},
        {"device_id": "ir", "kind": "heat", "entity_id": IR_HEATER, "rate_k_per_h_per_kw": 1.5, "rated_w": 1500.0},
    ]
    entry, calls = await _setup_fleet(hass, config_data, room="20.0", outdoor="10.0", fleet=fleet)
    assert _turned_on(calls) == {IR_HEATER}
    entry, calls = await _setup_fleet(hass, config_data, room="29.0", fleet=fleet)
    assert IR_HEATER not in _turned_on(calls)
    assert SMALL_AC in _turned_on(calls)


async def test_fleet_does_not_start_while_locked(hass, enable_custom_integrations, config_data, freezer):
    entry, calls = await _setup_fleet(hass, config_data, room="29.0", fleet=FLEET_AC_PAIR, mode="fast", pin="1234")
    assert _turned_on(calls)
    for entity in (SMALL_AC, BIG_AC):
        hass.states.async_set(entity, "off", {"temperature": 30.0})
    freezer.tick(timedelta(minutes=6))
    await hass.services.async_call("hvac_horizon", "set_lock", {"entry_id": entry.entry_id, "locked": True, "pin": "1234"}, blocking=True)
    calls.clear()
    await refresh(hass, entry)
    assert _turned_on(calls) == set()


async def test_fleet_stops_every_unit_when_a_sensor_stalls(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, calls = await _setup_fleet(hass, config_data, room="29.0", fleet=FLEET_AC_PAIR, mode="fast")
    for entity in (SMALL_AC, BIG_AC):
        hass.states.async_set(entity, "cool", {"temperature": 24.0})
    calls.clear()
    freezer.tick(timedelta(minutes=16))
    hass.states.async_set(ROOM_B, "unavailable")
    hass.states.async_set(OUTDOOR, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, entry)
    assert {("climate", "set_hvac_mode", SMALL_AC, "off"), ("climate", "set_hvac_mode", BIG_AC, "off")} <= set(calls)


async def test_device_without_a_power_sensor_publishes_its_rated_watts(hass, enable_custom_integrations, config_data):
    entry, _ = await _setup_fleet(hass, config_data, room="25.0", fleet=FLEET_AC_PAIR)
    dispatch = entry.runtime_data.data["dispatch"]
    assert dispatch["power_kw"] == 0.9
    assert dispatch["devices"]["small"]["power_kw"] == 0.9
    assert dispatch["devices"]["small"]["power_source"] == "rated"


async def test_device_with_a_power_sensor_publishes_the_measured_power(hass, enable_custom_integrations, config_data):
    fleet = [dict(FLEET_AC_PAIR[0], power_sensor="sensor.small_w"), FLEET_AC_PAIR[1]]
    hass.states.async_set("sensor.small_w", "640", {"unit_of_measurement": "W", "device_class": "power"})
    entry, _ = await _setup_fleet(hass, config_data, room="25.0", fleet=fleet)
    dispatch = entry.runtime_data.data["dispatch"]
    assert dispatch["devices"]["small"]["power_source"] == "sensor"
    assert dispatch["power_kw"] == 0.64


def _calibration_entries():
    """Readings of a room where the small cooler (3.0 K/h per kW, 0.9 kW) runs alone in blocks of 10 intervals."""
    import time

    k, g, temp, rate = 0.1, 0.3, 27.0, 3.0
    base = time.time() - 90 * 300
    out = []
    for i in range(80):
        outdoor = 30.0 + 4.0 * ((i % 40) / 40.0)
        kw = 0.9 if (i // 10) % 2 == 1 else 0.0
        out.append(
            asdict(Entry(t=base + i * 300, room_c=temp, outdoor_c=outdoor, power_w=0.0, device_kw={"small": kw, "big": 0.0}))
        )
        temp += (5.0 / 60.0) * (k * (outdoor - temp) + g - rate * kw)
    return out


async def test_fitted_rate_is_stored_and_used_after_a_reload(hass, enable_custom_integrations, config_data, hass_storage):
    def seed(entry):
        hass_storage[f"hvac_horizon.{entry.entry_id}"] = {
            "version": 3, "minor_version": 1, "key": f"hvac_horizon.{entry.entry_id}", "data": {"entries": _calibration_entries()},
        }

    entry, _ = await _setup_fleet(hass, config_data, room="25.0", fleet=FLEET_AC_PAIR, prepare=seed)
    devices = entry.runtime_data.data["dispatch"]["devices"]
    assert devices["small"]["rate_source"] == "fitted"
    assert abs(devices["small"]["rate_k_per_h_per_kw"] - 3.0) < 0.1
    assert devices["big"]["rate_source"] == "entered"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    key = f"hvac_horizon.{entry.entry_id}"
    assert abs(hass_storage[key]["data"]["device_rates"]["small"]["rate"] - 3.0) < 0.1
    # Reload with no readings left: the stored rate is still used.
    hass_storage[key]["data"]["entries"] = []
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await refresh(hass, entry)
    again = entry.runtime_data.data["dispatch"]["devices"]
    assert again["small"]["rate_source"] == "fitted"
    assert abs(again["small"]["rate_k_per_h_per_kw"] - 3.0) < 0.1


def _target_sensor_state(hass, entry):
    eid = er.async_get(hass).async_get_entity_id(sensor.DOMAIN, DOMAIN, f"{entry.entry_id}_target_c")
    return float(hass.states.get(eid).state)


async def _mid_price(hass):
    # A price midway between the cheap and expensive limits leaves the comfort target unshifted.
    hass.states.async_set(PRICE, "0.275", hass.states.get(PRICE).attributes)


@pytest.mark.parametrize("preset,expected", [("comfort", 24.0), ("eco", 26.0), ("boost", 27.0)])
async def test_target_sensor_shows_the_cool_threshold_of_the_preset(hass, enable_custom_integrations, config_data, preset, expected):
    # Documented effective target: the temperature the AC cools above (cool_on_above_c). Boost is 27 here, above the
    # heat target of 25, so boost_c stands.
    entry, _ = await setup_heater(hass, config_data, room="23.0", extra={CONF_ECO_C: 26.0, CONF_BOOST_C: 27.0, "comfort_c": 24.0})
    await _mid_price(hass)
    if preset != "comfort":
        await hass.services.async_call(DOMAIN, "start_timed_preset", {"entry_id": entry.entry_id, "preset": preset, "minutes": 60})
    await refresh(hass, entry)
    data = entry.runtime_data.data
    assert data["target_c"] == pytest.approx(expected)
    assert data["target_c"] == data["cool_on_above_c"]
    assert _target_sensor_state(hass, entry) == pytest.approx(expected)
    assert data["heat_on_below_c"] < data["target_c"]


async def test_target_sensor_matches_what_decide_cools_to(hass, enable_custom_integrations, config_data):
    # Eco with a 26 C room threshold: a 26.4 C room is inside the cooling deadband, so decide() holds off.
    from custom_components.hvac_horizon.decide import Preset, RoomState, Settings, cool_above_c

    entry, _ = await setup_heater(hass, config_data, room="23.0", extra={CONF_ECO_C: 26.0})
    await _mid_price(hass)
    await hass.services.async_call(DOMAIN, "start_timed_preset", {"entry_id": entry.entry_id, "preset": "eco", "minutes": 60})
    await refresh(hass, entry)
    coordinator = entry.runtime_data
    assert coordinator.data["target_c"] == cool_above_c(coordinator._settings, True) == 26.0
    assert coordinator.data["preset"] == "eco"


async def test_target_sensor_shows_the_user_target_from_the_room_climate(hass, enable_custom_integrations, config_data):
    entry, entity_id, _ = await _setup_room_climate(hass, config_data, control=True)
    await _mid_price(hass)
    await hass.services.async_call("climate", "set_temperature", {"entity_id": entity_id, "temperature": 22.5}, blocking=True)
    await hass.async_block_till_done()
    assert entry.runtime_data.data["target_c"] == pytest.approx(22.5)
    assert _target_sensor_state(hass, entry) == pytest.approx(22.5)
    assert entry.runtime_data.data["cool_on_above_c"] == pytest.approx(22.5)


async def test_start_autotune_service_runs_the_relay_and_switches_the_heater(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, calls = await setup_heater(hass, config_data)
    await refresh(hass, entry)
    assert entry.runtime_data.data["autotune_status"] != "running"
    calls.clear()
    await start_autotune_service(hass, entry, 22.0)
    await run_relay_ticks(hass, entry, freezer, 10)
    assert entry.runtime_data.data["autotune_status"] == "running"
    assert "turn_on" in calls


async def test_start_autotune_service_rejects_a_setpoint_below_the_room_rise(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, calls = await setup_heater(hass, config_data)
    await refresh(hass, entry)
    calls.clear()
    with pytest.raises(ValueError, match="at least 2 K above the room"):
        await start_autotune_service(hass, entry, 21.0)
    await run_relay_ticks(hass, entry, freezer, 10)
    assert entry.runtime_data.data["autotune_status"] != "running"
    assert entry.runtime_data.data["autotune"] is None


async def test_cancel_autotune_service_without_a_test_sends_no_device_call(hass, enable_custom_integrations, config_data):
    entry, calls = await setup_heater(hass, config_data)
    await refresh(hass, entry)
    calls.clear()
    await hass.services.async_call(DOMAIN, "cancel_autotune", {"entry_id": entry.entry_id}, blocking=True)
    await hass.async_block_till_done()
    assert calls == []
    assert entry.runtime_data.data["autotune_status"] != "cancelled"


async def test_set_house_mode_service_away_turns_the_heater_off_and_rejects_other_modes(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, calls = await setup_heater(hass, config_data, heater_state="on")
    await hass.services.async_call(DOMAIN, "set_house_mode", {"mode": "away"}, blocking=True)
    calls.clear()
    await refresh(hass, entry)
    assert "turn_off" in calls
    assert "turn_on" not in calls
    with pytest.raises(ValueError, match="home or away"):
        await hass.services.async_call(DOMAIN, "set_house_mode", {"mode": "party"}, blocking=True)


async def test_set_lock_service_needs_the_pin_and_stops_control(hass, enable_custom_integrations, config_data):
    writes = _record_climate_writes(hass)
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data, lock_pin="1234", control=True), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "set_lock", {"entry_id": entry.entry_id, "locked": True, "pin": "1234"}, blocking=True)
    writes.clear()
    await refresh(hass, entry)
    assert entry.runtime_data.data["action"] == "locked"
    with pytest.raises(ValueError, match="lock not changed"):
        await hass.services.async_call(DOMAIN, "set_lock", {"entry_id": entry.entry_id, "locked": False, "pin": "0000"}, blocking=True)
    await refresh(hass, entry)
    assert entry.runtime_data.data["action"] == "locked"
    await hass.services.async_call(DOMAIN, "set_lock", {"entry_id": entry.entry_id, "locked": False, "pin": "1234"}, blocking=True)
    await refresh(hass, entry)
    assert entry.runtime_data.data["action"] != "locked"


async def test_export_services_return_the_stored_readings(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await refresh(hass, entry)
    published = entry.runtime_data.data["samples"]
    assert published >= 1
    call = {"entry_id": entry.entry_id}
    samples = (await hass.services.async_call(DOMAIN, "export_samples", call, blocking=True, return_response=True))["samples"]
    rows = (await hass.services.async_call(DOMAIN, "export_csv", call, blocking=True, return_response=True))["csv"].splitlines()
    assert len(samples) == published
    assert len(rows) == published + 1
    assert samples[-1]["room_c"] == pytest.approx(26.1)
    assert float(rows[-1].split(",")[1]) == pytest.approx(26.1)


async def test_what_if_service_is_faster_with_a_higher_power(hass, enable_custom_integrations, config_data, freezer):
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    from custom_components.hvac_horizon import Fit

    entry.runtime_data._fit = Fit(k=0.3, c=1.5, g=0.0)
    base = {"entry_id": entry.entry_id, "room_c": 26.0, "target_c": 24.0, "outdoor_c": 30.0}
    low = await hass.services.async_call(DOMAIN, "what_if", {**base, "power_kw": 2.0}, blocking=True, return_response=True)
    high = await hass.services.async_call(DOMAIN, "what_if", {**base, "power_kw": 4.0}, blocking=True, return_response=True)
    assert set(low) >= {"status", "minutes", "energy_kwh", "cost"}
    assert low["status"] == high["status"] == "reachable"
    assert high["minutes"] < low["minutes"]


async def test_reset_learning_service_empties_the_exports(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await refresh(hass, entry)
    call = {"entry_id": entry.entry_id}
    assert (await hass.services.async_call(DOMAIN, "export_samples", call, blocking=True, return_response=True))["samples"]
    await hass.services.async_call(DOMAIN, "reset_learning", call, blocking=True)
    exported = await hass.services.async_call(DOMAIN, "export_samples", call, blocking=True, return_response=True)
    assert exported["samples"] == []
    assert entry.runtime_data.data["status"] == "learning"


async def test_start_timed_preset_service_publishes_the_preset_and_drives_the_ac(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data)
    await refresh(hass, entry)
    assert entry.runtime_data.data["preset"] == "comfort"
    await hass.services.async_call(DOMAIN, "start_timed_preset", {"entry_id": entry.entry_id, "preset": "eco", "minutes": 30}, blocking=True)
    await refresh(hass, entry)
    assert entry.runtime_data.data["preset"] == "eco"
    freezer.tick(timedelta(minutes=31))
    await refresh(hass, entry)
    assert entry.runtime_data.data["preset"] == "comfort"


# Safety stop covers every output the entry drives, and no command goes out before the first valid readings.

STOP_FAN = "switch.stop_fan"
STOP_HUMIDIFIER = "switch.stop_humidifier"
STOP_DEHUMIDIFIER = "switch.stop_dehumidifier"
STOP_BOILER = "switch.stop_boiler"
STOP_WHOLE_HOUSE_FAN = "switch.stop_whole_house_fan"
STOP_VALVE = "number.stop_valve"
SECOND_AC = "climate.test_ac_2"
STOP_CO2 = "sensor.stop_co2"
STOP_HUMIDITY = "sensor.stop_humidity"


async def _setup_recording(hass, config_data, *, room="20.0", outdoor="10.0", climate="off", extra=None, states=None, climate_age_s=None):
    """Set up a control-on entry with no heater. Every switch, climate and number service call is recorded as (domain, service, entity, value)."""
    set_inputs(hass)
    since = None if climate_age_s is None else time.time() - climate_age_s
    if since is not None:
        hass.states.async_remove(CLIMATE)  # set_inputs already set it, and an unchanged state keeps its old last_changed
    hass.states.async_set(CLIMATE, climate, {"temperature": 24.0, "friendly_name": "Test AC"}, timestamp=since)
    for entity in (ROOM_A, ROOM_B):
        hass.states.async_set(entity, room, {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(OUTDOOR, outdoor, {"unit_of_measurement": "°C", "device_class": "temperature"})
    for entity, state in (states or {}).items():
        hass.states.async_set(entity, state)
    calls = []

    def recorder(domain, service):
        async def record(call):
            value = call.data.get("hvac_mode", call.data.get("value", call.data.get("temperature")))
            calls.append((domain, service, call.data["entity_id"], value))

        hass.services.async_register(domain, service, record)

    for domain, service in (
        ("switch", "turn_on"),
        ("switch", "turn_off"),
        ("climate", "set_hvac_mode"),
        ("climate", "set_temperature"),
        ("number", "set_value"),
    ):
        recorder(domain, service)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data={**config_data, "control": True, **(extra or {})}, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, calls


async def _stop(hass, calls):
    calls.clear()
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    return list(calls)


@pytest.mark.parametrize(
    "switch, extra, states, setup_kwargs",
    [
        (STOP_FAN, {"fan": STOP_FAN, "co2": STOP_CO2}, {STOP_FAN: "off", STOP_CO2: "1500"}, {}),
        (STOP_HUMIDIFIER, {"humidifier": STOP_HUMIDIFIER, "room_humidity": STOP_HUMIDITY}, {STOP_HUMIDIFIER: "off", STOP_HUMIDITY: "30"}, {}),
        (STOP_DEHUMIDIFIER, {"dehumidifier": STOP_DEHUMIDIFIER, "room_humidity": STOP_HUMIDITY}, {STOP_DEHUMIDIFIER: "off", STOP_HUMIDITY: "70"}, {}),
        (STOP_BOILER, {"boiler": STOP_BOILER}, {STOP_BOILER: "off"}, {"outdoor": "0.0"}),
        (STOP_WHOLE_HOUSE_FAN, {"whole_house_fan": STOP_WHOLE_HOUSE_FAN}, {STOP_WHOLE_HOUSE_FAN: "off"}, {"room": "26.0", "outdoor": "18.0"}),
    ],
    ids=["fan", "humidifier", "dehumidifier", "boiler", "whole_house_fan"],
)
async def test_safety_stop_turns_off_a_switch_the_entry_turned_on(hass, enable_custom_integrations, config_data, switch, extra, states, setup_kwargs):
    entry, calls = await _setup_recording(hass, config_data, extra=extra, states=states, **setup_kwargs)
    assert ("switch", "turn_on", switch, None) in calls
    hass.states.async_set(switch, "on")
    assert ("switch", "turn_off", switch, None) in await _stop(hass, calls)


async def test_safety_stop_closes_a_valve_the_entry_opened(hass, enable_custom_integrations, config_data):
    entry, calls = await _setup_recording(hass, config_data, extra={"valve": STOP_VALVE}, states={STOP_VALVE: "0"})
    opened = [c for c in calls if c[:3] == ("number", "set_value", STOP_VALVE)]
    assert opened and opened[-1][3] > 0
    hass.states.async_set(STOP_VALVE, str(opened[-1][3]))
    assert ("number", "set_value", STOP_VALVE, 0.0) in await _stop(hass, calls)


async def test_safety_stop_turns_off_an_ac_the_entry_put_in_dry_mode(hass, enable_custom_integrations, config_data):
    extra = {"room_humidity": STOP_HUMIDITY, "dry_for_humidity": True}
    entry, calls = await _setup_recording(hass, config_data, room="23.0", extra=extra, states={STOP_HUMIDITY: "72"})
    assert ("climate", "set_hvac_mode", CLIMATE, "dry") in calls
    hass.states.async_set(CLIMATE, "dry", {"temperature": 24.0})
    assert ("climate", "set_hvac_mode", CLIMATE, "off") in await _stop(hass, calls)


async def test_safety_stop_turns_off_the_second_ac_the_entry_started(hass, enable_custom_integrations, config_data):
    entry, calls = await _setup_recording(
        hass, config_data, room="26.0", outdoor="30.0", climate="cool", extra={"climate_2": SECOND_AC}, states={SECOND_AC: "off"}
    )
    assert ("climate", "set_hvac_mode", SECOND_AC, "cool") in calls
    hass.states.async_set(SECOND_AC, "cool", {"temperature": 24.0})
    stop_calls = await _stop(hass, calls)
    assert ("climate", "set_hvac_mode", SECOND_AC, "off") in stop_calls
    # The first AC was already cooling when the entry started, so the entry never commanded it.
    assert not [c for c in stop_calls if c[2] == CLIMATE]


async def test_safety_stop_leaves_a_switch_the_entry_never_drove(hass, enable_custom_integrations, config_data):
    # The fan switch is unavailable while the entry runs, so it sends it no command. The user then turns it on.
    entry, calls = await _setup_recording(
        hass, config_data, extra={"fan": STOP_FAN, "co2": STOP_CO2}, states={STOP_FAN: "unavailable", STOP_CO2: "1500", "switch.other": "on"}
    )
    assert not [c for c in calls if c[2] == STOP_FAN]
    hass.states.async_set(STOP_FAN, "on")
    assert [c for c in await _stop(hass, calls) if c[0] == "switch"] == []


async def test_unload_turns_off_what_the_entry_turned_on(hass, enable_custom_integrations, config_data):
    entry, calls = await _setup_recording(hass, config_data, extra={"fan": STOP_FAN, "co2": STOP_CO2}, states={STOP_FAN: "off", STOP_CO2: "1500"})
    hass.states.async_set(STOP_FAN, "on")
    calls.clear()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert ("switch", "turn_off", STOP_FAN, None) in calls


async def test_cancelled_tick_during_unload_leaves_the_heater_off(hass, enable_custom_integrations, config_data):
    # Cancel a tick while its heater turn_on call is in flight. CancelledError passes through the tick, and the unload turns the heater off.
    entry, calls = await setup_heater(hass, config_data)
    coordinator = entry.runtime_data
    coordinator._unschedule_refresh()
    entered = asyncio.Event()

    async def hang_on(call):
        calls.append("turn_on")
        entered.set()
        await asyncio.sleep(3600)

    hass.services.async_register("switch", "turn_on", hang_on)
    task = hass.async_create_task(coordinator._async_update_data())
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    hass.states.async_set(HEATER, "on")
    calls.clear()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert calls == ["turn_off"]


async def test_keyboard_interrupt_in_a_tick_is_not_swallowed_by_the_heater_fallback(hass, enable_custom_integrations, config_data, monkeypatch):
    entry, calls = await setup_heater(hass, config_data, heater_state="on")
    coordinator = entry.runtime_data
    coordinator._unschedule_refresh()
    coordinator._heater_commanded_on = True

    async def interrupted(now, result):
        raise KeyboardInterrupt

    monkeypatch.setattr(coordinator, "_async_update_steps", interrupted)
    calls.clear()
    with pytest.raises(KeyboardInterrupt):
        await coordinator._async_update_data()
    assert calls == []


@pytest.mark.parametrize("missing", ["room", "outdoor"])
async def test_no_command_before_the_first_valid_readings(hass, enable_custom_integrations, config_data, missing):
    # Cold room with the outdoor sensor down: heater, boiler and valve would start. Hot room with no room reading: the AC would cool.
    extra = {
        "heater_switch": HEATER,
        "boiler": STOP_BOILER,
        "valve": STOP_VALVE,
        "fan": STOP_FAN,
        "co2": STOP_CO2,
        "room_humidity": STOP_HUMIDITY,
        "dehumidifier": STOP_DEHUMIDIFIER,
        "climate_2": SECOND_AC,
    }
    states = {HEATER: "off", STOP_BOILER: "off", STOP_VALVE: "0", STOP_FAN: "off", STOP_CO2: "1500", STOP_HUMIDITY: "70", STOP_DEHUMIDIFIER: "off", SECOND_AC: "off"}
    room, outdoor = ("unavailable", "0.0") if missing == "room" else ("26.0", "unavailable")
    entry, calls = await _setup_recording(hass, config_data, room=room, outdoor=outdoor, extra=extra, states=states)
    await refresh(hass, entry)
    starts = [c for c in calls if c[1] in ("turn_on", "set_value") or (c[1] == "set_hvac_mode" and c[3] != "off")]
    assert starts == []
    # Readings arrive. The normal decision applies: a hot room cools.
    for entity in (ROOM_A, ROOM_B):
        hass.states.async_set(entity, "26.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(OUTDOOR, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, entry)
    assert ("climate", "set_hvac_mode", CLIMATE, "cool") in calls
    assert ("switch", "turn_on", STOP_FAN, None) in calls


async def test_ac_switched_on_by_hand_is_stopped_at_once_when_the_entry_wants_it_off(hass, enable_custom_integrations, config_data, freezer):
    # The user turned the AC on 2 minutes ago. The room is cold, so the entry wants it off and no minimum on time holds the stop.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, calls = await _setup_recording(hass, config_data, climate="cool", extra={"ac_heat": False}, climate_age_s=120)
    assert ("climate", "set_hvac_mode", CLIMATE, "off") in calls


async def test_ac_the_entry_started_is_stopped_at_once_after_a_reload(hass, enable_custom_integrations, config_data, freezer, monkeypatch):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    hass.states.async_set(CLIMATE, "off", {"temperature": 24.0, "friendly_name": "Test AC"})
    set_room(hass, "26.0")
    calls = []

    async def record_mode(call):
        calls.append(call.data["hvac_mode"])
        state = hass.states.get(CLIMATE)
        hass.states.async_set(CLIMATE, call.data["hvac_mode"], dict(state.attributes), timestamp=time.time())

    async def record_temp(call):
        state = hass.states.get(CLIMATE)
        hass.states.async_set(CLIMATE, state.state, {**state.attributes, "temperature": call.data["temperature"]})

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    hass.services.async_register("climate", "set_temperature", record_temp)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data={**config_data, "control": True, "ac_heat": False}, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert calls == ["cool"]
    freezer.tick(timedelta(minutes=2))

    async def no_stop(self):
        return None

    # A restart leaves the AC running. The unload stop would turn it off first, so it is skipped here.
    monkeypatch.setattr(type(entry.runtime_data), "async_safety_stop", no_stop)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    set_room(hass, "20.0")
    await refresh(hass, entry)
    assert calls == ["cool", "off"]


async def _hard_restart(hass, entry, monkeypatch):
    """Unload and set up again without the unload stop, as a crash or a power cut leaves the devices. The store still saves."""

    async def no_stop(self):
        return None

    with monkeypatch.context() as patch:
        patch.setattr(type(entry.runtime_data), "async_safety_stop", no_stop)
        assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    return no_stop


async def test_manual_setpoint_change_while_ha_was_down_is_an_external_change(hass, enable_custom_integrations, config_data, monkeypatch):
    entry, calls = await _setup_recording(hass, config_data, room="26.0", outdoor="30.0", climate="cool")
    written = [c for c in calls if c[:2] == ("climate", "set_temperature")]
    assert written
    assert entry.runtime_data.data["external_change"] is False
    await _hard_restart(hass, entry, monkeypatch)
    # Someone sets the AC to 19 C while Home Assistant is down.
    hass.states.async_set(CLIMATE, "cool", {"temperature": 19.0, "friendly_name": "Test AC"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data._last_written_setpoint == written[-1][3]
    assert entry.runtime_data.data["external_change"] is True


async def test_safety_stop_after_a_restart_turns_off_a_device_the_previous_process_left_on(hass, enable_custom_integrations, config_data, monkeypatch):
    extra = {"fan": STOP_FAN, "co2": STOP_CO2}
    entry, calls = await _setup_recording(hass, config_data, extra=extra, states={STOP_FAN: "off", STOP_CO2: "1500"})
    assert ("switch", "turn_on", STOP_FAN, None) in calls
    hass.states.async_set(STOP_FAN, "on")
    await _hard_restart(hass, entry, monkeypatch)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # The new process sent no turn_on: the fan was already on. It still knows it left the fan on.
    assert ("switch", "turn_off", STOP_FAN, None) in await _stop(hass, calls)


async def test_commanded_state_loads_from_an_old_payload(hass, enable_custom_integrations, config_data, hass_storage):
    entry, _ = await _setup_recording(hass, config_data)
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass_storage[f"hvac_horizon.{entry.entry_id}"] = {"version": 3, "minor_version": 1, "key": f"hvac_horizon.{entry.entry_id}", "data": {"entries": []}}
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data._switches_on == set()


async def test_shed_load_the_user_changed_after_the_shed_is_not_restored(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    calls = []

    async def record_on(call):
        calls.append(("on", call.data["entity_id"]))
        hass.states.async_set(call.data["entity_id"], "on")

    async def record_off(call):
        calls.append(("off", call.data["entity_id"]))
        hass.states.async_set(call.data["entity_id"], "off", context=call.context, timestamp=time.time())

    async def ignore(call):
        return None

    hass.services.async_register("switch", "turn_on", record_on)
    hass.services.async_register("switch", "turn_off", record_off)
    hass.services.async_register("climate", "set_hvac_mode", ignore)
    hass.services.async_register("climate", "set_temperature", ignore)
    hass.states.async_set("sensor.test_house_power", "5000", {"unit_of_measurement": "W"})
    hass.states.async_set("switch.test_low", "on")
    data = dict(config_data, control=True, whole_house_power="sensor.test_house_power", shed_max_w=4000, shed_loads=["switch.test_low|500"])
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert calls == [("off", "switch.test_low")]
    assert entry.runtime_data._shed_at["switch.test_low"] == time.time()

    # The user turns the load on, then off again.
    freezer.tick(timedelta(minutes=1))
    hass.states.async_set("switch.test_low", "on", timestamp=time.time())
    freezer.tick(timedelta(minutes=1))
    hass.states.async_set("switch.test_low", "off", timestamp=time.time())
    calls.clear()
    hass.states.async_set("sensor.test_house_power", "2000", {"unit_of_measurement": "W"})
    await refresh(hass, entry)
    assert calls == []
    assert "restored" not in entry.runtime_data.data
    assert entry.runtime_data._shed_loads == set()


async def test_rooms_max_age_is_the_age_of_the_oldest_reading_used(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await enable_sensors(hass, entry, "rooms_max_age_s")  # disabled by default
    assert entry.runtime_data.data["rooms_max_age_s"] == 0.0
    # Room A last reported 6 minutes ago, inside the 15 minute stall window, so the median still uses it.
    freezer.tick(timedelta(minutes=6))
    hass.states.async_set(ROOM_B, "26.4", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(OUTDOOR, "30.5", {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(POWER, "1105", {"unit_of_measurement": "W", "device_class": "power"})
    await refresh(hass, entry)
    assert entry.runtime_data.data["rooms_max_age_s"] == 360.0
    state = hass.states.get("sensor.hvac_horizon_test_ac_room_reading_age")
    assert state is not None and state.state == "360.0"
    assert state.attributes["unit_of_measurement"] == "s"


def _room_climate_id(hass, entry):
    return er.async_get(hass).async_get_entity_id("climate", DOMAIN, f"{entry.entry_id}_room")


async def test_room_entity_shows_the_eco_cool_threshold_and_the_forecast_uses_it(hass, enable_custom_integrations, config_data):
    entry, _ = await setup_heater(hass, config_data, room="25.0", extra={CONF_ECO_C: 26.0})
    hass.states.async_set(PRICE, "0.275", hass.states.get(PRICE).attributes)
    await hass.services.async_call(DOMAIN, "start_timed_preset", {"entry_id": entry.entry_id, "preset": "eco", "minutes": 60})
    targets = []
    original = entry.runtime_data._forecast

    def spy(now, target_c=None):
        targets.append(target_c)
        return original(now, target_c)

    entry.runtime_data._forecast = spy
    await refresh(hass, entry)
    state = hass.states.get(_room_climate_id(hass, entry))
    assert state.attributes["temperature"] == 26.0
    assert targets[-1] == pytest.approx(26.0)
    assert entry.runtime_data.data["target_c"] == 26.0


async def test_room_entity_set_temperature_sets_the_comfort_target(hass, enable_custom_integrations, config_data):
    entry, _ = await setup_heater(hass, config_data, room="25.0", extra={CONF_ECO_C: 26.0})
    hass.states.async_set(PRICE, "0.275", hass.states.get(PRICE).attributes)
    entity_id = _room_climate_id(hass, entry)
    # setup_heater replaces the climate.set_temperature service with a fake, so call the entity itself.
    await hass.data["climate"].get_entity(entity_id).async_set_temperature(temperature=23.0)
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.attributes["temperature"] == 23.0
    assert state.attributes["preset_mode"] == "comfort"


async def test_room_entity_action_follows_the_devices(hass, enable_custom_integrations, config_data):
    entry, _ = await setup_heater(hass, config_data, room="25.0", climate="cool")
    entity_id = _room_climate_id(hass, entry)
    assert hass.states.get(entity_id).attributes["hvac_action"] == "cooling"
    hass.states.async_set(CLIMATE, "off", {"temperature": 22.0})
    hass.states.async_set(HEATER, "on")
    await refresh(hass, entry)
    assert hass.states.get(entity_id).attributes["hvac_action"] == "heating"
    hass.states.async_set(HEATER, "off")
    await refresh(hass, entry)
    assert hass.states.get(entity_id).attributes["hvac_action"] == "idle"


async def test_room_entity_says_observe_only_when_control_is_off(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(_room_climate_id(hass, entry))
    assert state.state == "off"
    assert state.attributes["control"] == "observe only"
    assert state.attributes["hvac_action"] == "cooling"


FLAP_SWITCH = "switch.flap_output"
FLAP_SENSOR = "sensor.flap_reading"
FLAP_COVER = "cover.test_window"


async def _flap_run(hass, config_data, freezer, *, extra, readings, sensor=FLAP_SENSOR, room="20.0", outdoor="10.0", climate="off", watch="switch"):
    """Run 6 ticks with `readings` (one per tick) on `sensor` and return every output call.

    The fake services move the entity state, as a real switch, cover or AC does, so the next tick sees what was sent.
    `sensor` is a sensor entity id, or "outdoor" or "room" to move that temperature input instead.
    """
    freezer.move_to("2026-11-20T10:00:00+00:00")
    set_inputs(hass)
    hass.states.async_set(CLIMATE, climate, {"temperature": 24.0, "friendly_name": "Test AC"})
    outputs = []

    def feed(value):
        if sensor == "outdoor":
            hass.states.async_set(OUTDOOR, value, {"unit_of_measurement": "°C", "device_class": "temperature"})
            _set_ac_rooms(hass, room)
        elif sensor == "room":
            _set_ac_rooms(hass, value)
            hass.states.async_set(OUTDOOR, outdoor, {"unit_of_measurement": "°C", "device_class": "temperature"})
        else:
            hass.states.async_set(sensor, value)
            _set_ac_rooms(hass, room)
            hass.states.async_set(OUTDOOR, outdoor, {"unit_of_measurement": "°C", "device_class": "temperature"})

    feed(readings[0])
    hass.states.async_set(FLAP_SWITCH, "off")
    hass.states.async_set(FLAP_COVER, "closed")

    def mover(domain, service, entity_state):
        async def move(call):
            outputs.append((domain, service, call.data["entity_id"], call.data.get("hvac_mode")))
            hass.states.async_set(call.data["entity_id"], entity_state(call))

        hass.services.async_register(domain, service, move)

    mover("switch", "turn_on", lambda c: "on")
    mover("switch", "turn_off", lambda c: "off")
    mover("cover", "open_cover", lambda c: "open")
    mover("cover", "close_cover", lambda c: "closed")
    mover("climate", "set_hvac_mode", lambda c: c.data["hvac_mode"])

    async def ignore(call):
        return None

    hass.services.async_register("climate", "set_temperature", ignore)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data={**config_data, "control": True, **extra}, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for index, value in enumerate(readings[1:], start=1):
        freezer.tick(timedelta(minutes=5))
        feed(value)
        # Move the room down by a hundredth each tick so no room sensor reads as stalled.
        if sensor != "room":
            _set_ac_rooms(hass, f"{float(room) - 0.01 * index:.2f}")
        await refresh(hass, entry)
    return [o for o in outputs if o[0] == watch]


ACROSS = 6  # ticks


async def test_fan_pm25_does_not_flap_across_its_threshold(hass, enable_custom_integrations, config_data, freezer):
    # PM2.5 turns the fan on at 25 and holds it until 22. Readings of 26 and 24 alternate for 6 ticks.
    outputs = await _flap_run(hass, config_data, freezer, extra={"fan": FLAP_SWITCH, "pm25": FLAP_SENSOR}, readings=["26", "24"] * 3)
    assert outputs == [("switch", "turn_on", FLAP_SWITCH, None)]


async def test_humidifier_does_not_flap_across_the_low_edge(hass, enable_custom_integrations, config_data, freezer):
    outputs = await _flap_run(hass, config_data, freezer, extra={"humidifier": FLAP_SWITCH, "room_humidity": FLAP_SENSOR}, readings=["39", "41"] * 3)
    assert outputs == [("switch", "turn_on", FLAP_SWITCH, None)]


async def test_dehumidifier_does_not_flap_across_the_high_edge(hass, enable_custom_integrations, config_data, freezer):
    outputs = await _flap_run(hass, config_data, freezer, extra={"dehumidifier": FLAP_SWITCH, "room_humidity": FLAP_SENSOR}, readings=["61", "59"] * 3)
    assert outputs == [("switch", "turn_on", FLAP_SWITCH, None)]


async def test_ac_dry_mode_does_not_flap_across_the_high_edge(hass, enable_custom_integrations, config_data, freezer):
    outputs = await _flap_run(
        hass, config_data, freezer, extra={"dry_for_humidity": True, "room_humidity": FLAP_SENSOR}, readings=["61", "59"] * 3, watch="climate"
    )
    assert outputs == [("climate", "set_hvac_mode", CLIMATE, "dry")]


async def test_whole_house_fan_does_not_flap_across_its_gap(hass, enable_custom_integrations, config_data, freezer):
    # Indoors 24 C, just under the cool threshold so the AC stays off. The fan starts at a 2 K gap and holds until 1.5 K.
    outputs = await _flap_run(
        hass, config_data, freezer, extra={"whole_house_fan": FLAP_SWITCH}, readings=["21.9", "22.1"] * 3, sensor="outdoor", room="24.0"
    )
    assert outputs == [("switch", "turn_on", FLAP_SWITCH, None)]


async def test_window_actuator_does_not_flap_across_the_outdoor_gap(hass, enable_custom_integrations, config_data, freezer):
    outputs = await _flap_run(
        hass, config_data, freezer, extra={"window_actuator": FLAP_COVER}, readings=["23.9", "24.1"] * 3, sensor="outdoor", room="26.0", watch="cover"
    )
    assert outputs == [("cover", "open_cover", FLAP_COVER, None)]


async def test_window_actuator_does_not_flap_across_the_cool_threshold(hass, enable_custom_integrations, config_data, freezer):
    # The cool threshold is 24.04 C. Room readings of 24.1 and 23.9 alternate with outdoors at 18 C.
    outputs = await _flap_run(
        hass, config_data, freezer, extra={"window_actuator": FLAP_COVER}, readings=["24.1", "23.9"] * 3, sensor="room", outdoor="18.0", watch="cover"
    )
    assert outputs == [("cover", "open_cover", FLAP_COVER, None)]


def _sparse_on_entries(now):
    """364 readings with 22 AC-on samples: too few cycles for the single fit, enough for the room-rate fit."""
    rng = random.Random(11)
    temp, readings = 24.0, []
    for i in range(364):
        outdoor = 27.0 + 6.0 * math.sin(i / 9.0)
        ac = 1.0 if 100 <= i < 111 or 250 <= i < 261 else 0.0
        mode = "cool" if ac else "off"
        readings.append(asdict(Entry(t=now - (364 - i) * 300, room_c=temp, outdoor_c=outdoor, power_w=1000.0 * ac, mode=mode, setpoint_c=24.0 if ac else None)))
        temp += (0.3 * (outdoor - temp) + 0.2 - 1.4 * ac) * (5 / 60) + rng.gauss(0, 0.01)
    return readings


_QUALITY_KEYS = ("fit_residual_k_per_h", "k_stderr", "c_stderr", "g_stderr", "fit_outliers_dropped", "fit_confidence")


async def test_fit_quality_is_published_for_the_room_rate_fit(hass, enable_custom_integrations, config_data):
    _register_climate_fakes(hass)
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    coordinator._entries = [Entry(**e) for e in _sparse_on_entries(dt_util.utcnow().timestamp())]
    coordinator._refit()
    assert coordinator._fit is None and coordinator._tpi is not None
    await refresh(hass, entry)
    data = coordinator.data
    assert data["k"] is not None
    for key in _QUALITY_KEYS:
        assert data[key] is not None, key
    assert data["fit_confidence"] in ("low", "medium", "high")
    assert data["c_stderr"] > 0
    assert data["status"] == "reachable"
    assert data["minutes_low"] is not None  # minutes_high stays None when c minus one standard error cannot reach the target


async def test_fit_quality_is_published_after_a_store_restore(hass, enable_custom_integrations, config_data, hass_storage):
    _register_climate_fakes(hass)
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    hass_storage[f"hvac_horizon.{entry.entry_id}"] = {
        "version": 2, "minor_version": 1, "key": f"hvac_horizon.{entry.entry_id}",
        "data": {"entries": _sparse_on_entries(dt_util.utcnow().timestamp())},
    }
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    data = entry.runtime_data.data
    assert entry.runtime_data._fit is None
    assert data["k"] is not None and data["c"] is not None and data["g"] is not None
    for key in _QUALITY_KEYS:
        assert data[key] is not None, key
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_status")
    attributes = hass.states.get(entity_id).attributes
    assert attributes["c_stderr"] == data["c_stderr"] and attributes["fit_confidence"] == data["fit_confidence"]


# ---- immediate off and the restart delay -------------------------------------------------------------------


def _device_services(hass):
    """Fake climate and switch services for the room's devices.

    Each call is recorded as (service, entity, value) and the new state is mirrored with the current time, as a real device does.
    Calls for the room's own climate entity go to the real services.
    """
    calls = []
    services = hass.services.async_services_internal()["climate"]
    real = {name: services[name] for name in ("set_hvac_mode", "set_temperature")}

    def stamp():
        return dt_util.utcnow().timestamp()

    def target_of(call):
        target = call.data["entity_id"]
        return target[0] if isinstance(target, list) else target

    def climate_handler(name):
        async def handler(call):
            target = target_of(call)
            if target.startswith("climate.hvac_horizon"):
                return await hass.async_run_hass_job(real[name].job, call)
            state = hass.states.get(target)
            if name == "set_hvac_mode":
                calls.append((name, target, call.data["hvac_mode"]))
                hass.states.async_set(target, call.data["hvac_mode"], dict(state.attributes), timestamp=stamp())
            else:
                calls.append((name, target, call.data["temperature"]))
                hass.states.async_set(target, state.state, {**state.attributes, "temperature": call.data["temperature"]}, timestamp=stamp())

        return handler

    def switch_handler(name, new_state):
        async def handler(call):
            target = target_of(call)
            calls.append((name, target, None))
            hass.states.async_set(target, new_state, timestamp=stamp())

        return handler

    schemas = {name: services[name].schema for name in real}
    for name in real:
        hass.services.async_register("climate", name, climate_handler(name), schema=schemas[name])
    hass.services.async_register("switch", "turn_on", switch_handler("turn_on", "on"))
    hass.services.async_register("switch", "turn_off", switch_handler("turn_off", "off"))
    return calls


async def _setup_with_devices(hass, config_data, *, room="26.0", outdoor="30.0", states=None, extra=None):
    """A control-on entry with fake device services. `states` sets other entities before setup. Returns the entry, the room entity id and the calls."""
    set_inputs(hass)
    calls = _device_services(hass)
    hass.states.async_set(CLIMATE, "off", {"temperature": 24.0, "friendly_name": "Test AC", "target_temp_step": 1.0})
    set_room(hass, room)
    hass.states.async_set(OUTDOOR, outdoor, {"unit_of_measurement": "°C", "device_class": "temperature"})
    for entity, (state, attributes) in (states or {}).items():
        hass.states.async_set(entity, state, attributes)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data={**config_data, "control": True, **(extra or {})}, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id("climate", DOMAIN, ROOM_CLIMATE_UNIQUE.format(entry.entry_id))
    return entry, entity_id, calls


async def _room_off(hass, entity_id):
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": "off"}, blocking=True)
    await hass.async_block_till_done()


async def test_room_off_stops_both_acs_at_once_inside_the_minimum_on_time(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    second = "climate.test_ac_2"
    entry, entity_id, calls = await _setup_with_devices(
        hass, config_data, states={CLIMATE: ("cool", {"temperature": 24.0, "target_temp_step": 1.0}), second: ("off", {"temperature": 24.0})},
        extra={"climate_2": second, "min_on_min": 30},
    )
    assert ("set_hvac_mode", second, "cool") in calls
    freezer.tick(timedelta(seconds=96))  # the owner's case: 1.6 minutes after the AC entered cool
    calls.clear()
    await _room_off(hass, entity_id)
    assert ("set_hvac_mode", CLIMATE, "off") in calls
    assert ("set_hvac_mode", second, "off") in calls
    assert hass.states.get(CLIMATE).state == "off" and hass.states.get(second).state == "off"
    assert hass.states.get(entity_id).state == "off"
    assert entry.runtime_data.data["action"] == "off: user turned the room off"


async def test_room_off_turns_the_heater_switch_off_at_once(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, entity_id, calls = await _setup_with_devices(
        hass, config_data, room="20.0", outdoor="10.0", states={HEATER: ("off", {})}, extra={CONF_HEATER_SWITCH: HEATER}
    )
    assert ("turn_on", HEATER, None) in calls
    freezer.tick(timedelta(minutes=1))
    calls.clear()
    await _room_off(hass, entity_id)
    assert ("turn_off", HEATER, None) in calls
    assert hass.states.get(HEATER).state == "off"


async def test_room_off_turns_every_fleet_device_off_at_once(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    fleet = [
        {"device_id": "small", "kind": "cool", "entity_id": SMALL_AC, "rate_k_per_h_per_kw": 2.0, "rated_w": 900.0},
        {"device_id": "ir", "kind": "heat", "entity_id": IR_HEATER, "rate_k_per_h_per_kw": 1.5, "rated_w": 1500.0},
    ]
    entry, entity_id, calls = await _setup_with_devices(
        hass, config_data, states={SMALL_AC: ("off", {"temperature": 30.0}), IR_HEATER: ("off", {})}, extra={"fleet": fleet, "dispatch_mode": "fast"}
    )
    assert ("set_hvac_mode", SMALL_AC, "cool") in calls
    hass.states.async_set(IR_HEATER, "on")  # left on by hand
    freezer.tick(timedelta(minutes=1))
    calls.clear()
    await _room_off(hass, entity_id)
    assert ("set_hvac_mode", SMALL_AC, "off") in calls
    assert ("turn_off", IR_HEATER, None) in calls


async def test_room_off_goes_out_inside_the_inhibit_window(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, entity_id, calls = await _setup_with_devices(hass, config_data)
    assert ("set_hvac_mode", CLIMATE, "cool") in calls
    entry.runtime_data._inhibit_until_min = dt_util.utcnow().timestamp() / 60.0 + 30.0
    calls.clear()
    await _room_off(hass, entity_id)
    assert ("set_hvac_mode", CLIMATE, "off") in calls


async def test_stop_is_not_held_by_the_minimum_on_time_and_restart_waits_for_the_off_time(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, entity_id, calls = await _setup_with_devices(hass, config_data, extra={"min_on_min": 30})
    assert calls[0] == ("set_hvac_mode", CLIMATE, "cool")
    freezer.tick(timedelta(minutes=2))
    set_room(hass, "22.0")  # below the threshold: the decision is off
    await refresh(hass, entry)
    assert calls[-1] == ("set_hvac_mode", CLIMATE, "off")
    assert entry.runtime_data.data["pending"] is None
    # One minute later the room is hot again. The AC stopped 1 minute ago and the restart delay is 3 minutes.
    freezer.tick(timedelta(minutes=1))
    set_room(hass, "26.0")
    calls.clear()
    await refresh(hass, entry)
    assert calls == []
    assert entry.runtime_data.data["action"] == "cool: waiting for restart delay"
    assert entry.runtime_data.data["pending"] == "start in 2 min: restart delay"
    assert hass.states.get(entity_id).attributes["pending"] == "start in 2 min: restart delay"
    # The re-check fires when the delay ends. No tick runs in between.
    freezer.tick(timedelta(seconds=121))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert calls == [("set_hvac_mode", CLIMATE, "cool")]
    assert entry.runtime_data.data["pending"] is None
    assert hass.states.get(entity_id).attributes["pending"] is None


async def test_restart_delay_of_zero_is_disabled(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, entity_id, calls = await _setup_with_devices(hass, config_data, extra={"min_off_min": 0})
    freezer.tick(timedelta(minutes=1))
    set_room(hass, "22.0")
    await refresh(hass, entry)
    assert calls[-1] == ("set_hvac_mode", CLIMATE, "off")
    freezer.tick(timedelta(seconds=30))
    set_room(hass, "26.0")
    await refresh(hass, entry)
    assert calls[-1] == ("set_hvac_mode", CLIMATE, "cool")
    assert entry.runtime_data.data["pending"] is None


async def test_recheck_is_cancelled_when_the_decision_changes_and_on_unload(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, entity_id, calls = await _setup_with_devices(hass, config_data)
    coordinator = entry.runtime_data
    freezer.tick(timedelta(minutes=1))
    set_room(hass, "22.0")
    await refresh(hass, entry)
    freezer.tick(timedelta(seconds=30))
    set_room(hass, "26.0")
    await refresh(hass, entry)
    assert coordinator.data["pending"] is not None and coordinator._recheck_cancel is not None
    # The room cools, so the decision is off and nothing is pending.
    set_room(hass, "22.0")
    await refresh(hass, entry)
    assert coordinator.data["pending"] is None and coordinator._recheck_cancel is None
    # A pending start again, then unload.
    set_room(hass, "26.0")
    await refresh(hass, entry)
    assert coordinator._recheck_cancel is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert coordinator._recheck_cancel is None


# ---- a room held off stays off across a restart -------------------------------------------------------------


OFF_ENTRY_ID = "stored-off-entry"


def _store_user_off(hass_storage, **extra):
    hass_storage[f"hvac_horizon.{OFF_ENTRY_ID}"] = {
        "version": 2, "minor_version": 1, "key": f"hvac_horizon.{OFF_ENTRY_ID}", "data": {"entries": [], "user_off": True, **extra},
    }


def _starts(calls):
    """Commands that turn something on or open it: a mode other than off, a switch on or a valve above 0."""
    return [
        c for c in calls
        if c[0] == "turn_on" or (c[0] == "set_hvac_mode" and c[2] != "off") or (c[0] == "set_value" and c[2] > 0)
    ]


@pytest.mark.parametrize("room,outdoor", [("29.0", "32.0"), ("26.0", "30.0"), ("23.8", "20.0"), ("15.0", "5.0")], ids=["hot", "warm", "inside_band", "cold"])
async def test_room_held_off_sends_no_start_on_any_tick_after_a_restart(hass, enable_custom_integrations, config_data, hass_storage, freezer, room, outdoor):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    _store_user_off(hass_storage)
    set_inputs(hass)
    calls = _device_services(hass)

    async def record_value(call):
        calls.append(("set_value", call.data["entity_id"], call.data["value"]))

    hass.services.async_register("number", "set_value", record_value)
    boiler, valve, second, fleet_ac, fleet_heater = "switch.boiler", "number.valve", "climate.test_ac_2", SMALL_AC, IR_HEATER
    for entity, state in ((HEATER, "off"), (boiler, "off"), (valve, "0"), (fleet_heater, "off")):
        hass.states.async_set(entity, state)
    for entity in (CLIMATE, second, fleet_ac):
        hass.states.async_set(entity, "off", {"temperature": 24.0, "target_temp_step": 1.0})
    set_room(hass, room)
    hass.states.async_set(OUTDOOR, outdoor, {"unit_of_measurement": "°C", "device_class": "temperature"})
    data = {**config_data, "control": True, CONF_HEATER_SWITCH: HEATER, "boiler": boiler, "valve": valve, "climate_2": second, "ac_heat": True}
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", entry_id=OFF_ENTRY_ID, data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id("climate", DOMAIN, ROOM_CLIMATE_UNIQUE.format(entry.entry_id))
    assert entry.runtime_data.user_off is True
    for _ in range(6):  # 30 minutes of ticks
        assert hass.states.get(entity_id).state == "off"
        assert _starts(calls) == []
        freezer.tick(timedelta(minutes=5))
        set_room(hass, room)
        hass.states.async_set(OUTDOOR, outdoor, {"unit_of_measurement": "°C", "device_class": "temperature"})
        await refresh(hass, entry)
    assert _starts(calls) == []
    assert hass.states.get(entity_id).state == "off"


async def test_fleet_held_off_sends_no_on_command_after_a_restart(hass, enable_custom_integrations, config_data, hass_storage, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    _store_user_off(hass_storage)
    fleet = [
        {"device_id": "small", "kind": "cool", "entity_id": SMALL_AC, "rate_k_per_h_per_kw": 2.0, "rated_w": 900.0},
        {"device_id": "ir", "kind": "heat", "entity_id": IR_HEATER, "rate_k_per_h_per_kw": 1.5, "rated_w": 1500.0},
    ]
    for room, outdoor in (("29.0", "32.0"), ("15.0", "5.0")):
        set_inputs(hass)
        calls = _device_services(hass)
        hass.states.async_set(SMALL_AC, "off", {"temperature": 30.0})
        hass.states.async_set(IR_HEATER, "off")
        set_room(hass, room)
        hass.states.async_set(OUTDOOR, outdoor, {"unit_of_measurement": "°C", "device_class": "temperature"})
        entry = MockConfigEntry(
            domain=DOMAIN, title="Test AC", entry_id=OFF_ENTRY_ID, data={**config_data, "control": True, "fleet": fleet}, unique_id=CLIMATE
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        for _ in range(4):
            freezer.tick(timedelta(minutes=5))
            set_room(hass, room)
            hass.states.async_set(OUTDOOR, outdoor, {"unit_of_measurement": "°C", "device_class": "temperature"})
            await refresh(hass, entry)
        assert _starts(calls) == []
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await hass.config_entries.async_remove(entry.entry_id)
        hass_storage.clear()
        _store_user_off(hass_storage)


async def test_stored_hold_is_read_before_the_first_tick(hass, enable_custom_integrations, config_data, hass_storage, monkeypatch):
    from custom_components.hvac_horizon.coordinator import HorizonCoordinator

    _store_user_off(hass_storage)
    seen = []
    original = HorizonCoordinator._async_update_data

    async def spy(self):
        seen.append(self._user_off)
        return await original(self)

    monkeypatch.setattr(HorizonCoordinator, "_async_update_data", spy)
    set_inputs(hass)
    calls = _device_services(hass)
    hass.states.async_set(CLIMATE, "off", {"temperature": 24.0, "target_temp_step": 1.0})
    set_room(hass, "29.0")
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", entry_id=OFF_ENTRY_ID, data={**config_data, "control": True}, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert seen and seen[0] is True
    assert _starts(calls) == []


async def test_a_tick_before_the_store_is_loaded_still_reads_the_hold(hass, enable_custom_integrations, config_data, hass_storage):
    from custom_components.hvac_horizon.coordinator import HorizonCoordinator

    _store_user_off(hass_storage)
    set_inputs(hass)
    calls = _device_services(hass)
    hass.states.async_set(CLIMATE, "off", {"temperature": 24.0, "target_temp_step": 1.0})
    set_room(hass, "29.0")
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", entry_id=OFF_ENTRY_ID, data={**config_data, "control": True}, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    coordinator = HorizonCoordinator(hass, entry)
    assert coordinator._user_off is False  # nothing is read yet
    await coordinator.async_refresh()  # a tick with no async_load first
    assert coordinator._user_off is True
    assert _starts(calls) == []
    await coordinator.async_shutdown()


async def test_a_held_room_starts_again_only_after_the_user_sets_a_mode(hass, enable_custom_integrations, config_data, hass_storage, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    _store_user_off(hass_storage)
    set_inputs(hass)
    calls = _device_services(hass)
    hass.states.async_set(CLIMATE, "off", {"temperature": 24.0, "target_temp_step": 1.0})
    set_room(hass, "29.0")
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", entry_id=OFF_ENTRY_ID, data={**config_data, "control": True}, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id("climate", DOMAIN, ROOM_CLIMATE_UNIQUE.format(entry.entry_id))
    for _ in range(3):
        freezer.tick(timedelta(minutes=5))
        set_room(hass, "29.0")
        hass.states.async_set(OUTDOOR, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
        await refresh(hass, entry)
    assert _starts(calls) == []
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": "cool"}, blocking=True)
    await hass.async_block_till_done()
    assert entry.runtime_data.user_off is False
    assert ("set_hvac_mode", CLIMATE, "cool") in calls


# ---- setpoint regulation against the AC's own sensor --------------------------------------------------------


def _ac_attrs(**extra):
    return {"temperature": 28.0, "target_temp_step": 1.0, "min_temp": 16.0, "max_temp": 30.0, **extra}


async def _tick(hass, entry, freezer, minutes=5, room="25.5", power=None):
    freezer.tick(timedelta(minutes=minutes))
    set_room(hass, room)
    hass.states.async_set(OUTDOOR, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    if power is not None:
        hass.states.async_set(POWER, power, {"unit_of_measurement": "W", "device_class": "power"})
    await refresh(hass, entry)


def _setpoints(calls, entity=CLIMATE):
    return [c[2] for c in calls if c[0] == "set_temperature" and c[1] == entity]


async def test_cool_setpoint_is_lowered_by_the_offset_of_the_ac_sensor(hass, enable_custom_integrations, config_data, freezer):
    # Room 25.5 C, the AC reads 23 C: the offset is 2.5 K. Target about 23.9 minus 2.5 is 21.4, and the 1 K step gives 21.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _, calls = await _setup_with_devices(
        hass, config_data, room="25.5", states={CLIMATE: ("off", _ac_attrs(current_temperature=23.0))}, extra={"comfort_c": 23.9}
    )
    assert _setpoints(calls) == [21.0]
    data = entry.runtime_data.data
    assert data["ac_setpoint_sent_c"] == 21.0
    assert data["ac_sensor_offset_k"] == 2.5
    # The next tick sees its own setpoint on the device. It is not a manual change.
    await _tick(hass, entry, freezer)
    assert data is not entry.runtime_data.data
    assert entry.runtime_data.data["external_change"] is False
    assert entry.runtime_data._inhibit_until_min is None
    assert entry.runtime_data.data["action"].startswith("cool")
    assert _setpoints(calls) == [21.0]
    sensor_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_ac_setpoint_sent_c")
    assert float(hass.states.get(sensor_id).state) == 21.0
    assert er.async_get(hass).async_get(sensor_id).entity_category is EntityCategory.DIAGNOSTIC and not er.async_get(hass).async_get(sensor_id).disabled


async def test_no_offset_when_the_ac_reads_the_room_temperature(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _, calls = await _setup_with_devices(
        hass, config_data, room="25.5", states={CLIMATE: ("off", _ac_attrs(current_temperature=25.5))}, extra={"comfort_c": 23.9}
    )
    assert _setpoints(calls) == [24.0]
    assert entry.runtime_data.data["ac_sensor_offset_k"] == 0.0


async def test_ac_reading_warmer_than_the_room_gives_no_cool_offset(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _, calls = await _setup_with_devices(
        hass, config_data, room="25.5", states={CLIMATE: ("off", _ac_attrs(current_temperature=27.0))}, extra={"comfort_c": 23.9}
    )
    assert _setpoints(calls) == [24.0]
    assert entry.runtime_data.data["ac_sensor_offset_k"] == 0.0


async def test_regulated_setpoint_is_clamped_at_the_device_min_temp(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _, calls = await _setup_with_devices(
        hass, config_data, room="25.5", states={CLIMATE: ("off", _ac_attrs(current_temperature=15.0, min_temp=17.0))}, extra={"comfort_c": 23.9}
    )
    assert _setpoints(calls) == [17.0]
    assert entry.runtime_data.data["ac_setpoint_sent_c"] == 17.0


async def test_setpoint_regulation_can_be_turned_off(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _, calls = await _setup_with_devices(
        hass, config_data, room="25.5", states={CLIMATE: ("off", _ac_attrs(current_temperature=23.0))},
        extra={"comfort_c": 23.9, "setpoint_regulation": False},
    )
    assert _setpoints(calls) == [24.0]
    assert entry.runtime_data.data["ac_sensor_offset_k"] is None


async def test_power_fallback_steps_the_setpoint_down_then_back_up(hass, enable_custom_integrations, config_data, freezer):
    # The AC reports no temperature. It runs the fan only (50 W), so the setpoint steps down one device step per tick.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _, calls = await _setup_with_devices(
        hass, config_data, room="25.5", states={CLIMATE: ("off", _ac_attrs()), POWER: ("50", {"unit_of_measurement": "W", "device_class": "power"})},
        extra={"comfort_c": 23.9},
    )
    assert _setpoints(calls) == [24.0]
    # Under 10 minutes in cool: no move yet.
    await _tick(hass, entry, freezer, power="50")
    assert _setpoints(calls) == [24.0]
    for expected in (23.0, 22.0, 21.0):
        await _tick(hass, entry, freezer, power="50")
        assert _setpoints(calls)[-1] == expected
        assert entry.runtime_data.data["ac_setpoint_sent_c"] == expected
    assert entry.runtime_data.data["ac_sensor_offset_k"] == 3.0
    assert entry.runtime_data.data["external_change"] is False and entry.runtime_data._inhibit_until_min is None
    # The compressor runs: the setpoint comes back toward the target one step per tick.
    for expected in (22.0, 23.0, 24.0):
        await _tick(hass, entry, freezer, power="1100")
        assert _setpoints(calls)[-1] == expected
    assert entry.runtime_data.data["ac_sensor_offset_k"] == 0.0
    # Fan only again, but the room has reached the threshold: no new drop.
    await _tick(hass, entry, freezer, room="23.0", power="50")
    assert _setpoints(calls)[-1] == 24.0


async def test_power_fallback_stops_at_the_device_min_temp(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _, calls = await _setup_with_devices(
        hass, config_data, room="25.5", states={CLIMATE: ("off", _ac_attrs(min_temp=22.0)), POWER: ("50", {"unit_of_measurement": "W", "device_class": "power"})},
        extra={"comfort_c": 23.9},
    )
    for _ in range(8):
        await _tick(hass, entry, freezer, power="50")
    assert min(_setpoints(calls)) == 22.0
    assert entry.runtime_data.data["ac_setpoint_sent_c"] == 22.0


async def test_ac_heat_setpoint_is_raised_by_the_offset_when_the_ac_reads_warmer(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _, calls = await _setup_with_devices(
        hass, config_data, room="20.0", outdoor="10.0", states={CLIMATE: ("off", _ac_attrs(current_temperature=22.0, hvac_modes=["off", "cool", "heat"]))},
        extra={"ac_heat": True},
    )
    assert ("set_hvac_mode", CLIMATE, "heat") in calls
    heat_target = entry.runtime_data._heat_on_below_c
    assert _setpoints(calls) == [round(heat_target + 2.0)]
    assert entry.runtime_data.data["ac_sensor_offset_k"] == 2.0


# ---- the entity mode is the user's intent, the action is what the compressor does -----------------------------


async def test_entity_mode_is_the_chosen_mode_even_when_the_ac_is_not_running(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, entity_id, calls = await _setup_with_devices(hass, config_data)
    assert ("set_hvac_mode", CLIMATE, "cool") in calls
    freezer.tick(timedelta(minutes=1))
    set_room(hass, "22.0")
    await refresh(hass, entry)  # the AC stopped: decision off
    assert hass.states.get(CLIMATE).state == "off"
    assert hass.states.get(entity_id).state == "auto"
    freezer.tick(timedelta(minutes=1))
    set_room(hass, "26.0")
    await refresh(hass, entry)  # decision cool, the restart delay holds the start, the device is still off
    assert entry.runtime_data.data["pending"] is not None
    assert hass.states.get(entity_id).state == "auto"
    assert hass.states.get(entity_id).attributes["hvac_action"] == "idle"


async def test_entity_mode_is_off_with_the_hold_and_with_control_off(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, entity_id, calls = await _setup_with_devices(hass, config_data)
    assert hass.states.get(entity_id).state == "auto"
    await _room_off(hass, entity_id)
    assert hass.states.get(entity_id).state == "off"
    assert hass.states.get(entity_id).attributes["hvac_action"] == "off"
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": "cool"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "cool"
    # With control off the mode reads off.
    off_entry, off_entity, _ = await _setup_room_climate(hass, {**config_data}, ac_state="cool")
    assert hass.states.get(off_entity).state == "off"


async def test_entity_mode_heat_is_the_last_mode_the_user_chose_and_is_stored(hass, enable_custom_integrations, config_data, freezer, hass_storage):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, entity_id, calls = await _setup_with_devices(hass, config_data, extra={CONF_HEATER_SWITCH: HEATER}, states={HEATER: ("off", {})})
    assert hass.states.get(entity_id).state == "auto"  # the default
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": "heat"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "heat"
    assert (await _stored_payload(hass, entry, hass_storage))["user_mode"] == "heat"
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "heat"
    # Off keeps the stored mode, and turning it on again returns to it.
    await _room_off(hass, entity_id)
    assert hass.states.get(entity_id).state == "off"
    assert entry.runtime_data.user_mode == "heat"


@pytest.mark.parametrize(
    "ac_state,power,action",
    [("cool", "1100", "cooling"), ("cool", "50", "idle"), ("off", "0", "idle"), ("heat", "1100", "heating"), ("heat", "50", "idle")],
)
async def test_action_is_cooling_only_when_the_compressor_runs(hass, enable_custom_integrations, config_data, freezer, ac_state, power, action):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, entity_id, calls = await _setup_with_devices(hass, config_data, extra={"min_off_min": 0})
    hass.states.async_set(CLIMATE, ac_state, _ac_attrs())
    hass.states.async_set(POWER, power, {"unit_of_measurement": "W", "device_class": "power"})
    coordinator = entry.runtime_data
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["hvac_action"] == action


async def test_action_of_a_fleet_climate_follows_its_own_power_sensor(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    fleet = [{"device_id": "small", "kind": "cool", "entity_id": SMALL_AC, "rate_k_per_h_per_kw": 2.0, "rated_w": 900.0, "power_sensor": "sensor.small_power"}]
    entry, entity_id, calls = await _setup_with_devices(
        hass, config_data, states={SMALL_AC: ("off", {"temperature": 30.0}), "sensor.small_power": ("1000", {})}, extra={"fleet": fleet}
    )
    hass.states.async_set(SMALL_AC, "cool", {"temperature": 24.0})
    entry.runtime_data.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["hvac_action"] == "cooling"
    hass.states.async_set("sensor.small_power", "40")
    entry.runtime_data.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["hvac_action"] == "idle"


def test_climate_entity_uses_the_temperature_names_its_base_class_supports(monkeypatch):
    import importlib

    from homeassistant.components.climate import ClimateEntity

    from custom_components.hvac_horizon import climate as climate_module

    old_names = {"current_temperature", "target_temperature", "_attr_temperature_unit"}
    native_names = {"native_current_temperature", "native_target_temperature", "_attr_native_temperature_unit"}
    try:
        # The installed Home Assistant: use whichever API its ClimateEntity has.
        has_native = hasattr(ClimateEntity, "native_temperature_unit")
        defined = set(vars(climate_module.HorizonClimate))
        assert (native_names <= defined and not old_names & defined) if has_native else (old_names <= defined and not native_names & defined)
        # A base class with the native API, as in 2026.11: the entity defines only the native names.
        monkeypatch.setattr(ClimateEntity, "native_temperature_unit", "°C", raising=False)
        importlib.reload(climate_module)
        defined = set(vars(climate_module.HorizonClimate))
        assert native_names <= defined and not old_names & defined
    finally:
        monkeypatch.undo()
        importlib.reload(climate_module)


async def test_home_assistant_logs_no_deprecation_for_this_integration(hass, enable_custom_integrations, config_data, caplog):
    """Home Assistant reports deprecated API use by a custom integration in its log. Setup and two ticks must log none."""
    import logging

    caplog.set_level(logging.WARNING)
    entry, _ = await setup_heater(hass, config_data)
    await refresh(hass, entry)
    await refresh(hass, entry)
    offending = [
        record.getMessage()
        for record in caplog.records
        if "deprecat" in record.getMessage().lower() and ("hvac_horizon" in record.getMessage() or "Horizon" in record.getMessage())
    ]
    assert offending == []


# Weather entity: hourly outdoor series, heatwave warning, cloud factor, outdoor fallback, daily lows and the learned bias.

WEATHER = "weather.test"
NOON_UTC = "2026-11-21T20:00:00+00:00"  # noon in the test time zone (US/Pacific, UTC-8)


class FakeWeather:
    """A weather.get_forecasts service that answers by type, counts calls per type and can be made to fail."""

    def __init__(self, hass, hourly=None, daily=None):
        self.hass = hass
        self.calls = {"hourly": 0, "daily": 0}
        self.fail = False
        self.hourly = hourly or (lambda start: [{"datetime": (start + timedelta(hours=i)).isoformat(), "temperature": 20.0, "condition": "sunny"} for i in range(72)])
        self.daily = daily or [{"datetime": "2026-11-21T00:00:00", "temperature": 25.0, "templow": None}]
        hass.services.async_register("weather", "get_forecasts", self._handle, supports_response=SupportsResponse.ONLY)

    async def _handle(self, call):
        kind = call.data["type"]
        self.calls[kind] += 1
        if self.fail:
            raise HomeAssistantError("weather offline")
        if kind == "hourly":
            start = dt_util.now().replace(minute=0, second=0, microsecond=0)
            items = self.hourly(start)
        else:
            items = self.daily
        return {call.data["entity_id"]: {"forecast": items}}


def local_naive(hours_from_now):
    """A forecast datetime without an offset: local time, as the BOM integration reports it."""
    start = dt_util.now().replace(minute=0, second=0, microsecond=0)
    return (start + timedelta(hours=hours_from_now)).replace(tzinfo=None).isoformat()


async def setup_weather(hass, config_data, freezer, *, extra=None, outdoor="30.0", **fake):
    """Set up an entry with a weather entity at noon, with the fake service registered. Returns (entry, fake, calls)."""
    freezer.move_to(NOON_UTC)
    weather = FakeWeather(hass, **fake)
    entry, calls = await setup_heater(hass, config_data, outdoor=outdoor, room="26.0", extra=dict(extra or {}, weather=WEATHER))
    return entry, weather, calls


def keep_sensors_fresh(hass, *, outdoor=None, room="26.0"):
    """Report the room sensors, and the outdoor sensor when a value is given, so only the others can go stale."""
    for room_id in (ROOM_A, ROOM_B):
        hass.states.async_set(room_id, room, {"unit_of_measurement": "°C", "device_class": "temperature"})
    if outdoor is not None:
        hass.states.async_set(OUTDOOR, outdoor, {"unit_of_measurement": "°C", "device_class": "temperature"})


async def test_hourly_forecast_gives_an_outdoor_series_anchored_to_the_sensor(hass, enable_custom_integrations, config_data, freezer):
    temps = [30.0, 28.0, 26.0, 24.0, 22.0, 20.0, 18.0, 16.0]
    hourly = lambda start: [{"datetime": local_naive(i), "temperature": temps[min(i, 7)], "condition": "sunny"} for i in range(72)]
    entry, weather, _ = await setup_weather(hass, config_data, freezer, hourly=hourly, outdoor="31.0")
    await refresh(hass, entry)
    data = entry.runtime_data.data
    assert data["outdoor_forecast_used"] is True
    # The sensor reads 1 K above the forecast, so the anchor adds 1 K that fades over 3 hours. The first pair also teaches a bias of 0.5 K.
    one, three, six = data["outdoor_forecast_next_c"]
    assert one == pytest.approx(28.0 + 0.5 + (1.0 - 0.5) * (1 - 60 / 180), abs=0.1)
    assert three == pytest.approx(24.0 + 0.5, abs=0.1)
    assert six == pytest.approx(18.0 + 0.5, abs=0.1)
    assert data["outdoor_source"] == "sensor"


async def test_without_a_weather_entity_the_series_is_not_used(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to(NOON_UTC)
    entry, _ = await setup_heater(hass, config_data)
    await refresh(hass, entry)
    data = entry.runtime_data.data
    assert data["outdoor_forecast_used"] is False
    assert data["outdoor_forecast_next_c"] is None
    assert data["outdoor_source"] == "sensor"


async def test_hourly_forecast_is_fetched_at_most_every_30_minutes(hass, enable_custom_integrations, config_data, freezer):
    entry, weather, _ = await setup_weather(hass, config_data, freezer)
    await refresh(hass, entry)
    assert weather.calls == {"hourly": 1, "daily": 1}
    for _ in range(5):
        freezer.tick(timedelta(minutes=5))
        keep_sensors_fresh(hass, outdoor="30.0")
        await refresh(hass, entry)
    assert weather.calls == {"hourly": 1, "daily": 1}
    freezer.tick(timedelta(minutes=6))
    keep_sensors_fresh(hass, outdoor="30.0")
    await refresh(hass, entry)
    assert weather.calls == {"hourly": 2, "daily": 2}


async def test_failed_weather_call_keeps_the_last_forecast_and_logs_once(hass, enable_custom_integrations, config_data, freezer, caplog):
    entry, weather, _ = await setup_weather(hass, config_data, freezer)
    await refresh(hass, entry)
    weather.fail = True
    for _ in range(3):
        freezer.tick(timedelta(minutes=31))
        keep_sensors_fresh(hass, outdoor="30.0")
        await refresh(hass, entry)
    assert weather.calls["hourly"] == 4
    assert entry.runtime_data.data["outdoor_forecast_used"] is True
    assert len([r for r in caplog.records if "hourly forecast unavailable" in r.getMessage()]) == 1
    assert len([r for r in caplog.records if "daily forecast unavailable" in r.getMessage()]) == 1
    weather.fail = False
    freezer.tick(timedelta(minutes=31))
    keep_sensors_fresh(hass, outdoor="30.0")
    await refresh(hass, entry)
    weather.fail = True
    freezer.tick(timedelta(minutes=31))
    keep_sensors_fresh(hass, outdoor="30.0")
    await refresh(hass, entry)
    assert len([r for r in caplog.records if "hourly forecast unavailable" in r.getMessage()]) == 2


async def test_failed_hourly_call_keeps_the_daily_highs(hass, enable_custom_integrations, config_data, freezer):
    daily = [{"datetime": local_naive(0), "temperature": 31.0}, {"datetime": local_naive(24), "temperature": 32.0}, {"datetime": local_naive(48), "temperature": 33.0}]
    entry, weather, _ = await setup_weather(hass, config_data, freezer, daily=daily)
    await refresh(hass, entry)
    assert entry.runtime_data.data["heatwave"] is True
    assert entry.runtime_data._forecast_highs == [31.0, 32.0, 33.0]


async def test_heatwave_warning_sensor_sets_heatwave(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    hass.states.async_set("binary_sensor.heat_warning", "off", {"device_class": "safety"})
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data, heatwave_warning="binary_sensor.heat_warning"), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await refresh(hass, entry)
    assert entry.runtime_data.data["heatwave"] is False
    hass.states.async_set("binary_sensor.heat_warning", "on", {"device_class": "safety"})
    await refresh(hass, entry)
    assert entry.runtime_data.data["heatwave"] is True


async def test_heatwave_warning_overrides_a_cool_streak(hass, enable_custom_integrations, config_data, freezer):
    hass.states.async_set("binary_sensor.heat_warning", "on", {"device_class": "safety"})
    daily = [{"datetime": local_naive(24 * i), "temperature": 22.0} for i in range(5)]
    entry, _, _ = await setup_weather(hass, config_data, freezer, daily=daily, extra={"heatwave_warning": "binary_sensor.heat_warning"})
    await refresh(hass, entry)
    assert entry.runtime_data.data["heatwave"] is True


async def test_cloudy_condition_scales_the_clear_sky_solar_estimate(hass, enable_custom_integrations, config_data, freezer):
    hourly = lambda start: [{"datetime": (start + timedelta(hours=i)).isoformat(), "temperature": 20.0, "condition": "cloudy"} for i in range(72)]
    entry, _, _ = await setup_weather(hass, config_data, freezer, hourly=hourly, extra={"window_area_m2": 4.0})
    await refresh(hass, entry)
    data = entry.runtime_data.data
    modelled = entry.runtime_data._modelled_solar_kw()
    assert modelled > 0
    assert data["solar_factor"] == 0.4
    assert data["solar_gain_kw"] == pytest.approx(modelled * 0.4, abs=0.001)
    assert entry.runtime_data._solar_pairs == []


async def test_irradiance_sensor_takes_over_from_the_cloud_factor(hass, enable_custom_integrations, config_data, freezer):
    hass.states.async_set("sensor.test_irradiance", "500", {"unit_of_measurement": "W/m²"})
    hourly = lambda start: [{"datetime": (start + timedelta(hours=i)).isoformat(), "temperature": 20.0, "condition": "cloudy"} for i in range(72)]
    entry, _, _ = await setup_weather(hass, config_data, freezer, hourly=hourly, extra={"window_area_m2": 4.0, "irradiance": "sensor.test_irradiance"})
    await refresh(hass, entry)
    data = entry.runtime_data.data
    assert data["solar_factor"] is None
    assert data["solar_gain_kw"] == pytest.approx(entry.runtime_data._solar_gain())
    assert data["solar_gain_kw"] == pytest.approx(500 * 4.0 * 0.001 * 1.0, rel=0.5)  # from the sensor, not the model
    assert entry.runtime_data._solar_pairs != []


async def test_no_solar_estimate_without_a_forecast_condition(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to(NOON_UTC)
    entry, _ = await setup_heater(hass, config_data, extra={"window_area_m2": 4.0})
    await refresh(hass, entry)
    assert entry.runtime_data.data["solar_gain_kw"] is None
    assert entry.runtime_data.data["solar_factor"] is None


async def test_stalled_outdoor_sensor_uses_the_fresh_forecast(hass, enable_custom_integrations, config_data, freezer):
    from homeassistant.helpers import issue_registry as ir

    hourly = lambda start: [{"datetime": local_naive(i), "temperature": 30.0 - 2.0 * i, "condition": "sunny"} for i in range(72)]
    entry, weather, _ = await setup_weather(hass, config_data, freezer, hourly=hourly)
    coordinator = entry.runtime_data
    await refresh(hass, entry)
    assert coordinator.data["outdoor_source"] == "sensor"
    freezer.tick(timedelta(minutes=20))  # the outdoor sensor stays silent past the 15 minute stall window
    keep_sensors_fresh(hass)
    await refresh(hass, entry)
    assert weather.calls["hourly"] == 1
    assert OUTDOOR in coordinator.data["sensors_stalled"]
    assert coordinator.data["outdoor_source"] == "forecast"
    assert coordinator._outdoor_now() == pytest.approx(30.0 - 2.0 * (20 / 60), abs=0.1)
    assert coordinator._stall_blocks(coordinator.data) is False
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"{entry.entry_id}_stalled") is not None


async def test_unavailable_outdoor_sensor_uses_the_fresh_forecast(hass, enable_custom_integrations, config_data, freezer):
    entry, _, _ = await setup_weather(hass, config_data, freezer)
    await refresh(hass, entry)
    hass.states.async_set(OUTDOOR, STATE_UNAVAILABLE)
    await refresh(hass, entry)
    assert entry.runtime_data.data["outdoor_source"] == "forecast"
    assert entry.runtime_data._outdoor_now() == pytest.approx(20.0, abs=0.6)


async def test_stalled_outdoor_sensor_stops_control_when_the_forecast_is_old(hass, enable_custom_integrations, config_data, freezer):
    entry, weather, _ = await setup_weather(hass, config_data, freezer)
    coordinator = entry.runtime_data
    await refresh(hass, entry)
    weather.fail = True
    for _ in range(5):  # 2 hours 5 minutes without a good fetch
        freezer.tick(timedelta(minutes=25))
        keep_sensors_fresh(hass)
        await refresh(hass, entry)
    assert OUTDOOR in coordinator.data["sensors_stalled"]
    assert coordinator.data["outdoor_source"] == "sensor"
    assert coordinator._stall_blocks(coordinator.data) is True


async def test_stalled_outdoor_sensor_stops_control_without_a_weather_entity(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to(NOON_UTC)
    entry, _ = await setup_heater(hass, config_data)
    await refresh(hass, entry)
    freezer.tick(timedelta(minutes=20))
    keep_sensors_fresh(hass)
    await refresh(hass, entry)
    assert entry.runtime_data._stall_blocks(entry.runtime_data.data) is True


async def test_daily_low_drives_the_night_plan(hass, enable_custom_integrations, config_data, freezer):
    daily = [{"datetime": local_naive(0), "temperature": 33.0, "templow": None}, {"datetime": local_naive(24), "temperature": 30.0, "templow": 15.0}]
    entry, _, _ = await setup_weather(hass, config_data, freezer, daily=daily, outdoor="30.0")
    await refresh(hass, entry)
    # The room is above comfort and the coming night's low is 15 C, so the plan pre-cools. The current 30 C reading alone gives none.
    assert entry.runtime_data.data["night_plan"] == "pre-cool"


async def test_night_plan_uses_the_reading_without_a_daily_low(hass, enable_custom_integrations, config_data, freezer):
    daily = [{"datetime": local_naive(0), "temperature": 33.0, "templow": None}]
    entry, _, _ = await setup_weather(hass, config_data, freezer, daily=daily, outdoor="30.0")
    await refresh(hass, entry)
    assert entry.runtime_data.data["night_plan"] == "none"


async def feed(hass, entry, freezer, count, outdoor, minutes=31):
    for _ in range(count):
        freezer.tick(timedelta(minutes=minutes))
        keep_sensors_fresh(hass, outdoor=outdoor)
        await refresh(hass, entry)


async def test_sensor_two_kelvin_above_the_forecast_is_learned_and_used(hass, enable_custom_integrations, config_data, freezer):
    entry, _, _ = await setup_weather(hass, config_data, freezer, outdoor="22.0")  # the forecast is 20 C at every hour
    coordinator = entry.runtime_data
    await refresh(hass, entry)
    await feed(hass, entry, freezer, 45, "22.0")
    data = coordinator.data
    assert data["outdoor_bias_pairs"] >= 40
    assert data["outdoor_bias_now_k"] == pytest.approx(2.0, abs=0.1)
    # Every hour ahead is the flat 20 C forecast plus the bias, and the anchor starts at the sensor's 22 C.
    assert data["outdoor_forecast_next_c"] == pytest.approx([22.0, 22.0, 22.0], abs=0.15)
    # With the sensor silent, the fallback is the forecast plus the bias.
    freezer.tick(timedelta(minutes=20))
    keep_sensors_fresh(hass)
    await refresh(hass, entry)
    assert coordinator.data["outdoor_source"] == "forecast"
    assert coordinator._outdoor_now() == pytest.approx(22.0, abs=0.15)


async def test_nothing_is_learned_from_a_stale_sensor(hass, enable_custom_integrations, config_data, freezer):
    entry, _, _ = await setup_weather(hass, config_data, freezer, outdoor="25.0")
    coordinator = entry.runtime_data
    await refresh(hass, entry)
    pairs = coordinator.data["outdoor_bias_pairs"]
    assert pairs == 1
    for _ in range(4):  # the outdoor sensor never reports again; the weather refreshes every 31 minutes
        freezer.tick(timedelta(minutes=31))
        keep_sensors_fresh(hass)
        await refresh(hass, entry)
    assert coordinator.data["outdoor_source"] == "forecast"
    assert coordinator.data["outdoor_bias_pairs"] == pairs


async def test_nothing_is_learned_from_an_unavailable_sensor(hass, enable_custom_integrations, config_data, freezer):
    entry, _, _ = await setup_weather(hass, config_data, freezer, outdoor="25.0")
    await refresh(hass, entry)
    hass.states.async_set(OUTDOOR, STATE_UNAVAILABLE)
    freezer.tick(timedelta(minutes=31))
    keep_sensors_fresh(hass)
    await refresh(hass, entry)
    assert entry.runtime_data.data["outdoor_bias_pairs"] == 1


async def test_learned_bias_survives_a_reload_and_reset_learning_clears_it(hass, enable_custom_integrations, config_data, freezer):
    entry, _, _ = await setup_weather(hass, config_data, freezer, outdoor="22.0")
    await refresh(hass, entry)
    await feed(hass, entry, freezer, 12, "22.0")
    before = entry.runtime_data._outdoor_bias
    assert before.total == 13
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    # The reload's first weather refresh adds one pair to the 13 restored from the store.
    assert entry.runtime_data._outdoor_bias.total == before.total + 1
    assert entry.runtime_data._outdoor_bias.overall == pytest.approx(before.overall, abs=0.1)
    await hass.services.async_call(DOMAIN, "reset_learning", {"entry_id": entry.entry_id}, blocking=True)
    assert entry.runtime_data._outdoor_bias.total == 0
    await refresh(hass, entry)
    assert entry.runtime_data.data["outdoor_bias_pairs"] <= 1


async def test_no_learning_sample_is_recorded_while_the_outdoor_sensor_is_stalled(hass, enable_custom_integrations, config_data, freezer):
    """A stalled outdoor sensor keeps its old value in the state machine. That value must not enter the samples."""
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data)
    await refresh(hass, entry)
    before = entry.runtime_data.data["samples"]
    # Rooms keep reporting. The outdoor sensor goes silent for longer than the stall window.
    for _ in range(4):
        freezer.tick(timedelta(minutes=5))
        set_room(hass, "20.0")
        await refresh(hass, entry)
    assert OUTDOOR in entry.runtime_data.data["sensors_stalled"]
    stalled_count = entry.runtime_data.data["samples"]
    freezer.tick(timedelta(minutes=5))
    set_room(hass, "20.0")
    await refresh(hass, entry)
    assert entry.runtime_data.data["samples"] == stalled_count
    assert stalled_count <= before + 3


async def test_room_entity_action_updates_between_ticks(hass, enable_custom_integrations, config_data):
    """hvac_action follows the AC's power and state when they change, without waiting for the next coordinator tick."""
    entry, _ = await setup_heater(hass, config_data, room="25.0", climate="cool")
    entity_id = _room_climate_id(hass, entry)
    attrs = {"unit_of_measurement": "W", "device_class": "power"}
    # Fan only: the AC is in cool mode but the compressor is not running.
    hass.states.async_set(POWER, "80", attrs)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["hvac_action"] == "idle"
    # The compressor starts. No coordinator refresh happens here.
    hass.states.async_set(POWER, "950", attrs)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["hvac_action"] == "cooling"
    # The AC goes off.
    hass.states.async_set(CLIMATE, "off", dict(hass.states.get(CLIMATE).attributes))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["hvac_action"] == "idle"


async def _set_room_mode(hass, entity_id, mode):
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": mode}, blocking=True)
    await hass.async_block_till_done()


# The thermostat contract. For each mode and room condition: may the AC cool, and may the heater run.
# Hot room: 27 C with 30 C outdoors, above the cool threshold. Cold room: 19 C with 10 C outdoors, below the heat threshold.
MODE_CONTRACT = [
    ("cool", "hot", True, False),
    ("cool", "cold", False, False),
    ("heat", "hot", False, False),
    ("heat", "cold", False, True),
    ("auto", "hot", True, False),
    ("auto", "cold", False, True),
    ("off", "hot", False, False),
    ("off", "cold", False, False),
]


@pytest.mark.parametrize(("mode", "room", "ac_cools", "heater_runs"), MODE_CONTRACT)
async def test_room_mode_decides_which_devices_may_run(hass, enable_custom_integrations, config_data, freezer, mode, room, ac_cools, heater_runs):
    """Cool only cools, heat only heats, auto does both, off does neither. The owner's AC cooled in heat mode before this."""
    freezer.move_to("2026-11-20T10:00:00+00:00")
    room_c, outdoor_c = ("27.0", "30.0") if room == "hot" else ("19.0", "10.0")
    # The room starts in a neutral band so setup itself starts nothing.
    entry, entity_id, calls = await _setup_with_devices(
        hass, config_data, room="23.8", outdoor="20.0", states={HEATER: ("off", {})}, extra={CONF_HEATER_SWITCH: HEATER, "min_off_min": 0},
    )
    await _set_room_mode(hass, entity_id, mode)
    assert hass.states.get(entity_id).state == mode
    calls.clear()
    set_room(hass, room_c)
    hass.states.async_set(OUTDOOR, outdoor_c, {"unit_of_measurement": "°C", "device_class": "temperature"})
    for _ in range(3):
        freezer.tick(timedelta(minutes=5))
        set_room(hass, room_c)
        hass.states.async_set(OUTDOOR, outdoor_c, {"unit_of_measurement": "°C", "device_class": "temperature"})
        await refresh(hass, entry)
    cooled = ("set_hvac_mode", CLIMATE, "cool") in calls
    ac_heated = ("set_hvac_mode", CLIMATE, "heat") in calls
    heater_on = any(call[0] == "turn_on" and call[1] == HEATER for call in calls)
    assert cooled is ac_cools, calls
    assert heater_on is heater_runs, calls
    assert not ac_heated, calls


async def test_changing_to_heat_stops_an_ac_that_is_cooling(hass, enable_custom_integrations, config_data, freezer):
    """The owner's case: the AC was cooling a 25.6 C room, the mode went to heat, and the AC must stop at once."""
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, entity_id, calls = await _setup_with_devices(
        hass, config_data, room="25.6", outdoor="24.0", states={HEATER: ("off", {})}, extra={CONF_HEATER_SWITCH: HEATER},
    )
    assert ("set_hvac_mode", CLIMATE, "cool") in calls
    calls.clear()
    await _set_room_mode(hass, entity_id, "heat")
    assert ("set_hvac_mode", CLIMATE, "off") in calls
    assert ("set_hvac_mode", CLIMATE, "cool") not in calls
    assert not any(call[0] == "turn_on" and call[1] == HEATER for call in calls)


async def test_room_mode_survives_a_restart(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, entity_id, calls = await _setup_with_devices(hass, config_data, room="23.8", outdoor="20.0", states={HEATER: ("off", {})}, extra={CONF_HEATER_SWITCH: HEATER})
    await _set_room_mode(hass, entity_id, "heat")
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "heat"


async def test_heat_mode_stops_the_second_ac(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    second = "climate.test_ac_2"
    entry, entity_id, calls = await _setup_with_devices(
        hass, config_data, states={second: ("cool", {"temperature": 24.0}), HEATER: ("off", {})},  # the second AC already cools
        extra={"climate_2": second, CONF_HEATER_SWITCH: HEATER, "min_off_min": 0},
    )
    assert ("set_hvac_mode", CLIMATE, "cool") in calls
    assert hass.states.get(second).state == "cool"
    calls.clear()
    await _set_room_mode(hass, entity_id, "heat")
    for _ in range(2):
        freezer.tick(timedelta(minutes=5))
        set_room(hass, "26.0")
        await refresh(hass, entry)
    for ac in (CLIMATE, second):
        assert hass.states.get(ac).state == "off", (ac, calls)
        assert ("set_hvac_mode", ac, "cool") not in calls


async def test_heat_mode_stops_a_cooling_fleet_device(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    fleet = [
        {"device_id": "small", "kind": "cool", "entity_id": SMALL_AC, "rate_k_per_h_per_kw": 2.0, "rated_w": 900.0},
        {"device_id": "ir", "kind": "heat", "entity_id": IR_HEATER, "rate_k_per_h_per_kw": 1.5, "rated_w": 1500.0},
    ]
    entry, entity_id, calls = await _setup_with_devices(
        hass, config_data, states={SMALL_AC: ("off", {"temperature": 30.0}), IR_HEATER: ("off", {})},
        extra={"fleet": fleet, "dispatch_mode": "fast", "min_off_min": 0},
    )
    assert ("set_hvac_mode", SMALL_AC, "cool") in calls
    assert "heat" in hass.states.get(entity_id).attributes["hvac_modes"]  # a heating fleet device gives the room a heat mode
    calls.clear()
    await _set_room_mode(hass, entity_id, "heat")
    for _ in range(2):
        freezer.tick(timedelta(minutes=5))
        set_room(hass, "26.0")
        await refresh(hass, entry)
    assert hass.states.get(SMALL_AC).state == "off", calls
    assert ("set_hvac_mode", SMALL_AC, "cool") not in calls
    assert ("turn_on", IR_HEATER, None) not in calls


async def test_cool_mode_keeps_a_heating_fleet_device_off_in_a_cold_room(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    fleet = [{"device_id": "ir", "kind": "heat", "entity_id": IR_HEATER, "rate_k_per_h_per_kw": 1.5, "rated_w": 1500.0}]
    entry, entity_id, calls = await _setup_with_devices(
        hass, config_data, room="23.8", outdoor="20.0", states={IR_HEATER: ("off", {})}, extra={"fleet": fleet, "dispatch_mode": "fast"},
    )
    await _set_room_mode(hass, entity_id, "cool")
    calls.clear()
    for _ in range(2):
        freezer.tick(timedelta(minutes=5))
        set_room(hass, "18.0")
        hass.states.async_set(OUTDOOR, "8.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
        await refresh(hass, entry)
    assert ("turn_on", IR_HEATER, None) not in calls
    # Auto lets it heat.
    await _set_room_mode(hass, entity_id, "auto")
    freezer.tick(timedelta(minutes=5))
    set_room(hass, "18.0")
    hass.states.async_set(OUTDOOR, "8.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, entry)
    assert ("turn_on", IR_HEATER, None) in calls


async def test_turn_on_returns_to_the_mode_chosen_before_off(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, entity_id, calls = await _setup_with_devices(hass, config_data, room="23.8", outdoor="20.0", states={HEATER: ("off", {})}, extra={CONF_HEATER_SWITCH: HEATER})
    await _set_room_mode(hass, entity_id, "cool")
    await hass.services.async_call("climate", "turn_off", {"entity_id": entity_id}, blocking=True)
    assert hass.states.get(entity_id).state == "off"
    await hass.services.async_call("climate", "turn_on", {"entity_id": entity_id}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "cool"


async def test_ac_heat_obeys_the_room_mode(hass, enable_custom_integrations, config_data, freezer):
    """An AC that heats: cool mode stops it and sends no heat, heat lets it heat a cold room again."""
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, modes, temps = await _setup_ac_only(hass, config_data, room="20.0", outdoor="10.0")
    coordinator = entry.runtime_data
    assert modes == ["heat"]  # auto, the default

    async def tick():
        freezer.tick(timedelta(minutes=10))
        for sensor, value in ((ROOM_A, "20.0"), (ROOM_B, "20.0"), (OUTDOOR, "10.0")):
            hass.states.async_set(sensor, value, {"unit_of_measurement": "°C", "device_class": "temperature"}, force_update=True)
        await refresh(hass, entry)

    coordinator.set_user_off(False, "cool")
    await tick()
    assert modes == ["heat", "off"]
    await tick()
    assert modes == ["heat", "off"]
    assert entry.runtime_data.data["action"] != "off: sensor stalled"
    coordinator.set_user_off(False, "heat")
    await tick()
    assert modes == ["heat", "off", "heat"]


async def test_a_mode_stored_before_version_4_loads_as_auto(hass, enable_custom_integrations, config_data, hass_storage, freezer):
    """0.5.0 stored "cool" by default and ignored it. After the update such a room must still heat."""
    freezer.move_to("2026-11-20T10:00:00+00:00")
    hass_storage[f"hvac_horizon.{OFF_ENTRY_ID}"] = {
        "version": 3, "minor_version": 1, "key": f"hvac_horizon.{OFF_ENTRY_ID}", "data": {"entries": [], "user_mode": "cool"},
    }
    set_inputs(hass)
    calls = _device_services(hass)
    hass.states.async_set(HEATER, "off")
    hass.states.async_set(CLIMATE, "off", {"temperature": 24.0, "target_temp_step": 1.0})
    set_room(hass, "17.0")
    hass.states.async_set(OUTDOOR, "8.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", entry_id=OFF_ENTRY_ID, data={**config_data, "control": True, CONF_HEATER_SWITCH: HEATER}, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.user_mode == "auto"
    for _ in range(2):
        freezer.tick(timedelta(minutes=5))
        set_room(hass, "17.0")
        hass.states.async_set(OUTDOOR, "8.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
        await refresh(hass, entry)
    assert ("turn_on", HEATER, None) in calls


async def test_a_stored_heat_mode_reads_auto_once_the_entry_cannot_heat(hass, enable_custom_integrations, config_data, hass_storage, freezer):
    """The heater was removed through reconfigure. The entity and control agree on auto, and the room cools."""
    freezer.move_to("2026-11-20T10:00:00+00:00")
    hass_storage[f"hvac_horizon.{OFF_ENTRY_ID}"] = {
        "version": 4, "minor_version": 1, "key": f"hvac_horizon.{OFF_ENTRY_ID}", "data": {"entries": [], "user_mode": "heat"},
    }
    set_inputs(hass)
    calls = _device_services(hass)
    hass.states.async_set(CLIMATE, "off", {"temperature": 24.0, "target_temp_step": 1.0})
    set_room(hass, "27.0")
    hass.states.async_set(OUTDOOR, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", entry_id=OFF_ENTRY_ID, data={**config_data, "control": True}, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id("climate", DOMAIN, ROOM_CLIMATE_UNIQUE.format(entry.entry_id))
    assert hass.states.get(entity_id).state == "auto"
    assert "heat" not in hass.states.get(entity_id).attributes["hvac_modes"]
    assert ("set_hvac_mode", CLIMATE, "cool") in calls


async def test_heat_mode_keeps_the_window_actuator_shut(hass, enable_custom_integrations, config_data, freezer):
    """Room 26 C with 18 C outdoors opens the window in auto. In heat mode free cooling stays off."""
    freezer.move_to("2026-11-20T10:00:00+00:00")
    calls = _window_actuator_fakes(hass)
    entry, _ = await setup_heater(hass, config_data, outdoor="18.0", room="26.0", extra={"window_actuator": "cover.test_window"})
    assert calls == [("open", "cover.test_window")]
    entry.runtime_data.set_user_off(False, "heat")
    await refresh(hass, entry)
    assert calls == [("open", "cover.test_window"), ("close", "cover.test_window")]
    await refresh(hass, entry)
    assert calls[-1] == ("close", "cover.test_window") and len(calls) == 2
