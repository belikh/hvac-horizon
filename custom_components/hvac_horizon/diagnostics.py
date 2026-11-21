"""Diagnostics export for HVAC Horizon. Entity IDs and the lock PIN are redacted. The entry title and unique id are left out."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_LOCK_PIN, DOMAIN

# Redacted by key name, at any depth in dicts and lists. The key stays in the export and the value
# becomes "**REDACTED**". Covered: the lock PIN (a secret) and every config key that holds an entity id,
# device id, entry reference or entity list, because the export is shared in public issues. Numeric
# settings and flags stay readable. Applied to the config entry data, its options and the coordinator
# result, so a key that appears in any of them is redacted the same way.
TO_REDACT = {
    CONF_LOCK_PIN,
    "climate", "climate_2", "power", "power_2", "room_sensors", "outdoor", "price", "humidity",
    "room_humidity", "energy", "window", "presence", "motion", "co2", "pm25", "fan", "dehumidifier",
    "humidifier", "boiler", "heater_switch", "schedule", "vacation", "irradiance", "weather", "valve",
    "trv_temperature", "ai_conversation", "output_min_entity", "output_max_entity", "cover",
    "whole_house_fan", "window_actuator", "whole_house_power", "peer_entry", "shed_loads",
    "fleet", "device_id", "power_sensor", "heatwave_warning", "compressor_group", "entity_id",
    # Result keys whose values are entity ids, device names or free text built from them.
    "sensors_stalled", "shed", "restored", "shed_skipped", "dispatch", "investigation", "investigation_ai",
    "investigation_report", "external_change",
}

# An entity id anywhere in a string value, for example inside an action text. Key-based redaction above cannot see these.
_ENTITY_ID = re.compile(
    r"\b(?:sensor|binary_sensor|climate|switch|cover|number|input_number|input_boolean|person|device_tracker|weather|"
    r"schedule|fan|light|select|conversation|zone|calendar|humidifier|valve)\.[a-z0-9_]+\b"
)


def _scrub(value: Any) -> Any:
    """Replace entity ids inside strings, at any depth. A dict key that is an entity id becomes an index."""
    if isinstance(value, str):
        return _ENTITY_ID.sub("**REDACTED**", value)
    if isinstance(value, dict):
        out = {}
        for index, (key, item) in enumerate(value.items()):
            safe = f"entity_{index}" if isinstance(key, str) and _ENTITY_ID.fullmatch(key) else key
            out[safe] = _scrub(item)
        return out
    if isinstance(value, (list, tuple)):
        return [_scrub(item) for item in value]
    return value


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data or {}
    return {
        "config": _scrub(async_redact_data(dict(entry.data), TO_REDACT)),
        "options": _scrub(async_redact_data(dict(entry.options), TO_REDACT)),
        "status": _scrub(async_redact_data({k: v for k, v in data.items() if k not in ("reason",)}, TO_REDACT)),
        "samples": len(coordinator._entries),
        "fit": None if coordinator._fit is None else {"k": coordinator._fit.k, "c": coordinator._fit.c, "g": coordinator._fit.g},
        "domain": DOMAIN,
    }
