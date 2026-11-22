"""Closed-loop harness: the real integration against a simulated house.

The ACs and the heater plug are real Home Assistant entities from tests/fakes.py, each wrapping a simulated device.
A command the integration sends goes through Home Assistant's own climate and switch services, which refuse a mode
or a setpoint the device does not have. Each 5 minute tick the harness writes the sensor states from the house and
refreshes the coordinator. Between ticks it advances the frozen clock one
minute at a time, fires the timers that fall due (the heater PWM edges) and steps the house. The result is a
Trace: one row per minute, every service call, and the outcome of every tick.

The run uses no price, weather, presence or schedule entities, so no MPC plan and no setback runs. The controller
reads the room sensors, the outdoor sensor and one power sensor. A profile can add the room humidity sensor, leave
the power sensor out, choose the thermostat mode and add settings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from unittest.mock import patch

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.hvac_horizon.const import (
    CONF_CLIMATE,
    CONF_HEATER_SWITCH,
    CONF_MIN_OFF_MIN,
    CONF_MIN_ON_MIN,
    CONF_OUTDOOR,
    CONF_POWER,
    CONF_ROOM_SENSORS,
    DOMAIN,
    SAMPLE_INTERVAL_S,
)
from custom_components.hvac_horizon.coordinator import HorizonCoordinator
from tests.fakes import ALL_MODES, FakeAc, FakeSwitch, add_devices
from tests.sim.house import AcSpec, Ac, Envelope, Heater, House, SensorFault, Weather

ROOM_A = "sensor.sim_room_a"
ROOM_B = "sensor.sim_room_b"
OUTDOOR = "sensor.sim_outdoor"
ROOM_RH = "sensor.sim_room_humidity"
POWER = "sensor.sim_power"
HEATER = "switch.sim_heater"
START = "2026-07-15T00:00:00+00:00"
TICK_MIN = SAMPLE_INTERVAL_S // 60
COMFORT_C = 24.0  # the integration default; the cool threshold the comfort check uses


@dataclass(frozen=True)
class Profile:
    name: str
    envelope: Envelope
    weather: Weather
    acs: tuple[tuple[str, AcSpec], ...]  # entity id and spec of each AC, the first one is the entry's climate entity
    heater_kw: float | None = None
    start_hour: float = 0.0
    fleet: tuple[dict[str, Any], ...] = ()  # entered fleet devices; with it the dispatch drives the ACs
    dispatch_mode: str = "economy"
    min_on_min: float = 10.0
    min_off_min: float = 5.0
    start_air_c: float = 26.0
    start_mass_c: float = 27.0
    ac_modes: tuple[str, ...] = ALL_MODES  # the modes each AC has
    power_sensor: bool = True  # False: the entry gets the AC's rated power and reads its hvac_action
    rated_w: float = 1000.0
    room_humidity: bool = False  # True adds the room humidity sensor
    start_rh_pct: float = 55.0
    mode: str | None = None  # the thermostat mode set after setup; None leaves auto
    settings: tuple[tuple[str, Any], ...] = ()  # extra settings of the entry


@dataclass(frozen=True)
class DeviceFault:
    """From start_min to end_min the device `entity_id` fails. kind: "unavailable", "ignores" or "errors" (see tests/fakes.py)."""

    start_min: float
    end_min: float
    entity_id: str
    kind: str


@dataclass(frozen=True)
class Call:
    minute: int
    domain: str
    service: str
    entity_id: str
    data: dict[str, Any]


@dataclass(frozen=True)
class Row:
    minute: int
    hour: float
    air_c: float
    mass_c: float
    outdoor_c: float
    modes: dict[str, str]  # entity id -> mode Home Assistant shows
    compressors: dict[str, bool]
    heater_on: bool
    power_w: float
    rh_pct: float = 55.0


@dataclass
class Trace:
    profile: Profile
    rows: list[Row] = field(default_factory=list)  # one per minute, row 0 is the start
    calls: list[Call] = field(default_factory=list)
    ticks: list[tuple[int, bool, str]] = field(default_factory=list)  # minute, last_update_success, action
    updates: int = 0  # coordinator update calls, one per tick
    cool_above_c: float | None = None
    command_failures: list[str] = field(default_factory=list)  # every command a device or Home Assistant refused
    unconfirmed: list[tuple[int, list[str]]] = field(default_factory=list)  # minute and the commands_unconfirmed list, when not empty
    dispatch: list[dict[str, Any]] = field(default_factory=list)


class Harness:
    def __init__(self, hass, freezer, profile: Profile, faults: list[SensorFault] | None = None, seed: int = 1, device_faults: list[DeviceFault] | None = None) -> None:
        self.hass = hass
        self.freezer = freezer
        self.profile = profile
        self.house = House(
            envelope=profile.envelope,
            weather=profile.weather,
            acs={entity: Ac(spec) for entity, spec in profile.acs},
            heater=Heater(profile.heater_kw) if profile.heater_kw else None,
            seed=seed,
            start_hour=profile.start_hour,
            start_air_c=profile.start_air_c,
            start_mass_c=profile.start_mass_c,
            start_rh_pct=profile.start_rh_pct,
            faults=list(faults or []),
        )
        self.device_faults = list(device_faults or [])
        self.acs: dict[str, FakeAc] = {}
        self.heater_plug: FakeSwitch | None = None
        self.thermostat = ""
        self.trace = Trace(profile)
        self.coordinator: HorizonCoordinator | None = None

    # devices

    def _log_call(self, entity_id: str, service: str, value: Any) -> None:
        key = {"set_hvac_mode": "hvac_mode", "set_temperature": "temperature", "set_fan_mode": "fan_mode"}.get(service)
        data = {"entity_id": entity_id, **({key: value} if key else {})}
        self.trace.calls.append(Call(self.house.minute, entity_id.split(".", 1)[0], service, entity_id, data))

    async def _add_devices(self) -> None:
        for entity, unit in self.house.acs.items():
            unit.sensed_c = round(self.house.air_c + unit.spec.sensor_bias_c, 1)
            self.acs[entity] = FakeAc(entity, name="Sim AC", modes=self.profile.ac_modes, unit=unit, listener=self._log_call)
        devices: list[Any] = list(self.acs.values())
        if self.house.heater:
            heater = self.house.heater
            self.heater_plug = FakeSwitch(HEATER, name="Sim heater", on_change=lambda on: setattr(heater, "on", on), listener=self._log_call)
            devices.append(self.heater_plug)
        await add_devices(self.hass, *devices)

    def _refresh_acs(self) -> None:
        """Publish what each unit does now: its compressor decides hvac_action, its sensor current_temperature."""
        devices: dict[str, Any] = {**self.acs, **({HEATER: self.heater_plug} if self.heater_plug else {})}
        for entity_id, device in devices.items():
            kinds = {f.kind for f in self.device_faults if f.entity_id == entity_id and f.start_min <= self.house.minute < f.end_min}
            device.available_now = "unavailable" not in kinds
            device.ignore_commands = "ignores" in kinds
            device.fail_commands = "errors" in kinds
            device.async_write_ha_state()

    # sensors

    def _write_sensors(self) -> None:
        house, hass = self.house, self.hass
        fault = house.active_fault()
        temp_attrs = {"unit_of_measurement": "°C", "device_class": "temperature"}
        readings = [
            (ROOM_A, house.room_sensors[0].read(house.air_c, house.rng, fault)),
            (ROOM_B, house.room_sensors[1].read(house.air_c, house.rng, fault)),
            (OUTDOOR, house.outdoor_sensor.read(house.outdoor_c, house.rng, fault)),
        ]
        for entity, (kind, value) in readings:
            if kind == "value":
                hass.states.async_set(entity, str(value), temp_attrs)
            elif kind == "unavailable":
                hass.states.async_set(entity, "unavailable", temp_attrs)
        power_attrs = {"unit_of_measurement": "W", "device_class": "power"}
        if self.profile.power_sensor:
            hass.states.async_set(POWER, str(round(house.power_w(), 1)), power_attrs)
        if self.profile.room_humidity and not (fault is not None and "room" in fault.targets):
            hass.states.async_set(ROOM_RH, str(round(house.rh_pct, 1)), {"unit_of_measurement": "%", "device_class": "humidity"})
        for device in self.profile.fleet:  # each fleet unit has its own meter
            if device.get("power_sensor"):
                ac = house.acs[device["entity_id"]]
                hass.states.async_set(device["power_sensor"], str(round(ac.power_w(house.outdoor_c), 1)), power_attrs)

    # recording

    def _record(self) -> None:
        house = self.house
        self.trace.rows.append(
            Row(
                house.minute, house.hour, house.air_c, house.mass_c, house.outdoor_c,
                {e: ac.mode for e, ac in house.acs.items()},
                {e: ac.compressor for e, ac in house.acs.items()},
                bool(house.heater and house.heater.on),
                house.power_w(),
                house.rh_pct,
            )
        )

    async def _tick(self) -> None:
        self._write_sensors()
        coordinator = self.coordinator
        await coordinator.async_refresh()
        await self.hass.async_block_till_done()
        data = coordinator.data or {}
        self.trace.ticks.append((self.house.minute, bool(coordinator.last_update_success), str(data.get("action"))))
        self.trace.cool_above_c = data.get("cool_on_above_c")
        if data.get("commands_unconfirmed"):
            self.trace.unconfirmed.append((self.house.minute, list(data["commands_unconfirmed"])))
        if data.get("dispatch"):
            self.trace.dispatch.append({"minute": self.house.minute, **data["dispatch"]})

    async def run(self, hours: float) -> Trace:
        profile, hass = self.profile, self.hass
        self.freezer.move_to(START)
        await self._add_devices()
        data: dict[str, Any] = {
            CONF_CLIMATE: profile.acs[0][0],
            **({CONF_POWER: POWER} if profile.power_sensor else {"ac_rated_w": profile.rated_w}),
            **({"room_humidity": ROOM_RH} if profile.room_humidity else {}),
            **dict(profile.settings),
            CONF_ROOM_SENSORS: [ROOM_A, ROOM_B],
            CONF_OUTDOOR: OUTDOOR,
            "control": True,
            CONF_MIN_ON_MIN: profile.min_on_min,
            CONF_MIN_OFF_MIN: profile.min_off_min,
        }
        if profile.heater_kw:
            data[CONF_HEATER_SWITCH] = HEATER
        if profile.fleet:
            data["fleet"] = [dict(d) for d in profile.fleet]
            data["dispatch_mode"] = profile.dispatch_mode
        self._write_sensors()
        entry = MockConfigEntry(domain=DOMAIN, title="Sim room", data=data, unique_id=profile.acs[0][0])
        entry.add_to_hass(hass)
        real_update = HorizonCoordinator._async_update_data
        trace = self.trace

        async def counted(coordinator):
            trace.updates += 1
            return await real_update(coordinator)

        with patch.object(HorizonCoordinator, "_async_update_data", counted):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
            self.coordinator = entry.runtime_data
            registry = er.async_get(hass)
            self.thermostat = next(e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id) if e.domain == "climate")
            if profile.mode:
                await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": self.thermostat, "hvac_mode": profile.mode}, blocking=True)
                await hass.async_block_till_done()
            data_now = self.coordinator.data or {}
            self.trace.ticks.append((0, bool(self.coordinator.last_update_success), str(data_now.get("action"))))
            self._record()
            for _ in range(int(hours * 60) // TICK_MIN):
                for minute_in_tick in range(TICK_MIN):
                    self.freezer.tick(timedelta(minutes=1))
                    self.house.step()
                    self._refresh_acs()
                    self._record()
                    if minute_in_tick < TICK_MIN - 1:
                        async_fire_time_changed(hass, dt_util.utcnow())
                        await hass.async_block_till_done()
                await self._tick()
            self.trace.command_failures = list(self.coordinator._command_failures)
        return self.trace
