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
    data = dict(config_data, humidity="sensor.test_humidity", control=True)
    entry = MockConfigEntry(domain=DOMAIN, title="Test AC", data=data, unique_id=CLIMATE)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert {"entity_id": CLIMATE, "hvac_mode": "dry"} in calls


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
    data = dict(config_data, humidifier="switch.test_humidifier", humidity="sensor.test_humidity", control=True)
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



def test_every_conf_key_is_in_the_flow_schema():
    import re
    from pathlib import Path

    from custom_components.hvac_horizon import config_flow

    text = Path(config_flow.__file__).with_name("const.py").read_text()
    # CONF_SCHEDULE_BLOCKS is edited through the schedule service, not the flow.
    values = {
        m.group(2)
        for m in re.finditer(r'^(CONF_[A-Z0-9_]+)\s*=\s*"([^"]+)"', text, re.M)
        if m.group(1) != "CONF_SCHEDULE_BLOCKS"
    }
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
