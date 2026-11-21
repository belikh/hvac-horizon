"""Closed-loop harness: the real integration against a simulated house.

Each 5 minute tick the harness writes the sensor states from the house, refreshes the coordinator and lets the
fake climate and switch services update the simulated devices. Between ticks it advances the frozen clock one
minute at a time, fires the timers that fall due (the heater PWM edges) and steps the house. The result is a
Trace: one row per minute, every service call, and the outcome of every tick.

The run uses no price, humidity, weather, presence or schedule entities, so no MPC plan and no setback runs.
The controller reads the room sensors, the outdoor sensor and one power sensor.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from unittest.mock import patch

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
from tests.sim.house import AcSpec, Ac, Envelope, Heater, House, SensorFault, Weather

ROOM_A = "sensor.sim_room_a"
ROOM_B = "sensor.sim_room_b"
OUTDOOR = "sensor.sim_outdoor"
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


@dataclass
class Trace:
    profile: Profile
    rows: list[Row] = field(default_factory=list)  # one per minute, row 0 is the start
    calls: list[Call] = field(default_factory=list)
    ticks: list[tuple[int, bool, str]] = field(default_factory=list)  # minute, last_update_success, action
    updates: int = 0  # coordinator update calls, one per tick
    cool_above_c: float | None = None
    dispatch: list[dict[str, Any]] = field(default_factory=list)


class Harness:
    def __init__(self, hass, freezer, profile: Profile, faults: list[SensorFault] | None = None, seed: int = 1) -> None:
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
            faults=list(faults or []),
        )
        self.trace = Trace(profile)
        self.coordinator: HorizonCoordinator | None = None

    # device states and services

    def _put(self, entity_id: str, state: str, attributes: dict[str, Any] | None = None) -> None:
        """Set a climate or switch state stamped with the frozen clock, so last_changed is the simulated time."""
        self.hass.states.async_set(entity_id, state, attributes or {}, timestamp=time.time())

    def _mirror_ac(self, entity_id: str) -> None:
        ac = self.house.acs[entity_id]
        self._put(entity_id, ac.mode, {"temperature": ac.setpoint_c})

    def _register_services(self) -> None:
        hass, house, trace = self.hass, self.house, self.trace

        async def set_hvac_mode(call):
            entity = call.data["entity_id"]
            trace.calls.append(Call(house.minute, "climate", "set_hvac_mode", entity, dict(call.data)))
            house.acs[entity].mode = call.data["hvac_mode"]
            self._mirror_ac(entity)

        async def set_temperature(call):
            entity = call.data["entity_id"]
            trace.calls.append(Call(house.minute, "climate", "set_temperature", entity, dict(call.data)))
            house.acs[entity].setpoint_c = float(call.data["temperature"])
            self._mirror_ac(entity)

        async def turn_on(call):
            entity = call.data["entity_id"]
            trace.calls.append(Call(house.minute, "switch", "turn_on", entity, dict(call.data)))
            house.heater.on = True
            self._put(entity, "on")

        async def turn_off(call):
            entity = call.data["entity_id"]
            trace.calls.append(Call(house.minute, "switch", "turn_off", entity, dict(call.data)))
            house.heater.on = False
            self._put(entity, "off")

        hass.services.async_register("climate", "set_hvac_mode", set_hvac_mode)
        hass.services.async_register("climate", "set_temperature", set_temperature)
        hass.services.async_register("switch", "turn_on", turn_on)
        hass.services.async_register("switch", "turn_off", turn_off)

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
        hass.states.async_set(POWER, str(round(house.power_w(), 1)), power_attrs)
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
        if data.get("dispatch"):
            self.trace.dispatch.append({"minute": self.house.minute, **data["dispatch"]})

    async def run(self, hours: float) -> Trace:
        profile, hass = self.profile, self.hass
        self.freezer.move_to(START)
        for entity, _spec in profile.acs:
            self._mirror_ac(entity)
        if self.house.heater:
            self._put(HEATER, "off")
        self._register_services()
        data: dict[str, Any] = {
            CONF_CLIMATE: profile.acs[0][0],
            CONF_POWER: POWER,
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
            data_now = self.coordinator.data or {}
            self.trace.ticks.append((0, bool(self.coordinator.last_update_success), str(data_now.get("action"))))
            self._record()
            for _ in range(int(hours * 60) // TICK_MIN):
                for minute_in_tick in range(TICK_MIN):
                    self.freezer.tick(timedelta(minutes=1))
                    self.house.step()
                    self._record()
                    if minute_in_tick < TICK_MIN - 1:
                        async_fire_time_changed(hass, dt_util.utcnow())
                        await hass.async_block_till_done()
                await self._tick()
        return self.trace
