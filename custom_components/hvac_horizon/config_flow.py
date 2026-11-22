"""Config flow in steps. The first step takes the AC, its power meter and the room and outdoor sensors.
A menu then offers to finish with control off, or to open any of the optional sections. Each section is one
form and can be submitted empty. Reconfigure opens the same menu over the stored settings, so it edits the
same fields without deleting samples."""

from __future__ import annotations

import dataclasses
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import SOURCE_RECONFIGURE, ConfigFlow, ConfigFlowResult
from homeassistant.helpers import selector

from .engine.fleet import Device
from .const import (
    DEFAULT_PRICE_CHEAP,
    DEFAULT_PRICE_EXPENSIVE,
    CONF_AWAY_DELAY_MIN,
    CONF_COIL_DRY,
    CONF_DRY_FOR_HUMIDITY,
    CONF_AC_HEAT,
    CONF_AC_RATED_W,
    CONF_MPC_HEAT_PLAN,
    CONF_AUTO_FAN,
    CONF_SETPOINT_REGULATION,
    CONF_HEATER_ALGORITHM,
    CONF_HEATER_KD,
    CONF_HEATER_KI,
    CONF_TPI_COEF_EXT,
    CONF_TPI_THRESHOLD_HIGH,
    CONF_TPI_THRESHOLD_LOW,
    CONF_MAX_ON_PERCENT,
    CONF_NIGHT_PLAN_ACT,
    CONF_ACCURACY_PANEL,
    CONF_ECONOMIZER_FAN,
    CONF_WINDOW_ACTUATOR,
    CONF_PEER_ENTRY,
    CONF_PEER_SETPOINT_MODE,
    CONF_TPI_COEF_INT,
    DEFAULT_TPI_COEF_EXT,
    DEFAULT_TPI_COEF_INT,
    HEATER_ALGORITHM_PID,
    HEATER_ALGORITHM_TPI,
    CONF_HEATER_KI,
    CONF_HEATER_KP,
    CONF_HEATER_POWER_W,
    CONF_HEATER_SWITCH,
    CONF_RADIATORS,
    CONF_HOUSE_SEALED,
    CONF_LOCK_PIN,
    CONF_MIN_OFF_MIN,
    CONF_MIN_ON_MIN,
    CONF_PRICE_CHEAP,
    CONF_PRICE_EXPENSIVE,
    CONF_VACATION,
    CONF_VACATION_C,
    CONF_WHOLE_HOUSE_FAN,
    CONF_BOOST_C,
    CONF_HEATER_INVERTED,
    CONF_OUTPUT_MIN,
    CONF_OUTPUT_MAX,
    CONF_OUTPUT_MAX_ENTITY,
    CONF_OUTPUT_MIN_ENTITY,
    CONF_COVER,
    CONF_MOTION,
    CONF_MOTION_AWAY_MIN,
    DEFAULT_MOTION_AWAY_MIN,
    CONF_COMPRESSOR_GROUP,
    CONF_SHED_LOADS,
    CONF_SHED_MAX_W,
    CONF_STALL_WINDOW_S,
    CONF_WHOLE_HOUSE_POWER,
    CONF_SCHEDULE_BLOCKS,
    CONF_FLEET,
    CONF_DISPATCH_MODE,
    CONF_AI_CONVERSATION,
    CONF_AI_BUDGET,
    CONF_ECO_C,
    CONF_HEAT_BOOST_C,
    CONF_HEAT_ECO_C,
    CONF_COMFORT_ENERGY_WEIGHT,
    CONF_BOILER,
    CONF_DEHUMIDIFIER,
    CONF_HUMIDIFIER,
    CONF_FAN,
    CONF_CO2,
    CONF_PM25,
    CONF_SCHEDULE,
    CONF_AWAY_C,
    CONF_COMFORT_C,
    CONF_CONTROL,
    CONF_VALVE,
    CONF_TRV_TEMPERATURE,
    CONF_PRESENCE,
    CONF_WINDOW,
    CONF_WINDOW_AZIMUTH,
    CONF_WINDOW_AREA_M2,
    CONF_HEATWAVE_WARNING,
    CONF_IRRADIANCE,
    CONF_WINDOW_DELAY_MIN,
    DEFAULT_AWAY_C,
    DEFAULT_COMFORT_C,
    DEFAULT_WINDOW_AZIMUTH,
    DEFAULT_WINDOW_DELAY_MIN,
    CONF_CLIMATE,
    CONF_CLIMATE_2,
    CONF_POWER_2,
    CONF_ENERGY,
    CONF_HUMIDITY,
    CONF_ROOM_HUMIDITY,
    CONF_OUTDOOR,
    CONF_POWER,
    CONF_PRICE,
    CONF_ROOM_SENSORS,
    CONF_WEATHER,
    DOMAIN,
)


MISSING = object()  # marks a key() call with no schema default
SCHEDULE_PRESETS = ("comfort", "eco", "boost", "away")


FLEET_KEYS = frozenset(f.name for f in dataclasses.fields(Device))


def _number_ok(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and 0 <= value <= 10


def _price_errors(data: dict[str, Any]) -> dict[str, str]:
    """Return a form error for each invalid price threshold.

    Each threshold is a number from 0 to 10. When both are set, price_cheap must be below price_expensive.
    """
    errors: dict[str, str] = {}
    for name in (CONF_PRICE_CHEAP, CONF_PRICE_EXPENSIVE):
        if data.get(name) is not None and not _number_ok(data[name]):
            errors[name] = "invalid_price"
    cheap = data.get(CONF_PRICE_CHEAP, DEFAULT_PRICE_CHEAP) if data.get(CONF_PRICE_CHEAP) is not None else DEFAULT_PRICE_CHEAP
    expensive = (
        data.get(CONF_PRICE_EXPENSIVE) if data.get(CONF_PRICE_EXPENSIVE) is not None else DEFAULT_PRICE_EXPENSIVE
    )
    if not errors and cheap >= expensive:
        errors[CONF_PRICE_CHEAP] = "price_order"
    return errors


def _fleet_problem(value: Any) -> str | None:
    """Return why a room device list is invalid, or None when it is valid.

    Each device needs a unique device_id, a kind of cool or heat and rated_w. An entity_id, when set, is a climate entity or a heat switch.
    """
    if not isinstance(value, list):
        return "fleet must be a list"
    ids: set[str] = set()
    for device in value:
        if not isinstance(device, dict) or "device_id" not in device or "kind" not in device:
            return f"each device needs device_id and kind: {device}"
        if device["device_id"] in ids:
            return f"device_id must be unique: {device['device_id']}"
        ids.add(device["device_id"])
        if device["kind"] not in ("cool", "heat"):
            return f"kind must be cool or heat: {device['kind']}"
        if not device.get("rated_w"):
            return f"a device needs rated_w, the watts it draws while running: {device['device_id']}"
        unknown = sorted(set(device) - FLEET_KEYS)
        if unknown:
            return f"unknown key {', '.join(repr(k) for k in unknown)} in device {device['device_id']}; allowed keys: {', '.join(sorted(FLEET_KEYS))}"
        entity = device.get("entity_id")
        if entity is not None:
            domain = str(entity).split(".", 1)[0]
            if domain not in ("climate", "switch") or (domain == "switch" and device["kind"] == "cool"):
                return f"entity_id must be a climate entity, or a switch for a heat device: {device['device_id']}"
    return None


def _schedule_block_problem(value: Any) -> str | None:
    """Return why a schedule block list is invalid, or None when it is valid.

    The list holds {start_h, end_h, preset} dicts. Hours run from 0 to 24. A block with end_h below
    start_h crosses midnight.
    """
    if not isinstance(value, list):
        return "schedule blocks must be a list"
    for block in value:
        if not isinstance(block, dict) or set(block) != {"start_h", "end_h", "preset"}:
            return f"each block needs exactly start_h, end_h and preset: {block}"
        for hour in ("start_h", "end_h"):
            h = block[hour]
            if isinstance(h, bool) or not isinstance(h, (int, float)) or not 0 <= h <= 24:
                return f"{hour} must be a number from 0 to 24: {h}"
        if block["start_h"] == block["end_h"]:
            return f"start_h and end_h must differ: {block}"
        if block["preset"] not in SCHEDULE_PRESETS:
            return f"preset must be one of {', '.join(SCHEDULE_PRESETS)}: {block['preset']}"
    return None


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    def key(name: str, required: bool = False, default: Any = MISSING, secret: bool = False):
        marker = vol.Required if required else vol.Optional
        kwargs: dict[str, Any] = {}
        if name in defaults and defaults[name] is not None and not secret:
            kwargs["description"] = {"suggested_value": defaults[name]}
        if default is MISSING:
            return marker(name, **kwargs)
        return marker(name, default=default, **kwargs)

    return vol.Schema(
        {
            key(CONF_CLIMATE): selector.EntitySelector(selector.EntitySelectorConfig(domain="climate")),
            key(CONF_POWER): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="power")
            ),
            key(CONF_AC_RATED_W): selector.NumberSelector(
                selector.NumberSelectorConfig(min=50, max=10000, step=10, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="W")
            ),
            key(CONF_ROOM_SENSORS, True): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="temperature", multiple=True)
            ),
            key(CONF_OUTDOOR, True): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
            ),
            key(CONF_CLIMATE_2): selector.EntitySelector(selector.EntitySelectorConfig(domain="climate")),
            key(CONF_POWER_2): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="power")
            ),
            key(CONF_WEATHER): selector.EntitySelector(selector.EntitySelectorConfig(domain="weather")),
            key(CONF_HEATWAVE_WARNING): selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor")),
            key(CONF_PRICE): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            key(CONF_HUMIDITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="humidity")
            ),
            key(CONF_ROOM_HUMIDITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="humidity")
            ),
            key(CONF_ENERGY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="energy")
            ),
            key(CONF_COVER): selector.EntitySelector(selector.EntitySelectorConfig(domain="cover")),
            key(CONF_WINDOW): selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor")),
            key(CONF_CO2): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="carbon_dioxide")
            ),
            key(CONF_FAN): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
            key(CONF_DEHUMIDIFIER): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
            key(CONF_BOILER): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
            key(CONF_HUMIDIFIER): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
            key(CONF_PM25): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="pm25")
            ),
            key(CONF_SCHEDULE): selector.EntitySelector(selector.EntitySelectorConfig(domain=["schedule", "sensor", "input_datetime"])),
            key(CONF_MOTION): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor", device_class="motion")
            ),
            key(CONF_PRESENCE): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["binary_sensor", "device_tracker", "person"], multiple=True)
            ),
            key(CONF_VALVE): selector.EntitySelector(selector.EntitySelectorConfig(domain="number")),
            key(CONF_TRV_TEMPERATURE): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
            ),
            key(CONF_WHOLE_HOUSE_POWER): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="power")
            ),
            key(CONF_VACATION): selector.EntitySelector(selector.EntitySelectorConfig(domain=["input_boolean", "switch"])),
            key(CONF_VACATION_C): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=30, step=0.5, unit_of_measurement="°C")
            ),
            key(CONF_HEATER_SWITCH): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
            key(CONF_RADIATORS, default=defaults.get(CONF_RADIATORS, False)): selector.BooleanSelector(),
            key(CONF_HEATER_POWER_W): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=10000, step=10, unit_of_measurement="W")
            ),
            key(CONF_HEATER_KP): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=200, step=0.5)),
            key(CONF_HEATER_KI): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=500, step=1)),
            key(CONF_HEATER_KD): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=50, step=0.1)),
            key(CONF_HEATER_ALGORITHM, default=defaults.get(CONF_HEATER_ALGORITHM, HEATER_ALGORITHM_PID)): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[HEATER_ALGORITHM_PID, HEATER_ALGORITHM_TPI],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_HEATER_ALGORITHM,
                )
            ),
            key(CONF_TPI_COEF_INT, default=defaults.get(CONF_TPI_COEF_INT, DEFAULT_TPI_COEF_INT)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=5, step=0.01, mode="box")
            ),
            key(CONF_TPI_COEF_EXT, default=defaults.get(CONF_TPI_COEF_EXT, DEFAULT_TPI_COEF_EXT)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=1, step=0.001, mode="box")
            ),
            key(CONF_TPI_THRESHOLD_HIGH): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=5, step=0.1, unit_of_measurement="°C")
            ),
            key(CONF_TPI_THRESHOLD_LOW): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=5, step=0.1, unit_of_measurement="°C")
            ),
            key(CONF_MAX_ON_PERCENT): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=100, step=1, unit_of_measurement="%")
            ),
            key(CONF_WHOLE_HOUSE_FAN): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
            key(CONF_LOCK_PIN, secret=True): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
            key(CONF_COIL_DRY, default=defaults.get(CONF_COIL_DRY, False)): selector.BooleanSelector(),
            key(CONF_DRY_FOR_HUMIDITY, default=defaults.get(CONF_DRY_FOR_HUMIDITY, False)): selector.BooleanSelector(),
            key(CONF_AC_HEAT, default=defaults.get(CONF_AC_HEAT, False)): selector.BooleanSelector(),
            key(CONF_MPC_HEAT_PLAN, default=defaults.get(CONF_MPC_HEAT_PLAN, False)): selector.BooleanSelector(),
            key(CONF_NIGHT_PLAN_ACT, default=defaults.get(CONF_NIGHT_PLAN_ACT, False)): selector.BooleanSelector(),
            key(CONF_ECONOMIZER_FAN, default=defaults.get(CONF_ECONOMIZER_FAN, False)): selector.BooleanSelector(),
            key(CONF_ACCURACY_PANEL, default=defaults.get(CONF_ACCURACY_PANEL, False)): selector.BooleanSelector(),
            key(CONF_WINDOW_ACTUATOR): selector.EntitySelector(selector.EntitySelectorConfig(domain=["cover", "switch"])),
            key(CONF_PEER_ENTRY): selector.TextSelector(),
            key(CONF_PEER_SETPOINT_MODE, default=defaults.get(CONF_PEER_SETPOINT_MODE, False)): selector.BooleanSelector(),
            key(CONF_AUTO_FAN, default=defaults.get(CONF_AUTO_FAN, False)): selector.BooleanSelector(),
            key(CONF_SETPOINT_REGULATION, default=defaults.get(CONF_SETPOINT_REGULATION, True)): selector.BooleanSelector(),
            key(CONF_HOUSE_SEALED, default=defaults.get(CONF_HOUSE_SEALED, False)): selector.BooleanSelector(),
            key(CONF_PRICE_CHEAP): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=10, step=0.01, mode="box")),
            key(CONF_PRICE_EXPENSIVE): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=10, step=0.01, mode="box")),
            key(CONF_MIN_ON_MIN): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=120, step=1, unit_of_measurement="min")
            ),
            key(CONF_MIN_OFF_MIN): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=120, step=1, unit_of_measurement="min")
            ),
            key(CONF_AWAY_DELAY_MIN): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=240, step=1, unit_of_measurement="min")
            ),
            key(CONF_MOTION_AWAY_MIN, default=defaults.get(CONF_MOTION_AWAY_MIN, DEFAULT_MOTION_AWAY_MIN)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=240, step=1, unit_of_measurement="min")
            ),
            key(CONF_STALL_WINDOW_S, default=defaults.get(CONF_STALL_WINDOW_S, 900.0)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=300, max=3600, step=30, unit_of_measurement="s")
            ),
            key(CONF_SHED_MAX_W): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=50000, step=50, unit_of_measurement="W")
            ),
            key(CONF_SHED_LOADS): selector.TextSelector(selector.TextSelectorConfig(multiple=True)),
            key(CONF_COMPRESSOR_GROUP): selector.TextSelector(),
            key(CONF_COMFORT_C, default=defaults.get(CONF_COMFORT_C, DEFAULT_COMFORT_C)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=16, max=30, step=0.5, unit_of_measurement="°C")
            ),
            key(CONF_ECO_C): selector.NumberSelector(
                selector.NumberSelectorConfig(min=16, max=30, step=0.5, unit_of_measurement="°C")
            ),
            key(CONF_COMFORT_ENERGY_WEIGHT): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=1, step=0.05, mode="slider")
            ),
            key(CONF_HEAT_ECO_C): selector.NumberSelector(
                selector.NumberSelectorConfig(min=10, max=30, step=0.5, unit_of_measurement="°C")
            ),
            key(CONF_HEAT_BOOST_C): selector.NumberSelector(
                selector.NumberSelectorConfig(min=10, max=30, step=0.5, unit_of_measurement="°C")
            ),
            key(CONF_BOOST_C, default=defaults.get(CONF_BOOST_C, DEFAULT_COMFORT_C - 1.0)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=16, max=30, step=0.5, unit_of_measurement="°C")
            ),
            key(CONF_AWAY_C, default=defaults.get(CONF_AWAY_C, DEFAULT_AWAY_C)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=16, max=32, step=0.5, unit_of_measurement="°C")
            ),
            key(CONF_WINDOW_DELAY_MIN, default=defaults.get(CONF_WINDOW_DELAY_MIN, DEFAULT_WINDOW_DELAY_MIN)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=120, step=1, unit_of_measurement="min")
            ),
            key(CONF_WINDOW_AZIMUTH, default=defaults.get(CONF_WINDOW_AZIMUTH, DEFAULT_WINDOW_AZIMUTH)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=360, step=1, unit_of_measurement="°")
            ),
            key(CONF_WINDOW_AREA_M2): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=50, step=0.1, unit_of_measurement="m²")
            ),
            key(CONF_IRRADIANCE): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="irradiance")
            ),
            key(CONF_CONTROL, default=defaults.get(CONF_CONTROL, False)): selector.BooleanSelector(),
            key(CONF_HEATER_INVERTED, default=bool(defaults.get(CONF_HEATER_INVERTED, False))): selector.BooleanSelector(),
            key(CONF_OUTPUT_MIN): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=100, step=1, unit_of_measurement="%")
            ),
            key(CONF_OUTPUT_MAX): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=100, step=1, unit_of_measurement="%")
            ),
            key(CONF_OUTPUT_MIN_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(domain="number")),
            key(CONF_OUTPUT_MAX_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(domain="number")),
            key(CONF_DISPATCH_MODE, default="fast"): selector.SelectSelector(
                selector.SelectSelectorConfig(options=["fast", "economy"], mode=selector.SelectSelectorMode.DROPDOWN)
            ),
            key(CONF_AI_CONVERSATION): selector.EntitySelector(selector.EntitySelectorConfig(domain="conversation")),
            key(CONF_AI_BUDGET): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=100, step=1)),
        }
    )


# The form is split into sections. Each section is one step. SECTIONS lists the fields of each one, and every
# field of the full schema is in exactly one section (a test checks this). "room" holds the required fields.
SECTIONS: dict[str, tuple[str, ...]] = {
    "room": (CONF_CLIMATE, CONF_POWER, CONF_AC_RATED_W, CONF_HEATER_SWITCH, CONF_ROOM_SENSORS, CONF_OUTDOOR),
    "comfort": (
        CONF_COMFORT_C, CONF_ECO_C, CONF_BOOST_C, CONF_AWAY_C, CONF_HEAT_ECO_C, CONF_HEAT_BOOST_C,
        CONF_COMFORT_ENERGY_WEIGHT, CONF_SCHEDULE, CONF_VACATION, CONF_VACATION_C,
        CONF_AWAY_DELAY_MIN,
    ),
    "price": (CONF_PRICE, CONF_PRICE_CHEAP, CONF_PRICE_EXPENSIVE, CONF_ENERGY),
    "heater": (
        CONF_HEATER_INVERTED, CONF_HEATER_POWER_W, CONF_RADIATORS, CONF_HEATER_ALGORITHM, 
        
        
        CONF_VALVE, CONF_TRV_TEMPERATURE, CONF_BOILER, CONF_AC_HEAT, CONF_MPC_HEAT_PLAN,
    ),
    "occupancy": (
        CONF_PRESENCE, CONF_MOTION, CONF_MOTION_AWAY_MIN, CONF_WINDOW, CONF_WINDOW_DELAY_MIN, CONF_WINDOW_ACTUATOR,
        CONF_COVER, CONF_WINDOW_AZIMUTH, CONF_WINDOW_AREA_M2, CONF_IRRADIANCE, CONF_WEATHER,
        CONF_HEATWAVE_WARNING,
    ),
    "air_quality": (
        CONF_HUMIDITY, CONF_ROOM_HUMIDITY, CONF_CO2, CONF_PM25, CONF_FAN, CONF_AUTO_FAN, CONF_DEHUMIDIFIER,
        CONF_HUMIDIFIER, CONF_COIL_DRY, CONF_DRY_FOR_HUMIDITY, CONF_HOUSE_SEALED, CONF_ECONOMIZER_FAN,
        CONF_WHOLE_HOUSE_FAN,
    ),
    "devices": (
        CONF_CLIMATE_2, CONF_POWER_2, CONF_DISPATCH_MODE, CONF_WHOLE_HOUSE_POWER, CONF_SHED_MAX_W,
        CONF_SHED_LOADS, CONF_COMPRESSOR_GROUP, 
    ),
    "advanced": (
        CONF_CONTROL, CONF_LOCK_PIN, CONF_NIGHT_PLAN_ACT, CONF_ACCURACY_PANEL, CONF_PEER_ENTRY,
        CONF_PEER_SETPOINT_MODE, CONF_AI_CONVERSATION, CONF_AI_BUDGET, 
    ),
    "expert": (
        CONF_HEATER_KP, CONF_HEATER_KI, CONF_HEATER_KD, CONF_TPI_COEF_INT, CONF_TPI_COEF_EXT, CONF_TPI_THRESHOLD_HIGH,
        CONF_TPI_THRESHOLD_LOW, CONF_MAX_ON_PERCENT, CONF_OUTPUT_MIN, CONF_OUTPUT_MAX, CONF_OUTPUT_MIN_ENTITY, CONF_OUTPUT_MAX_ENTITY,
        CONF_MIN_ON_MIN, CONF_MIN_OFF_MIN, CONF_STALL_WINDOW_S, CONF_SETPOINT_REGULATION,
    ),
}
OPTIONAL_SECTIONS = [name for name in SECTIONS if name != "room"]


def _section_schema(section: str, defaults: dict[str, Any]) -> vol.Schema:
    """The form for one section, with suggested values from defaults."""
    keys = SECTIONS[section]
    return vol.Schema({k: v for k, v in _schema(defaults).schema.items() if str(k) in keys})


def _section_problems(section: str, data: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """Return the form errors and description placeholders for a submitted section."""
    errors: dict[str, str] = {}
    placeholders: dict[str, str] = {}
    if section == "room":
        # The room needs a device: an AC or a heater. An AC needs its power, measured or rated.
        if not data.get(CONF_CLIMATE) and not data.get(CONF_HEATER_SWITCH):
            errors["base"] = "need_device"
        elif data.get(CONF_CLIMATE) and not data.get(CONF_POWER) and not data.get(CONF_AC_RATED_W):
            errors[CONF_POWER] = "need_power"
    if section == "price":
        errors.update(_price_errors(data))
    return errors, placeholders


# Field names in the form. A reconfigure submit that omits one of these clears it, except the lock PIN.
FORM_FIELDS = frozenset(str(k) for k in _schema({}).schema)

# Two settings are lists. Each has its own step, which adds one item per submit and removes the ticked ones.
LIST_STEPS = ("schedule", "fleet")
MENU_ORDER = ["comfort", "schedule", "price", "heater", "occupancy", "air_quality", "devices", "fleet", "advanced", "expert"]


def _hour(value: Any) -> float | None:
    """Hours from a time selector value such as "06:30:00", or None when the field is empty."""
    if not value:
        return None
    parts = str(value).split(":")
    return int(parts[0]) + (int(parts[1]) if len(parts) > 1 else 0) / 60.0


def _clock(hour: float) -> str:
    return f"{int(hour) % 24:02d}:{round((hour % 1) * 60):02d}"


def _block_label(block: dict[str, Any]) -> str:
    return f"{_clock(block['start_h'])} to {_clock(block['end_h'])}: {block['preset']}"


def _remove_selector(labels: list[str]) -> Any:
    return selector.SelectSelector(selector.SelectSelectorConfig(options=labels, multiple=True, mode=selector.SelectSelectorMode.LIST))


def _schedule_schema(blocks: list[dict[str, Any]]) -> vol.Schema:
    fields: dict[Any, Any] = {}
    if blocks:
        fields[vol.Optional("remove", default=[])] = _remove_selector([_block_label(b) for b in blocks])
    fields[vol.Optional("start")] = selector.TimeSelector()
    fields[vol.Optional("end")] = selector.TimeSelector()
    fields[vol.Optional("preset", default="eco")] = selector.SelectSelector(
        selector.SelectSelectorConfig(options=list(SCHEDULE_PRESETS), mode=selector.SelectSelectorMode.DROPDOWN)
    )
    fields[vol.Optional("add_another", default=False)] = selector.BooleanSelector()
    return vol.Schema(fields)


def _fleet_schema(devices: list[dict[str, Any]]) -> vol.Schema:
    fields: dict[Any, Any] = {}
    if devices:
        fields[vol.Optional("remove", default=[])] = _remove_selector([str(d["device_id"]) for d in devices])
    fields[vol.Optional("device_id")] = selector.TextSelector()
    fields[vol.Optional("kind", default="cool")] = selector.SelectSelector(
        selector.SelectSelectorConfig(options=["cool", "heat"], mode=selector.SelectSelectorMode.DROPDOWN)
    )
    fields[vol.Optional("entity_id")] = selector.EntitySelector(selector.EntitySelectorConfig(domain=["climate", "switch"]))
    fields[vol.Optional("rated_w")] = selector.NumberSelector(
        selector.NumberSelectorConfig(min=10, max=20000, step=10, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="W")
    )
    fields[vol.Optional("power_sensor")] = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="power"))
    fields[vol.Optional("rate_k_per_h_per_kw")] = selector.NumberSelector(
        selector.NumberSelectorConfig(min=0.05, max=20, step=0.05, mode=selector.NumberSelectorMode.BOX)
    )
    fields[vol.Optional("add_another", default=False)] = selector.BooleanSelector()
    return vol.Schema(fields)


class HorizonConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._stored_pin: str | None = None  # the PIN in the entry being reconfigured

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self.async_step_room()

    async def async_step_reconfigure(self, user_input: dict | None = None) -> ConfigFlowResult:
        if not self._data:
            entry = self._get_reconfigure_entry()
            self._data = dict(entry.data)
            self._stored_pin = entry.data.get(CONF_LOCK_PIN)
        return self._show_menu()

    def _show_menu(self) -> ConfigFlowResult:
        reconfiguring = self.source == SOURCE_RECONFIGURE
        options = (["room"] if reconfiguring else []) + MENU_ORDER
        return self.async_show_menu(step_id="reconfigure" if reconfiguring else "menu", menu_options=["finish"] + options)

    async def async_step_menu(self, user_input: dict | None = None) -> ConfigFlowResult:
        return self._show_menu()

    async def _async_section(self, section: str, user_input: dict | None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id=section, data_schema=_section_schema(section, self._data))
        errors, placeholders = _section_problems(section, user_input)
        if errors:
            return self.async_show_form(
                step_id=section,
                data_schema=_section_schema(section, user_input),
                errors=errors,
                description_placeholders=placeholders or None,
            )
        # A field left out of the submit is cleared, so reconfigure can remove a setting. A blank PIN box
        # keeps the stored PIN.
        for name in SECTIONS[section]:
            self._data.pop(name, None)
        if CONF_LOCK_PIN in SECTIONS[section] and not user_input.get(CONF_LOCK_PIN) and self._stored_pin:
            user_input = {**user_input, CONF_LOCK_PIN: self._stored_pin}
        self._data.update(user_input)
        return self._show_menu()

    async def async_step_room(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None and self.source != SOURCE_RECONFIGURE:
            await self.async_set_unique_id(user_input.get(CONF_CLIMATE) or user_input.get(CONF_HEATER_SWITCH))
            self._abort_if_unique_id_configured()
        return await self._async_section("room", user_input)

    async def async_step_comfort(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self._async_section("comfort", user_input)

    async def async_step_price(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self._async_section("price", user_input)

    async def async_step_heater(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self._async_section("heater", user_input)

    async def async_step_occupancy(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self._async_section("occupancy", user_input)

    async def async_step_air_quality(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self._async_section("air_quality", user_input)

    async def async_step_devices(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self._async_section("devices", user_input)

    async def async_step_advanced(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self._async_section("advanced", user_input)

    async def async_step_expert(self, user_input: dict | None = None) -> ConfigFlowResult:
        return await self._async_section("expert", user_input)

    async def async_step_schedule(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Schedule blocks: tick the blocks to remove, and fill start, end and preset to add one."""
        blocks = list(self._data.get(CONF_SCHEDULE_BLOCKS) or [])
        if user_input is None:
            return self.async_show_form(step_id="schedule", data_schema=_schedule_schema(blocks))
        blocks = [b for b in blocks if _block_label(b) not in user_input.get("remove", [])]
        start, end = _hour(user_input.get("start")), _hour(user_input.get("end"))
        errors: dict[str, str] = {}
        if (start is None) != (end is None):
            errors["base"] = "schedule_needs_both"
        elif start is not None and start == end:
            errors["base"] = "schedule_same_time"
        if errors:
            return self.async_show_form(step_id="schedule", data_schema=_schedule_schema(blocks), errors=errors)
        if start is not None:
            blocks.append({"start_h": start, "end_h": end, "preset": user_input.get("preset", "eco")})
        self._data[CONF_SCHEDULE_BLOCKS] = blocks
        if user_input.get("add_another"):
            return self.async_show_form(step_id="schedule", data_schema=_schedule_schema(blocks))
        return self._show_menu()

    async def async_step_fleet(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Room devices: tick the devices to remove, and fill the fields to add one."""
        devices = list(self._data.get(CONF_FLEET) or [])
        if user_input is None:
            return self.async_show_form(step_id="fleet", data_schema=_fleet_schema(devices))
        kept = [d for d in devices if str(d["device_id"]) not in user_input.get("remove", [])]
        device = {k: user_input[k] for k in ("device_id", "kind", "entity_id", "rated_w", "power_sensor", "rate_k_per_h_per_kw") if user_input.get(k) not in (None, "")}
        adding = any(k in device for k in ("device_id", "entity_id", "rated_w"))
        if adding:
            kept.append(device)
            if problem := _fleet_problem(kept):
                return self.async_show_form(
                    step_id="fleet", data_schema=_fleet_schema(devices), errors={"base": "invalid_fleet"}, description_placeholders={"fleet_problem": problem}
                )
        self._data[CONF_FLEET] = kept
        if user_input.get("add_another"):
            return self.async_show_form(step_id="fleet", data_schema=_fleet_schema(kept))
        return self._show_menu()

    async def async_step_finish(self, user_input: dict | None = None) -> ConfigFlowResult:
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_reload_and_abort(self._get_reconfigure_entry(), data=dict(self._data))
        data = {CONF_CONTROL: False, **self._data}  # finishing early leaves the room in observe-only mode
        device_id = data.get(CONF_CLIMATE) or data[CONF_HEATER_SWITCH]
        device = self.hass.states.get(device_id)
        title = device.attributes.get("friendly_name", device_id) if device else device_id
        return self.async_create_entry(title=title, data=data)
