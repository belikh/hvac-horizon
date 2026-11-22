"""Helpers and constants shared by the coordinator and its mixins. The logger keeps the coordinator module name so logging configuration by module name keeps working."""

from __future__ import annotations

import math

import logging
from typing import Any
from datetime import datetime
from dataclasses import fields

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from custom_components.hvac_horizon.const import CONF_AREA, DOMAIN
from custom_components.hvac_horizon.room_model.collector import Entry


_LOGGER = logging.getLogger("custom_components.hvac_horizon.coordinator")

def _parse_deadline(raw: Any) -> tuple[float | None, str | None]:
    """Epoch seconds of a schedule next_event, and a problem text when the value cannot be read.

    Accepts a datetime, an ISO string or a timestamp in seconds. An empty, unknown or unavailable value is no deadline
    and no problem. A naive time is read in the Home Assistant time zone.
    """
    if raw is None or (isinstance(raw, str) and raw.strip().lower() in ("", "unknown", "unavailable", "none")):
        return None, None
    try:
        if isinstance(raw, datetime):
            when = raw
        elif isinstance(raw, bool):
            raise TypeError("bool")
        elif isinstance(raw, (int, float)):
            return float(raw), None
        elif isinstance(raw, str):
            try:
                return float(raw), None
            except ValueError:
                when = datetime.fromisoformat(raw.strip())
        else:
            raise TypeError(type(raw).__name__)
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt_util.get_default_time_zone())
        return when.timestamp(), None
    except (TypeError, ValueError, OverflowError, OSError):
        return None, f"next_event {raw!r} is not a datetime, ISO string or timestamp"

_ENTRY_FIELDS = {f.name for f in fields(Entry)}

def _round_or_none(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)

def _float(state: State | None) -> float | None:
    if state is None or state.state in ("unknown", "unavailable", ""):
        return None
    try:
        return float(state.state)
    except ValueError:
        return None

def _optional_float(value: Any) -> float | None:
    """A stored number, or None when the key is absent or empty."""
    return None if value is None or value == "" else float(value)

def _entry_from_dict(raw: dict[str, Any]) -> Entry:
    """Load a stored record. Unknown keys are dropped, missing optional keys take their defaults."""
    clean = {k: v for k, v in raw.items() if k in _ENTRY_FIELDS}
    if "rooms_c" in clean:
        clean["rooms_c"] = tuple(clean["rooms_c"])
    return Entry(**clean)


def _entries_from_store(raw: Any) -> list[Entry]:
    """The stored readings. A record that is not a dict, or lacks a number for a required field, is left out."""
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or any(_stored_number(item.get(key)) is None for key in ("t", "room_c", "outdoor_c", "power_w")):
            continue
        try:
            out.append(_entry_from_dict(item))
        except (TypeError, ValueError):
            continue
    return out


def _stored_number(value: Any) -> float | None:
    """A finite number from the store, or None for any other value."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _stored_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}

NO_AC = "climate.hvac_horizon_no_ac"  # stands for the first AC in a room that has none; the entity never exists

MPC_STEP_MIN = 15.0
OUTDOOR_AHEAD_H = (1, 3, 6)  # hours ahead published as outdoor_forecast_next_c

MPC_STEPS = 16  # 4 hours

# The heat plan's energy cost is multiplied by this. At 1.0, one 15 min step at $0.18/kWh costs about 0.05 units, and
# one K of discomfort costs 1 unit per step, so price rarely changes step 0. At 50, a $0.28/kWh step costs about 3.9 units.
# Picked by a sweep of 1 to 119 on one room at 23.4 C. It is not calibrated against metered data.
MPC_HEAT_COST_WEIGHT = 50.0

USER_MODES = ("cool", "heat", "auto", "dry", "fan_only")
PRECOOL_LATE_MAX_MIN = 120.0  # a pre-cool with no plan that meets the event starts at once when the event is this close
AUTO_CHANGEOVER_MIN = 60.0  # in auto, minutes after the heater ran before the AC may cool
AUTO_CHANGEOVER_OVERRIDE_K = 2.0  # a room this far above the cool threshold cools at once
DRY_FLOOR_BELOW_HEAT_K = 1.0  # dry mode pauses the AC this far below the heat threshold, and resumes at the threshold  # the modes the room climate entity offers besides off
SAFETY_FALLBACK_PCT = 0.0  # heater output when a sensor stalls; safety_output applies it

class _HorizonStore(Store[dict[str, Any]]):
    """Store that accepts older payloads. Keys added after version 2 are optional, so older data loads as it is.

    Before version 4 `user_mode` was stored ("cool" by default) and control ignored it, so every room both cooled and
    heated. An older payload drops the key, which loads as "auto" and keeps that behaviour.
    """

    async def _async_migrate_func(self, old_major_version: int, old_minor_version: int, old_data: dict[str, Any]) -> dict[str, Any]:
        if old_major_version < 4 and isinstance(old_data, dict):
            old_data = {key: value for key, value in old_data.items() if key != "user_mode"}
        return old_data


def room_device_info(hass: HomeAssistant, entry: ConfigEntry) -> DeviceInfo:
    """The device of a room entry. Its suggested area is the entry's area, which Home Assistant applies when it creates the device."""
    area = ar.async_get(hass).async_get_area(entry.data[CONF_AREA]) if entry.data.get(CONF_AREA) else None
    info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name=f"HVAC Horizon {entry.title}", manufacturer="HVAC Horizon", model="Room")
    if area is not None:
        info["suggested_area"] = area.name
    return info
