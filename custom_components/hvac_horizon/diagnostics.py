"""Diagnostics export for HVAC Horizon. Entity IDs and the stored record are redacted."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

TO_REDACT = {"climate", "power", "room_sensors", "outdoor", "price", "humidity", "energy", "window", "presence", "co2", "pm25", "fan", "dehumidifier", "heater_switch", "schedule", "vacation", "irradiance"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data or {}
    return {
        "config": async_redact_data(dict(entry.data), TO_REDACT),
        "status": {k: v for k, v in data.items() if k not in ("reason",)},
        "samples": len(coordinator._entries),
        "fit": None if coordinator._fit is None else {"k": coordinator._fit.k, "c": coordinator._fit.c, "g": coordinator._fit.g},
        "domain": DOMAIN,
    }
