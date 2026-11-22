"""Every setting changes what the integration does.

A setting that is stored and shown but never reaches control is a fault: the owner's AC cooled in heat mode because
the mode was stored and never read. Each setting here runs the same scripted day twice, once with a base entry and
once with the setting changed, and the two runs are compared.

`control`: the commands sent to devices differ. `published`: only published data differs (the setting feeds a sensor,
a forecast or a report and drives nothing). The expected level of every setting is written in SETTINGS. A setting
that falls below its level fails, and so does a setting in const.py that is missing from SETTINGS.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import SupportsResponse
from homeassistant.util import dt as dt_util
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.hvac_horizon.const import DOMAIN

START = "2026-11-20T00:00:00+00:00"

AC = "climate.audit_ac"
AC_POWER = "sensor.audit_ac_power"
AC2 = "climate.audit_ac_2"
AC2_POWER = "sensor.audit_ac_2_power"
ROOM_1 = "sensor.audit_room_1"
ROOM_2 = "sensor.audit_room_2"
ROOM_ALT = "sensor.audit_room_alt"
OUTDOOR = "sensor.audit_outdoor"
OUTDOOR_ALT = "sensor.audit_outdoor_alt"
PRICE = "sensor.audit_price"
WEATHER = "weather.audit"
OUT_RH = "sensor.audit_outdoor_humidity"
ROOM_RH = "sensor.audit_room_humidity"
ENERGY = "sensor.audit_energy"
COVER = "cover.audit_blind"
WINDOW = "binary_sensor.audit_window"
CO2 = "sensor.audit_co2"
PM25 = "sensor.audit_pm25"
FAN = "switch.audit_fan"
DEHUMIDIFIER = "switch.audit_dehumidifier"
HUMIDIFIER = "switch.audit_humidifier"
BOILER = "switch.audit_boiler"
SCHEDULE = "schedule.audit"
MOTION = "binary_sensor.audit_motion"
PERSON = "person.audit"
VALVE = "number.audit_valve"
TRV = "sensor.audit_trv"
HOUSE_POWER = "sensor.audit_house_power"
VACATION = "switch.audit_vacation"
HEATER = "switch.audit_heater"
HOUSE_FAN = "switch.audit_house_fan"
ACTUATOR = "cover.audit_window"
IRRADIANCE = "sensor.audit_irradiance"
HEATWAVE = "binary_sensor.audit_heatwave"
SHED_LOAD = "switch.audit_pool_pump"
OUT_MIN = "number.audit_out_min"
OUT_MAX = "number.audit_out_max"
FLEET_SMALL = "climate.audit_fleet_small"
FLEET_BIG = "climate.audit_fleet_big"
FLEET_HEAT = "switch.audit_fleet_heater"
PEER_AC = "climate.audit_peer_ac"
PEER_POWER = "sensor.audit_peer_power"
PEER_ROOM = "sensor.audit_peer_room"
PEER_VALVE = "number.audit_peer_valve"

TEMP = {"unit_of_measurement": "°C", "device_class": "temperature"}
AC_ATTRS = {
    "temperature": 24.0,
    "target_temp_step": 1.0,
    "min_temp": 16.0,
    "max_temp": 32.0,
    "hvac_modes": ["off", "cool", "heat", "dry", "fan_only"],
    "fan_modes": ["low", "medium", "high"],
    "fan_mode": "low",
}

# The rich entry: every optional device and sensor that can share one room. A setting's base starts from this.
RICH: dict[str, Any] = {
    "climate": AC,
    "power": AC_POWER,
    "room_sensors": [ROOM_1, ROOM_2],
    "outdoor": OUTDOOR,
    "control": True,
    "price": PRICE,
    "weather": WEATHER,
    "humidity": OUT_RH,
    "room_humidity": ROOM_RH,
    "energy": ENERGY,
    "cover": COVER,
    "window": WINDOW,
    "co2": CO2,
    "pm25": PM25,
    "fan": FAN,
    "humidifier": HUMIDIFIER,
    "schedule": SCHEDULE,
    "motion": MOTION,
    "presence": [PERSON],
    "whole_house_power": HOUSE_POWER,
    "vacation": VACATION,
    "heater_switch": HEATER,
    "whole_house_fan": HOUSE_FAN,
    "window_actuator": ACTUATOR,
    "irradiance": IRRADIANCE,
    "window_area_m2": 4.0,
    "heatwave_warning": HEATWAVE,
    "shed_max_w": 8000.0,
    "shed_loads": [f"{SHED_LOAD}|1500"],
    "lock_pin": "1234",
    "eco_c": 26.0,
}

NO_HEATER = {"drop": ["heater_switch"]}
FLEET = [
    {"device_id": "small", "kind": "cool", "entity_id": FLEET_SMALL, "rate_k_per_h_per_kw": 3.0, "rated_w": 700.0},
    {"device_id": "big", "kind": "cool", "entity_id": FLEET_BIG, "rate_k_per_h_per_kw": 2.0, "rated_w": 3000.0},
    {"device_id": "panel", "kind": "heat", "entity_id": FLEET_HEAT, "rate_k_per_h_per_kw": 1.5, "rated_w": 1500.0},
]
TPI = {"add": {"heater_algorithm": "tpi"}}
VALVE_BASE = {"add": {"valve": VALVE}}

# setting: (level, base change, the change under test). A change is {"add": {...}} or {"drop": [...]}.
# `peer` adds a second entry in a cooler room. `why` explains a level below control.
SETTINGS: dict[str, dict[str, Any]] = {
    "climate": {"level": "control", "alt": {"add": {"climate": AC2}}},
    "power": {"level": "published", "alt": {"add": {"power": AC2_POWER}}, "why": "feeds the fit, energy and the running check"},
    "room_sensors": {"level": "control", "alt": {"add": {"room_sensors": [ROOM_ALT]}}},
    "outdoor": {"level": "control", "alt": {"add": {"outdoor": OUTDOOR_ALT}}},
    "climate_2": {"level": "control", "alt": {"add": {"climate_2": AC2}}},
    "power_2": {"level": "published", "base": {"add": {"climate_2": AC2}}, "alt": {"add": {"power_2": AC2_POWER}}, "why": "enables the two-device fit"},
    "weather": {"level": "published", "alt": {"drop": ["weather"]}, "why": "forecast series, night plan and heatwave"},
    "heatwave_warning": {"level": "published", "alt": {"drop": ["heatwave_warning"]}, "why": "forces the heatwave flag"},
    "price": {"level": "control", "alt": {"drop": ["price"]}},
    "humidity": {"level": "control", "alt": {"drop": ["humidity"]}},
    "room_humidity": {"level": "control", "alt": {"drop": ["room_humidity"]}},
    "energy": {"level": "exempt", "why": "interval power falls back to the energy counter when the power sensor has no interval mean; tests/test_collector.py covers it"},
    "cover": {"level": "control", "alt": {"drop": ["cover"]}},
    "window": {"level": "control", "alt": {"drop": ["window"]}},
    "co2": {"level": "control", "alt": {"drop": ["co2"]}},
    "fan": {"level": "control", "alt": {"drop": ["fan"]}},
    "dehumidifier": {"level": "control", "alt": {"add": {"dehumidifier": DEHUMIDIFIER}}},
    "boiler": {"level": "control", "base": NO_HEATER, "alt": {"add": {"boiler": BOILER}}},
    "humidifier": {"level": "control", "alt": {"drop": ["humidifier"]}},
    "pm25": {"level": "control", "alt": {"drop": ["pm25"]}},
    "schedule": {"level": "control", "alt": {"drop": ["schedule"]}},
    "motion": {"level": "control", "alt": {"drop": ["motion"]}},
    "presence": {"level": "control", "alt": {"drop": ["presence"]}},
    "valve": {"level": "control", "alt": {"add": {"valve": VALVE}}},
    "trv_temperature": {"level": "control", "base": VALVE_BASE, "alt": {"add": {"trv_temperature": TRV}}},
    "whole_house_power": {"level": "control", "alt": {"drop": ["whole_house_power"]}},
    "vacation": {"level": "control", "alt": {"drop": ["vacation"]}},
    "vacation_c": {"level": "control", "alt": {"add": {"vacation_c": 25.0}}},
    "heater_switch": {"level": "control", "alt": {"drop": ["heater_switch"]}},
    "radiators": {"level": "control", "alt": {"add": {"radiators": True}}},
    "heater_kp": {"level": "control", "alt": {"add": {"heater_kp": 2.0}}},
    "ac_rated_w": {"level": "published", "base": {"drop": ["power"], "add": {"ac_rated_w": 800}}, "alt": {"add": {"ac_rated_w": 1600}}, "why": "the AC's power in each reading, and the forecast's energy"},
    "heater_power_w": {"level": "published", "alt": {"add": {"heater_power_w": 2000}}, "why": "energy and cost of the heating forecast"},
    "heater_ki": {"level": "control", "alt": {"add": {"heater_ki": 1.0}}},
    "heater_kd": {"level": "control", "alt": {"add": {"heater_kd": 8.0}}},
    "heater_algorithm": {"level": "control", "alt": {"add": {"heater_algorithm": "tpi"}}},
    "tpi_coef_int": {"level": "control", "base": TPI, "alt": {"add": {"tpi_coef_int": 0.05}}},
    "tpi_coef_ext": {"level": "control", "base": TPI, "alt": {"add": {"tpi_coef_ext": 0.08}}},
    "tpi_threshold_high": {"level": "control", "base": {"add": {"heater_algorithm": "tpi", "tpi_threshold_low": 0.1}}, "alt": {"add": {"tpi_threshold_high": 0.2}}},
    "tpi_threshold_low": {"level": "control", "base": {"add": {"heater_algorithm": "tpi", "tpi_threshold_high": 0.2}}, "alt": {"add": {"tpi_threshold_low": 0.1}}},
    "max_on_percent": {"level": "control", "base": TPI, "alt": {"add": {"max_on_percent": 20.0}}},
    "whole_house_fan": {"level": "control", "alt": {"drop": ["whole_house_fan"]}},
    "lock_pin": {"level": "control", "alt": {"add": {"lock_pin": "9999"}}},
    "coil_dry": {"level": "control", "alt": {"add": {"coil_dry": True}}},
    "dry_for_humidity": {"level": "control", "alt": {"add": {"dry_for_humidity": True}}},
    "ac_heat": {"level": "control", "base": NO_HEATER, "alt": {"add": {"ac_heat": True}}},
    "mpc_heat_plan": {"level": "control", "base": NO_HEATER, "alt": {"add": {"mpc_heat_plan": True}}},
    "night_plan_act": {"level": "control", "alt": {"add": {"night_plan_act": True}}},
    "economizer_fan": {"level": "control", "alt": {"add": {"economizer_fan": True}}},
    "accuracy_panel": {"level": "exempt", "why": "registers the sidebar panel; covered by tests of accuracy_api"},
    "window_actuator": {"level": "control", "alt": {"drop": ["window_actuator"]}},
    "peer_entry": {"level": "control", "peer": True, "alt": {"add": {"peer_entry": "audit-peer"}}},
    "peer_setpoint_mode": {"level": "control", "peer": True, "base": {"add": {"peer_entry": "audit-peer"}}, "alt": {"add": {"peer_setpoint_mode": True}}},
    "auto_fan": {"level": "control", "alt": {"add": {"auto_fan": True}}},
    "setpoint_regulation": {"level": "control", "alt": {"add": {"setpoint_regulation": False}}},
    "house_sealed": {"level": "control", "alt": {"add": {"house_sealed": True}}},
    "price_cheap": {"level": "control", "alt": {"add": {"price_cheap": 0.02}}},
    "price_expensive": {"level": "control", "alt": {"add": {"price_expensive": 0.9}}},
    "min_on_min": {"level": "exempt", "why": "deprecated and documented as ignored: a stop is never delayed"},
    "min_off_min": {"level": "control", "alt": {"add": {"min_off_min": 12.0}}},
    "away_delay_min": {"level": "control", "alt": {"add": {"away_delay_min": 5.0}}},
    "motion_away_min": {"level": "control", "alt": {"add": {"motion_away_min": 5.0}}},
    "stall_window_s": {"level": "control", "alt": {"add": {"stall_window_s": 300.0}}},
    "shed_max_w": {"level": "control", "alt": {"add": {"shed_max_w": 20000.0}}},
    "shed_loads": {"level": "control", "alt": {"drop": ["shed_loads"]}},
    "compressor_group": {"level": "control", "peer": True, "peer_offset": 0.5, "alt": {"add": {"compressor_group": "roof"}}},
    "comfort_c": {"level": "control", "alt": {"add": {"comfort_c": 21.0}}},
    "eco_c": {"level": "control", "alt": {"add": {"eco_c": 24.5}}},
    "comfort_energy_weight": {"level": "control", "alt": {"add": {"comfort_energy_weight": 1.0}}},
    "heat_eco_c": {"level": "control", "alt": {"add": {"heat_eco_c": 17.0}}},
    "heat_boost_c": {"level": "control", "alt": {"add": {"heat_boost_c": 22.0}}},
    "boost_c": {"level": "control", "alt": {"add": {"boost_c": 19.0}}},
    "away_c": {"level": "control", "alt": {"add": {"away_c": 25.0}}},
    "window_delay_min": {"level": "control", "alt": {"add": {"window_delay_min": 2.0}}},
    "window_azimuth": {"level": "published", "base": {"drop": ["irradiance"]}, "alt": {"add": {"window_azimuth": 0.0}}, "why": "modelled solar gain"},
    "window_area_m2": {"level": "published", "alt": {"add": {"window_area_m2": 12.0}}, "why": "solar gain from irradiance"},
    "irradiance": {"level": "published", "alt": {"drop": ["irradiance"]}, "why": "measured solar gain"},
    "control": {"level": "control", "alt": {"add": {"control": False}}},
    "heater_inverted": {"level": "control", "alt": {"add": {"heater_inverted": True}}},
    "output_min": {"level": "control", "alt": {"add": {"output_min": 100.0}}},
    "output_max": {"level": "control", "alt": {"add": {"output_max": 30.0}}},
    "output_min_entity": {"level": "control", "alt": {"add": {"output_min_entity": OUT_MIN}}},
    "output_max_entity": {"level": "control", "alt": {"add": {"output_max_entity": OUT_MAX}}},
    "schedule_blocks": {"level": "control", "alt": {"add": {"schedule_blocks": [{"start_h": 0, "end_h": 24, "preset": "eco"}]}}},
    "fleet": {"level": "control", "alt": {"add": {"fleet": FLEET}}},
    "dispatch_mode": {"level": "control", "base": {"add": {"fleet": FLEET}}, "alt": {"add": {"dispatch_mode": "economy"}}},
    "ai_conversation": {"level": "published", "base": {"add": {"ai_budget_per_day": 50}}, "alt": {"add": {"ai_conversation": "conversation.audit"}}, "why": "the investigator's model text"},
    "ai_budget_per_day": {"level": "published", "base": {"add": {"ai_conversation": "conversation.audit"}}, "alt": {"add": {"ai_budget_per_day": 50}}, "why": "allows the investigator's model calls"},
}


def _apply(data: dict[str, Any], change: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(data)
    for key in (change or {}).get("drop", []):
        out.pop(key, None)
    out.update((change or {}).get("add", {}))
    return out


class House:
    """The fake devices and the scripted day."""

    def __init__(self, hass, freezer) -> None:
        self.hass = hass
        self.freezer = freezer
        self.calls: list[tuple] = []
        self.lines: list[str] = []
        self.published: list[str] = []
        self.env: dict[str, Any] = {}
        self.stalled = False
        self.energy = 100.0
        self.peer_offset = -3.5

    def register(self) -> None:
        hass = self.hass

        def target(call) -> str:
            entity = call.data["entity_id"]
            return entity[0] if isinstance(entity, list) else entity

        def mirror(domain: str, service: str, new_state=None, attr: str | None = None, field: str | None = None):
            async def handler(call):
                entity = target(call)
                value = call.data.get(field) if field else None
                self.calls.append((f"{domain}.{service}", entity, value))
                state = hass.states.get(entity)
                attributes = dict(state.attributes) if state else {}
                current = state.state if state else "unknown"
                if attr:
                    attributes[attr] = value
                if field and not attr:
                    current = str(value)
                if new_state is not None:
                    current = new_state
                hass.states.async_set(entity, current, attributes, timestamp=dt_util.utcnow().timestamp())

            return handler

        hass.services.async_register("climate", "set_hvac_mode", mirror("climate", "set_hvac_mode", field="hvac_mode"))
        hass.services.async_register("climate", "set_temperature", mirror("climate", "set_temperature", attr="temperature", field="temperature"))
        hass.services.async_register("climate", "set_fan_mode", mirror("climate", "set_fan_mode", attr="fan_mode", field="fan_mode"))
        hass.services.async_register("switch", "turn_on", mirror("switch", "turn_on", new_state="on"))
        hass.services.async_register("switch", "turn_off", mirror("switch", "turn_off", new_state="off"))
        hass.services.async_register("number", "set_value", mirror("number", "set_value", field="value"))
        hass.services.async_register("cover", "open_cover", mirror("cover", "open_cover", new_state="open"))
        hass.services.async_register("cover", "close_cover", mirror("cover", "close_cover", new_state="closed"))

        async def forecasts(call):
            entity = target(call)
            now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
            if call.data["type"] == "hourly":
                items = [
                    {"datetime": (now + timedelta(hours=h)).isoformat(), "temperature": self.env["outdoor"] + (3.0 if h < 6 else -6.0), "condition": "sunny"}
                    for h in range(48)
                ]
            else:
                items = [{"datetime": (now + timedelta(days=d)).isoformat(), "temperature": 28.0, "templow": 17.0, "condition": "sunny"} for d in range(5)]
            return {entity: {"forecast": items}}

        hass.services.async_register("weather", "get_forecasts", forecasts, supports_response=SupportsResponse.ONLY)

        async def converse(call):
            self.calls.append(("conversation.process", call.data.get("agent_id"), None))
            return {"response": {"speech": {"plain": {"speech": "The room is above the cool threshold."}}}}

        hass.services.async_register("conversation", "process", converse, supports_response=SupportsResponse.ONLY)

    def reset_states(self) -> None:
        hass = self.hass
        self.stalled = False
        self.energy = 100.0
        # Remove every fake first, so each one starts with a fresh last_changed and no state from an earlier run.
        for entity_id in [e for e in hass.states.async_entity_ids() if ".audit" in e]:
            hass.states.async_remove(entity_id)
        old = dt_util.utcnow().timestamp() - 3600
        for ac in (AC, AC2, FLEET_SMALL, FLEET_BIG, PEER_AC):
            hass.states.async_set(ac, "off", dict(AC_ATTRS), timestamp=old)
        for switch in (FAN, DEHUMIDIFIER, HUMIDIFIER, BOILER, VACATION, HEATER, HOUSE_FAN, SHED_LOAD, FLEET_HEAT):
            hass.states.async_set(switch, "off", timestamp=old)
        hass.states.async_set(SHED_LOAD, "on", timestamp=old)
        for cover in (COVER, ACTUATOR):
            hass.states.async_set(cover, "closed" if cover == ACTUATOR else "open")
        for number, value in ((VALVE, "0"), (PEER_VALVE, "0"), (OUT_MIN, "100"), (OUT_MAX, "30")):
            hass.states.async_set(number, value)
        hass.states.async_set(WEATHER, "sunny", {"temperature": 30.0})
        hass.states.async_set(SCHEDULE, "off", {"next_event": (dt_util.utcnow() + timedelta(hours=2)).isoformat()})
        self.set_env(room=24.0, outdoor=24.0)

    def set_env(self, **changes: Any) -> None:
        defaults = {
            "room": 24.0, "outdoor": 24.0, "rh": 50.0, "price": 0.28, "window": "off", "co2": 600.0, "pm25": 5.0,
            "motion": "on", "person": "home", "house_w": 2000.0, "vacation": "off", "irradiance": 100.0, "heatwave": "off",
            "trv": None, "schedule": "off", "out_rh": 55.0, "deadline": None,
        }
        self.env = {**defaults, **self.env, **changes}
        self.write_env()

    def write_env(self) -> None:
        hass, env = self.hass, self.env
        if self.stalled:
            return
        room = env["room"]
        hass.states.async_set(ROOM_1, f"{room:.2f}", TEMP, force_update=True)
        hass.states.async_set(ROOM_2, f"{room + 0.2:.2f}", TEMP, force_update=True)
        hass.states.async_set(ROOM_ALT, f"{room - 4.0:.2f}", TEMP, force_update=True)
        hass.states.async_set(PEER_ROOM, f"{room + self.peer_offset:.2f}", TEMP, force_update=True)
        hass.states.async_set(OUTDOOR, f"{env['outdoor']:.2f}", TEMP, force_update=True)
        hass.states.async_set(OUTDOOR_ALT, f"{env['outdoor'] - 12.0:.2f}", TEMP, force_update=True)
        hass.states.async_set(TRV, f"{(env['trv'] if env['trv'] is not None else room + 3.0):.2f}", TEMP, force_update=True)
        hass.states.async_set(ROOM_RH, f"{env['rh']:.0f}", {"unit_of_measurement": "%"}, force_update=True)
        hass.states.async_set(OUT_RH, f"{env['out_rh']:.0f}", {"unit_of_measurement": "%"}, force_update=True)
        hass.states.async_set(PRICE, f"{env['price']}", {"unit_of_measurement": "$/kWh", "forecast": []}, force_update=True)
        hass.states.async_set(WINDOW, env["window"])
        hass.states.async_set(CO2, f"{env['co2']:.0f}", force_update=True)
        hass.states.async_set(PM25, f"{env['pm25']:.0f}", force_update=True)
        hass.states.async_set(MOTION, env["motion"])
        hass.states.async_set(PERSON, env["person"])
        hass.states.async_set(HOUSE_POWER, f"{env['house_w']:.0f}", {"unit_of_measurement": "W"}, force_update=True)
        hass.states.async_set(VACATION, env["vacation"])
        hass.states.async_set(IRRADIANCE, f"{env['irradiance']:.0f}", {"unit_of_measurement": "W/m²"}, force_update=True)
        hass.states.async_set(HEATWAVE, env["heatwave"])
        deadline = env["deadline"] or dt_util.utcnow() + timedelta(hours=6)
        hass.states.async_set(SCHEDULE, env["schedule"], {"next_event": deadline.isoformat()})
        self.energy += 0.05
        hass.states.async_set(ENERGY, f"{self.energy:.2f}", {"unit_of_measurement": "kWh"}, force_update=True)
        for ac, power in ((AC, AC_POWER), (AC2, AC2_POWER), (PEER_AC, PEER_POWER)):
            state = hass.states.get(ac)
            running = state is not None and state.state in ("cool", "heat", "dry")
            watts = (1000 if ac != AC2 else 2400) if running else 5
            hass.states.async_set(power, str(watts), {"unit_of_measurement": "W", "device_class": "power"}, force_update=True)
            if state is not None:
                # The AC's own sensor reads cooler than the room, as the owner's portable unit does.
                attributes = {**state.attributes, "current_temperature": round(room - 1.6, 1)}
                hass.states.async_set(ac, state.state, attributes, timestamp=state.last_changed.timestamp())

    async def tick(self, entries, minutes: float = 5.0, count: int = 1) -> None:
        for _ in range(count):
            self.freezer.tick(timedelta(minutes=minutes))
            async_fire_time_changed(self.hass)  # timers such as the heater PWM edges and held starts run
            await self.hass.async_block_till_done()
            self.write_env()
            for entry in entries:
                await entry.runtime_data.async_refresh()
            await self.hass.async_block_till_done()
            self.snapshot(entries[0])

    def snapshot(self, entry) -> None:
        # Sorted: two entries refresh in no fixed order within a tick.
        self.lines.append("calls " + json.dumps(sorted(self.calls, key=str), default=str))
        self.calls.clear()
        data = entry.runtime_data.data or {}
        self.published.append(json.dumps(_rounded(data), sort_keys=True, default=str))

    def note(self, text: str) -> None:
        self.lines.append(text)

    async def day(self, entries) -> None:
        """The scripted day. Each phase puts the room in a state that some setting acts on."""
        main = entries[0].runtime_data
        tick, env = self.tick, self.set_env
        env(room=27.0, outdoor=32.0)
        await tick(entries, count=4)
        for preset, room in (("eco", 25.3), ("boost", 23.4), ("away", 26.5), ("comfort", 24.6)):
            main.set_manual_preset(preset)
            env(room=room)
            await tick(entries, count=2)
        env(room=25.0, price=0.6)
        await tick(entries, count=2)
        env(room=24.3, price=0.01)
        await tick(entries, count=2)
        env(price=0.28, room=27.0, window="on")
        await tick(entries, minutes=3.0, count=6)
        env(window="off", room=25.5, outdoor=18.0)
        await tick(entries, count=4)
        env(room=25.0)  # free cooling works: the room falls, so it is not given up
        await tick(entries, count=5)
        env(room=25.5, outdoor=21.0, out_rh=98.0, rh=45.0)
        await tick(entries, count=3)
        env(out_rh=55.0, rh=50.0)
        env(room=23.8, outdoor=22.0, rh=75.0)
        await tick(entries, count=3)
        env(rh=30.0)
        await tick(entries, count=2)
        env(rh=50.0, co2=1500.0)
        await tick(entries, count=2)
        env(co2=600.0)
        await tick(entries, count=2)
        env(pm25=80.0)
        await tick(entries, count=2)
        env(pm25=5.0, room=24.5, outdoor=30.0, irradiance=900.0, schedule="on")
        await tick(entries, count=2)
        env(irradiance=100.0, schedule="off", heatwave="on", room=25.0)
        await tick(entries, count=2)
        env(heatwave="off", room=26.0, vacation="on")
        await tick(entries, count=2)
        env(vacation="off", room=26.5, house_w=9500.0)
        await tick(entries, count=2)
        env(house_w=2000.0)
        await tick(entries, count=2)
        # Lock with the PIN the form holds in the rich entry, then try to unlock with it.
        self.note(f"lock {_attempt(main.set_lock, True, '1234')} unlock {_attempt(main.set_lock, False, '1234')}")
        _attempt(main.set_lock, False, "9999")
        await tick(entries, count=1)
        # The AC stops, then the room is hot again 6 minutes later: a restart delay longer than that holds the start.
        env(room=22.0)
        await tick(entries, minutes=2.0, count=1)
        env(room=27.0)
        await tick(entries, minutes=3.0, count=3)
        # Cold part of the day.
        env(room=19.0, outdoor=4.0, trv=24.0)
        await tick(entries, count=5)
        for preset in ("eco", "boost", "comfort"):
            main.set_manual_preset(preset)
            await tick(entries, count=3)
        # A room just below the heat target: the heater runs part of the time, so gains and limits show.
        env(room=23.0, outdoor=12.0)
        await tick(entries, minutes=2.0, count=12)
        env(room=23.3, outdoor=14.0)
        await tick(entries, minutes=2.0, count=8)
        env(room=23.7)
        await tick(entries, minutes=2.0, count=6)
        main.set_manual_preset("boost")
        await tick(entries, minutes=2.0, count=6)
        main.set_manual_preset("comfort")
        env(room=24.1)
        await tick(entries, minutes=2.0, count=4)
        env(room=23.9)
        await tick(entries, minutes=2.0, count=4)
        # A sensor outage of 12 minutes.
        self.stalled = True
        await tick(entries, minutes=4.0, count=3)
        self.stalled = False
        # A quiet 70 minutes: in auto the AC does not cool within 60 minutes of the heater's last run.
        env(room=24.0, outdoor=20.0)
        await tick(entries, minutes=10.0, count=7)
        # Nobody moves in the room for 52 minutes while a schedule event is 50 minutes away. Then the house empties
        # for 40 minutes and gets cold.
        env(room=25.0, outdoor=31.0, motion="off", deadline=dt_util.utcnow() + timedelta(minutes=50))
        await tick(entries, minutes=4.0, count=13)
        env(room=20.0, outdoor=10.0, person="not_home", deadline=None)
        await tick(entries, minutes=4.0, count=10)
        env(motion="on", person="home", room=26.0, outdoor=29.0)
        # Evening and night, for the night plan.
        await tick(entries, minutes=60.0, count=14)
        for mode in ("cool", "heat", "dry"):
            main.set_user_off(False, mode)
            await tick(entries, count=2)
        main.set_user_off(True)
        await tick(entries, count=1)


def _attempt(function, *args) -> str:
    try:
        return repr(function(*args))
    except Exception as err:  # the result text is part of the comparison
        return f"{type(err).__name__}: {err}"


def _rounded(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, dict):
        return {str(k): _rounded(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_rounded(v) for v in value]
    return value


async def _run(hass, freezer, house: House, data: dict[str, Any], peer: bool, hass_storage) -> tuple[list[str], list[str]]:
    freezer.move_to(START)
    for key in [k for k in hass_storage if k.startswith(DOMAIN)]:
        hass_storage.pop(key)
    hass.data.pop(DOMAIN, None)
    house.calls.clear()
    house.lines, house.published, house.env = [], [], {}
    house.reset_states()
    entries = [MockConfigEntry(domain=DOMAIN, title="Audit", entry_id="audit-main", data=data, unique_id=data["climate"])]
    if peer:
        peer_data = {
            "climate": PEER_AC, "power": PEER_POWER, "room_sensors": [PEER_ROOM], "outdoor": OUTDOOR, "control": True,
            "valve": PEER_VALVE, "compressor_group": "roof",
        }
        entries.append(MockConfigEntry(domain=DOMAIN, title="Audit peer", entry_id="audit-peer", data=peer_data, unique_id=PEER_AC))
    for entry in entries:
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    house.snapshot(entries[0])
    await house.day(entries)
    lines, published = house.lines, house.published
    for entry in entries:
        assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    return lines, published


_BASE_RUNS: dict[str, tuple[list[str], list[str]]] = {}


async def _compare(hass, freezer, hass_storage, spec: dict[str, Any]) -> str:
    """"control" when device commands differ, "published" when only published data differs, "none" otherwise."""
    house = House(hass, freezer)
    house.register()
    base = _apply(RICH, spec.get("base"))
    changed = _apply(base, spec["alt"])
    assert changed != base
    peer = bool(spec.get("peer"))
    house.peer_offset = spec.get("peer_offset", -3.5)
    # The base run is repeatable (test_the_scripted_day_is_repeatable), so settings that share a base share its run.
    cache_key = json.dumps([base, peer, house.peer_offset], sort_keys=True, default=str)
    if cache_key not in _BASE_RUNS:
        _BASE_RUNS[cache_key] = await _run(hass, freezer, house, base, peer, hass_storage)
    base_lines, base_published = _BASE_RUNS[cache_key]
    alt_lines, alt_published = await _run(hass, freezer, house, changed, peer, hass_storage)
    if base_lines != alt_lines:
        return "control"
    return "published" if base_published != alt_published else "none"


def _const_keys() -> set[str]:
    text = (Path(__file__).parents[2] / "custom_components" / DOMAIN / "const.py").read_text(encoding="utf-8")
    return set(re.findall(r'^CONF_[A-Z0-9_]+\s*=\s*"([^"]+)"', text, re.M))


def test_every_setting_in_const_has_an_audit_row():
    assert _const_keys() == set(SETTINGS)


async def test_the_scripted_day_is_repeatable(hass, enable_custom_integrations, freezer, hass_storage):
    """Two runs of the same entry give the same commands and data. Without this, any difference would prove nothing."""
    house = House(hass, freezer)
    house.register()
    first = await _run(hass, freezer, house, dict(RICH), True, hass_storage)
    second = await _run(hass, freezer, house, dict(RICH), True, hass_storage)
    assert first[0] == second[0]
    assert first[1] == second[1]
    assert sum(line != "calls []" for line in first[0]) > 20  # the day drives devices


LEVELS = {"none": 0, "published": 1, "control": 2}


# Rows the scripted day does not yet show at their level. Each is traced by hand before it leaves this list.
OPEN_ROWS: dict[str, str] = {}


def _row(key: str):
    marks = [pytest.mark.xfail(strict=True, reason=OPEN_ROWS[key])] if key in OPEN_ROWS else []
    return pytest.param(key, marks=marks)


@pytest.mark.parametrize("key", [_row(k) for k in sorted(k for k, spec in SETTINGS.items() if spec["level"] != "exempt")])
async def test_setting_changes_what_the_integration_does(hass, enable_custom_integrations, freezer, hass_storage, key):
    spec = SETTINGS[key]
    found = await _compare(hass, freezer, hass_storage, spec)
    assert LEVELS[found] >= LEVELS[spec["level"]], f"{key}: expected {spec['level']}, the scripted day shows {found}"


MATRIX_PATH = Path(__file__).resolve().parents[2] / "docs" / "feature-matrix.md"
LEVEL_TEXT = {
    "control": "A scripted day with the setting changed sends different device commands.",
    "published": "A scripted day with the setting changed publishes different values. No device command changes.",
    "exempt": "Not run on the scripted day.",
}


def render_feature_matrix() -> str:
    """The feature matrix page, built from the audit table above, the form sections and the sensor lists."""
    import json

    from custom_components.hvac_horizon import config_flow
    from custom_components.hvac_horizon import sensor as sensor_module

    steps = json.loads((Path(config_flow.__file__).parent / "translations" / "en.json").read_text())["config"]["step"]
    section_of = {key: name for name, keys in config_flow.SECTIONS.items() for key in keys}
    section_of.update({"schedule_blocks": "schedule", "fleet": "fleet"})
    order = ["room", *config_flow.MENU_ORDER]
    lines = [
        "# Feature matrix",
        "",
        "<!-- Generated by tests/ha/test_settings_effect.py. Do not edit. Regenerate with:",
        "     HVAC_WRITE_MATRIX=1 python -m pytest tests/ha/test_settings_effect.py -k feature_matrix -->",
        "",
        "Each setting, the form step that holds it, and what the test suite proves about it. `tests/ha/test_settings_effect.py`",
        "runs a scripted day twice, once with the setting changed, and compares the device commands and the published values.",
        "A test fails when this page differs from that table, so the page matches the code of the same commit.",
        "",
        "| Level | Meaning | Settings |",
        "| :-- | :-- | --: |",
    ]
    for level, text in LEVEL_TEXT.items():
        lines.append(f"| `{level}` | {text} | {sum(1 for spec in SETTINGS.values() if spec['level'] == level)} |")
    lines += ["", "For what has run on real devices, see [What has run on real devices](concepts/limits.md#what-has-run-on-real-devices).", ""]
    for name in order:
        keys = sorted(key for key in SETTINGS if section_of.get(key) == name)
        if not keys:
            continue
        lines += [f"## {steps[name]['title']}", "", "| Setting | Level | Note |", "| :-- | :-- | :-- |"]
        for key in keys:
            spec = SETTINGS[key]
            lines.append(f"| `{key}` | `{spec['level']}` | {spec.get('why', '')} |")
        lines.append("")
    unplaced = sorted(key for key in SETTINGS if section_of.get(key) not in order)
    assert not unplaced, f"settings with no form step: {unplaced}"
    lines += [
        "## Published values",
        "",
        "`tests/test_published_keys.py` fails when the coordinator publishes a key that no sensor or attribute shows.",
        "",
        "| Status sensors | Count |",
        "| :-- | --: |",
        f"| All status keys | {len(sensor_module.STATUS_KEYS)} |",
        f"| Diagnostic, enabled | {len(sensor_module.DIAGNOSTIC_ENABLED_KEYS)} |",
        f"| Diagnostic, disabled by default | {len(sensor_module.DIAGNOSTIC_DISABLED_KEYS)} |",
        "",
    ]
    return "\n".join(lines)


def test_the_feature_matrix_page_matches_the_audit_table():
    """docs/feature-matrix.md is generated from SETTINGS. A setting added, moved or relabelled without regenerating fails here."""
    import os

    text = render_feature_matrix()
    if os.environ.get("HVAC_WRITE_MATRIX"):
        MATRIX_PATH.write_text(text)
    assert MATRIX_PATH.read_text() == text, "run HVAC_WRITE_MATRIX=1 python -m pytest tests/ha/test_settings_effect.py -k feature_matrix"
