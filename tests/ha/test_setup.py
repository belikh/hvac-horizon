"""Home Assistant test: config flow, setup, one refresh and unload.

Run with pytest-homeassistant-custom-component. This directory has no __init__.py,
so the standard-library unittest run does not collect it.
"""

from datetime import datetime, timedelta, timezone

import pytest
from homeassistant import config_entries
from homeassistant.components import sensor
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.hvac_horizon.const import (
    CONF_CLIMATE,
    CONF_OUTDOOR,
    CONF_POWER,
    CONF_PRICE,
    CONF_ROOM_SENSORS,
    DOMAIN,
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


async def test_config_flow_creates_entry(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] == "form"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], config_data)
    assert result["type"] == "create_entry"
    assert result["data"][CONF_CLIMATE] == CLIMATE


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

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] == "form"
    new_data = dict(config_data)
    new_data[CONF_PRICE] = PRICE
    result = await hass.config_entries.flow.async_configure(result["flow_id"], new_data)
    await hass.async_block_till_done()
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
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
    hass.states.async_set(CLIMATE, "off", {"temperature": 22.0, "friendly_name": "Test AC"})
    data = dict(config_data, control=True)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert ("mode", {"entity_id": CLIMATE, "hvac_mode": "cool"}) in calls
    # Price 0.28 $/kWh sits inside the 0.15 to 0.40 band, so the target shifts from 24.0 to 24.04 C.
    assert ("temp", {"entity_id": CLIMATE, "temperature": 24.04}) in calls


async def test_flow_accepts_control_options(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    data = dict(config_data, comfort_c=24.0, away_c=27.0, window_delay_min=10, control=False)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], data)
    assert result["type"] == "create_entry"
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
