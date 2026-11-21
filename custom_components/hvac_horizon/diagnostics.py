"""Diagnostics export for HVAC Horizon. Entity IDs and the stored record are redacted."""

from __future__ import annotations

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
    "fleet", "device_id", "power_sensor",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data or {}
    return {
        "config": async_redact_data(dict(entry.data), TO_REDACT),
        "options": async_redact_data(dict(entry.options), TO_REDACT),
        "status": async_redact_data({k: v for k, v in data.items() if k not in ("reason",)}, TO_REDACT),
        "samples": len(coordinator._entries),
        "fit": None if coordinator._fit is None else {"k": coordinator._fit.k, "c": coordinator._fit.c, "g": coordinator._fit.g},
        "domain": DOMAIN,
    }
