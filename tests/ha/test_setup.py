"""Home Assistant test: config flow, setup, one refresh and unload.

Run with pytest-homeassistant-custom-component. This directory has no __init__.py,
so the standard-library unittest run does not collect it.
"""

from datetime import datetime, timedelta, timezone

import pytest
from homeassistant import config_entries
from homeassistant.components import sensor
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import SupportsResponse
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant.util import dt as dt_util

from custom_components.hvac_horizon.features import room_devices
from custom_components.hvac_horizon import coordinator as coordinator_module
from custom_components.hvac_horizon.pid import Pid
from custom_components.hvac_horizon.tpi import TpiFit
from custom_components.hvac_horizon.const import (
    CONF_BOOST_C,
    CONF_AWAY_DELAY_MIN,
    CONF_COMFORT_C,
    CONF_ECO_C,
    CONF_HEATER_INVERTED,
    CONF_HEATER_KP,
    CONF_HEATER_SWITCH,
    CONF_HEAT_BOOST_C,
    CONF_HEAT_ECO_C,
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


async def test_respond_suggestion_dismiss_records_gain(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    if coordinator._fit is None:
        from custom_components.hvac_horizon import Fit

        coordinator._fit = Fit(k=0.3, c=1.5, g=0.0)
    coordinator.respond_suggestion(False)
    assert coordinator._dismissed_for_k == coordinator._fit.k
    coordinator.respond_suggestion(True)
    assert coordinator._dismissed_for_k is None


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


async def test_status_sensors_exist_with_control_off(hass, enable_custom_integrations, config_data):
    set_inputs(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=config_data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
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

    monkeypatch.setattr(HorizonCoordinator, "_stalled_sensors", lambda self, now: [ROOM_A])
    entry, calls = await setup_heater(hass, config_data, heater_state="on")
    await refresh(hass, entry)
    assert "turn_off" in calls
    assert "turn_on" not in calls
    assert entry.runtime_data.data["safety_output_pct"] == 0.0


async def test_heater_switches_off_on_unload(hass, enable_custom_integrations, config_data):
    entry, calls = await setup_heater(hass, config_data)
    await refresh(hass, entry)
    hass.states.async_set(HEATER, "on")
    calls.clear()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert calls == ["turn_off"]


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
    assert coordinator._pwm_output_pct == 40.0


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


async def _reconfigure(hass, entry, submitted):
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] == "form"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], submitted)
    await hass.async_block_till_done()
    assert result["type"] == "abort" and result["reason"] == "reconfigure_successful", result


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
    result = await entry.start_reconfigure_flow(hass)
    bad = {**config_data, CONF_SCHEDULE_BLOCKS: [{"start_h": 0.0, "end_h": 25.0, "preset": "eco"}]}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], bad)
    assert result["type"] == "form"
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
    entry, _ = await setup_heater(hass, config_data)
    coordinator = entry.runtime_data
    coordinator.start_autotune(24.0)
    # One reading per 5 minute cycle, a 1 K swing with a one hour period. The test completes after about five hours.
    # The outdoor sensor is refreshed too: a sensor that stops updating for 15 minutes closes the heater gate.
    # Home Assistant keeps last_updated when neither the state nor an attribute changes, so the tick is an attribute.
    for tick in range(120):
        set_room(hass, f"{24.0 + math.sin(2 * math.pi * tick / 12):.3f}")
        hass.states.async_set(OUTDOOR, "10.0", {"unit_of_measurement": "°C", "device_class": "temperature", "tick": tick})
        freezer.tick(timedelta(minutes=5))
        await refresh(hass, entry)
        if coordinator._autotune_result is not None:
            break
    tuned = coordinator._autotune_result
    assert tuned is not None
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
    result = await entry.start_reconfigure_flow(hass)
    new_data = dict(entry.data)
    new_data[CONF_HEATER_KP] = 12.0
    result = await hass.config_entries.flow.async_configure(result["flow_id"], new_data)
    await hass.async_block_till_done()
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    await refresh(hass, entry)
    pid = entry.runtime_data._heater_pid
    assert pid.kp == pytest.approx(12.0)
    assert (pid.ki, pid.kd) == pytest.approx((30.0, 0.2))


@pytest.mark.parametrize("room", ["26.0", "unavailable"], ids=["hot_room", "rooms_unavailable"])
async def test_stalled_sensor_turns_the_ac_off(hass, enable_custom_integrations, config_data, monkeypatch, freezer, room):
    from custom_components.hvac_horizon.coordinator import HorizonCoordinator

    monkeypatch.setattr(HorizonCoordinator, "_stalled_sensors", lambda self, now: [ROOM_A])
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


async def _setup_ac_only(hass, config_data, room="20.0", outdoor="10.0"):
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
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data, control=True), unique_id=CLIMATE)
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


async def test_heat_then_cool_inside_the_minimum_on_time_is_held(hass, enable_custom_integrations, config_data, freezer):
    # Heat starts at 10:00. Cool is wanted at 10:04. Heat counts as running, so the 5 minute minimum on time holds the switch.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, modes, _ = await _setup_ac_only(hass, config_data, room="20.0", outdoor="10.0")
    assert modes == ["heat"]
    # The outdoor sensor warms to 30 C, so heat is no longer allowed and cooling is.
    freezer.tick(timedelta(minutes=4))
    _set_ac_rooms(hass, "26.0")
    hass.states.async_set(OUTDOOR, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, entry)
    assert "cool" not in modes
    freezer.tick(timedelta(minutes=2))
    _set_ac_rooms(hass, "26.0")
    hass.states.async_set(OUTDOOR, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, entry)
    assert modes[-1] == "cool"


async def test_heat_stop_inside_the_minimum_on_time_is_held(hass, enable_custom_integrations, config_data, freezer):
    # Heat starts at 10:00. The room reaches the dead band at 10:01, where neither heat nor cool is wanted.
    # The 5 minute minimum on time holds the heat switch until 10:05.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, modes, _ = await _setup_ac_only(hass, config_data, room="20.0", outdoor="10.0")
    assert modes == ["heat"]
    freezer.tick(timedelta(minutes=1))
    _set_ac_rooms(hass, "23.8")
    await refresh(hass, entry)
    assert "off" not in modes
    freezer.tick(timedelta(minutes=5))
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


async def test_second_ac_stop_inside_minimum_on_time_is_held(hass, enable_custom_integrations, config_data, freezer):
    # The second AC starts at 10:00. A stop at 10:02 is inside the 5 minute minimum on time, so it waits until 10:06.
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
    assert (second, "off") not in modes
    assert entry.runtime_data.data["second_ac"] == "waiting for minimum time"

    freezer.tick(timedelta(minutes=4))
    _set_ac_rooms(hass, "22.0")
    await refresh(hass, entry)
    assert (second, "off") in modes


async def test_stall_output_turns_the_heater_off_even_without_the_gate(hass, enable_custom_integrations, config_data, monkeypatch):
    # The window, lock and away gates are open here. Only the stall can turn the heater off, through safety_output.
    from custom_components.hvac_horizon.coordinator import HorizonCoordinator

    monkeypatch.setattr(HorizonCoordinator, "_heater_gate_closed", lambda self, result: False)
    monkeypatch.setattr(HorizonCoordinator, "_stalled_sensors", lambda self, now: [ROOM_A])
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


async def _fan_mode_run(hass, config_data, *, state="cool", room="27.0", fan_mode="auto", control=True):
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
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data, control=control), unique_id=CLIMATE)
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


async def _mpc_run(hass, config_data, monkeypatch, freezer, *, room="23.0", plan=None, no_prices=False, outdoor="30.0"):
    """Control on, a fixed MPC plan and a pre-cool start now. Returns the entry and the AC mode commands.

    The price sensor stays set, so the target is 24.04 C and the cool threshold is 24.04 C. The
    deadband is 0.5 K, so a room at 23.0 C is below the idle limit of 23.54 C, and a room at 23.8 C is above it.
    """
    from custom_components.hvac_horizon import coordinator as coordinator_module
    from custom_components.hvac_horizon.coordinator import HorizonCoordinator
    from custom_components.hvac_horizon.model import Fit

    # The price fixture starts at 08:10 UTC. Slots that have ended are dropped, so the clock sits before them.
    freezer.move_to("2026-11-20T08:00:00+00:00")
    monkeypatch.setattr(HorizonCoordinator, "_refit", lambda self: setattr(self, "_fit", Fit(k=0.05, c=0.5, g=0.0)))
    monkeypatch.setattr(HorizonCoordinator, "_precool_start", lambda self, now, rooms: 0.0)
    if plan is not None:
        monkeypatch.setattr(coordinator_module, "mpc_plan", lambda *args, **kwargs: list(plan))
    if no_prices:
        monkeypatch.setattr(HorizonCoordinator, "_price_forecast", lambda self, now: [])
    set_inputs(hass)
    _set_ac_rooms(hass, room)
    hass.states.async_set(OUTDOOR, outdoor, {"unit_of_measurement": "°C", "device_class": "temperature"})
    hass.states.async_set(CLIMATE, "off", {"temperature": 22.0, "friendly_name": "Test AC", "target_temp_step": 1.0})
    modes = []

    async def record_mode(call):
        modes.append(call.data["hvac_mode"])
        hass.states.async_set(CLIMATE, call.data["hvac_mode"], dict(hass.states.get(CLIMATE).attributes))

    async def record_temp(call):
        state = hass.states.get(CLIMATE)
        hass.states.async_set(CLIMATE, state.state, {**state.attributes, "temperature": call.data["temperature"]})

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    hass.services.async_register("climate", "set_temperature", record_temp)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=dict(config_data, control=True), unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
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


async def test_mpc_idle_step_inside_the_deadband_keeps_the_decision(hass, enable_custom_integrations, config_data, monkeypatch, freezer):
    entry, modes = await _mpc_run(hass, config_data, monkeypatch, freezer, room="23.8", plan=[False] * 16)
    assert modes == ["cool"]
    assert entry.runtime_data.data["action"] == "cool: pre-cool starts now"


async def test_no_mpc_plan_falls_back_to_the_current_decision(hass, enable_custom_integrations, config_data, monkeypatch, freezer):
    entry, modes = await _mpc_run(hass, config_data, monkeypatch, freezer, room="23.0", no_prices=True)
    assert "mpc_plan" not in entry.runtime_data.data
    assert modes == ["cool"]


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
    monkeypatch.setattr(coordinator_module, "learn_room_rates", lambda samples: TpiFit(k=0.0, g=0.0, c=1.0, samples_off=12, samples_on=12))
    monkeypatch.setattr(coordinator_module, "learn_heat_rate", lambda pairs, k, g: 5.0)
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

