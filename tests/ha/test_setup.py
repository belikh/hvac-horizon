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
from custom_components.hvac_horizon.const import (
    CONF_BOOST_C,
    CONF_ECO_C,
    CONF_HEATER_INVERTED,
    CONF_HEATER_SWITCH,
    CONF_CLIMATE,
    CONF_LOCK_PIN,
    CONF_SCHEDULE_BLOCKS,
    CONF_OUTDOOR,
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
    assert entry.runtime_data._stalled_sensors(dt_util.utcnow().timestamp()) == []


async def test_sensor_without_a_report_for_15_minutes_is_a_stall(hass, enable_custom_integrations, config_data, freezer):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data)
    freezer.tick(timedelta(minutes=15))
    assert set(entry.runtime_data._stalled_sensors(dt_util.utcnow().timestamp())) == {ROOM_A, ROOM_B, OUTDOOR}


@pytest.mark.parametrize("state", ["missing", "unavailable"])
async def test_missing_or_unavailable_sensor_is_not_a_stall(hass, enable_custom_integrations, config_data, freezer, state):
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data)
    if state == "missing":
        hass.states.async_remove(ROOM_A)
    else:
        hass.states.async_set(ROOM_A, "unavailable", {"unit_of_measurement": "°C", "device_class": "temperature"})
    freezer.tick(timedelta(minutes=30))
    assert ROOM_A not in entry.runtime_data._stalled_sensors(dt_util.utcnow().timestamp())


async def test_no_room_data_leaves_the_ac_alone(hass, enable_custom_integrations, config_data, freezer):
    # With no room reading and no stall, _async_control returns without a climate action. The AC keeps its state.
    freezer.move_to("2026-11-20T10:00:00+00:00")
    entry, _ = await setup_heater(hass, config_data, room="unavailable", outdoor="30.0", climate="cool")
    modes = []

    async def record_mode(call):
        modes.append(call.data["hvac_mode"])

    hass.services.async_register("climate", "set_hvac_mode", record_mode)
    freezer.tick(timedelta(minutes=30))
    # Keep the outdoor sensor fresh, so only the room readings are missing.
    hass.states.async_set(OUTDOOR, "30.0", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await refresh(hass, entry)
    assert modes == []


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
    data = dict(config_data, room_humidity="sensor.test_humidity", control=True)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert {"entity_id": CLIMATE, "hvac_mode": "dry"} in calls


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
    assert len(entry.runtime_data._entries) >= 1
    await hass.services.async_call(DOMAIN, "reset_learning", {"entry_id": entry.entry_id}, blocking=True)
    assert entry.runtime_data._entries == []


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
    assert coordinator._locked is True
    assert coordinator.set_lock(False, "0000") is False
    assert coordinator.set_lock(False, "1234") is True
    assert coordinator._locked is False


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


async def test_timed_preset_overrides_then_reverts(hass, enable_custom_integrations, config_data):
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
    now_min = 1_000_000.0
    coordinator.start_timed_preset("eco", 30.0, now_min)
    assert coordinator._effective_preset(now_min, "comfort") == "eco"
    assert coordinator._effective_preset(now_min + 31, "comfort") == "comfort"


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
    hass.services.async_register("switch", "turn_on", lambda call: None)
    hass.services.async_register("switch", "turn_off", lambda call: None)
    data = {**config_data, "heater_switch": "switch.test_heater"}
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    coordinator.set_heater_output(40.0)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator._pwm_output_pct == 40.0
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
    hass.services.async_register("climate", "set_temperature", lambda call: None)
    # Comfort 24.26 plus the price shift gives about 24.30, which the 1.0 C step device stores as 24.0.
    hass.states.async_set(CLIMATE, "cool", {"temperature": 22.0, "target_temp_step": 1.0, "friendly_name": "Test AC"})
    data = dict(config_data, control=True, comfort_c=24.26)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    # The setup refresh wrote the rounded setpoint. The device echoes it at its 1.0 C step.
    assert coordinator._last_written_setpoint == 24.0
    hass.states.async_set(CLIMATE, "cool", {"temperature": 24.0, "target_temp_step": 1.0, "friendly_name": "Test AC"})
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator._inhibit_until_min is None


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


async def setup_heater(hass, config_data, *, heater_state="off", outdoor="10.0", room="20.0", extra=None, climate="off"):
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
    hass.services.async_register("climate", "set_temperature", ignore)
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
    assert entry.runtime_data._pwm_output_pct == 0.0


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
    assert "turn_on" not in calls
    assert entry.runtime_data._heater_commanded_on is False


async def test_heater_feedback_is_set_after_one_driven_tick(hass, enable_custom_integrations, config_data):
    entry, _ = await setup_heater(hass, config_data)
    await refresh(hass, entry)
    assert entry.runtime_data.data["heater_feedback"] != "not_configured"
    assert entry.runtime_data._heater_commanded_on is True


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
    assert entry.runtime_data._heater_commanded_on is True
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
    entry, _ = await setup_heater(hass, config_data)
    coordinator = entry.runtime_data
    coordinator.set_heater_output(40.0)
    hass.states.async_set(OUTDOOR, "unavailable")
    await refresh(hass, entry)
    assert coordinator._pwm_output_pct == 0.0


async def test_heater_output_is_held_during_inhibit(hass, enable_custom_integrations, config_data):
    entry, _ = await setup_heater(hass, config_data)
    coordinator = entry.runtime_data
    coordinator.set_heater_output(40.0)
    coordinator._inhibit_until_min = dt_util.utcnow().timestamp() / 60.0 + 60.0
    await refresh(hass, entry)
    assert coordinator._pwm_output_pct == 40.0


# The AC cools above these values. Eco and boost use the setpoints set in the entry; comfort is 24 C.
COOL_ABOVE_BY_PRESET = {"comfort": 24.0, "eco": 26.0, "boost": 22.0, "away": 27.0, "vacation": 27.0}


@pytest.mark.parametrize("scenario", ["comfort", "eco", "boost", "away", "vacation"])
async def test_heater_threshold_is_below_ac_threshold_on_every_preset(hass, enable_custom_integrations, config_data, scenario):
    extra = {CONF_ECO_C: 26.0, CONF_BOOST_C: 22.0}
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
    assert cool_on - heat_on == pytest.approx(0.5)
    assert cool_on == pytest.approx(COOL_ABOVE_BY_PRESET[scenario])



async def test_boost_drives_heater_output_to_100(hass, enable_custom_integrations, config_data):
    entry, _ = await setup_heater(hass, config_data)
    await hass.services.async_call(DOMAIN, "start_timed_preset", {"entry_id": entry.entry_id, "preset": "boost", "minutes": 60})
    await refresh(hass, entry)
    assert entry.runtime_data._pwm_output_pct == 100.0


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
    data = {**config_data, CONF_SCHEDULE_BLOCKS: blocks}
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert entry.runtime_data._preset_now == "eco"


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
    assert coordinator._inhibit_until_min is not None

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
    assert coordinator._inhibit_until_min > dt_util.utcnow().timestamp() / 60.0


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
    assert coordinator._inhibit_until_min is not None

    # No sensor reports for 16 minutes. The inhibition still holds, and the stall must still switch the AC off.
    freezer.tick(timedelta(minutes=16))
    await refresh(hass, entry)
    assert coordinator._inhibit_until_min > dt_util.utcnow().timestamp() / 60.0
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
    assert entry.runtime_data._pwm_output_pct > 0.0
    await hass.services.async_call(DOMAIN, "set_house_mode", {"mode": "away"}, blocking=True)
    await refresh(hass, entry)
    assert entry.runtime_data._pwm_output_pct == 0.0


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
    entry, _ = await setup_heater(hass, config_data, room="23.7")
    coordinator = entry.runtime_data
    before = coordinator._heater_pid._integral
    for tick in range(100):
        set_room(hass, "23.7")
        hass.states.async_set(OUTDOOR, "10.0", {"unit_of_measurement": "°C", "device_class": "temperature", "tick": tick})
        freezer.tick(timedelta(minutes=5))
        await refresh(hass, entry)
    # The band overrides the PID request in the drive step, so the switch output (_pwm_pct_now) is 0.
    assert coordinator._pwm_pct_now == 0.0
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
    assert entry.runtime_data._settings.comfort_c == 26.0
    assert entry.runtime_data._settings.eco_c == 26.0


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
    assert entry.runtime_data._settings.comfort_c == 24.0
