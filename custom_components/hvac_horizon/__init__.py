"""HVAC Horizon: learns how fast an AC cools a room and forecasts time and cost to the setpoint."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform
from homeassistant.util import dt as dt_util

from .const import CONF_ECO_C, DEFAULT_COMFORT_C, DOMAIN
from .coordinator import HorizonCoordinator
from .forecast import Forecast, PriceSlot, forecast
from .model import Fit, NotEnoughData, Sample, fit

__all__ = ["Fit", "Forecast", "NotEnoughData", "PriceSlot", "Sample", "fit", "forecast"]

PLATFORMS = [Platform.SENSOR]


SERVICE_RESET = "reset_learning"
SERVICE_EXPORT = "export_samples"
SERVICE_AUTOTUNE = "start_autotune"
SERVICE_WHAT_IF = "what_if"
SERVICE_EXPORT_CSV = "export_csv"
SERVICE_LOCK = "set_lock"
SERVICE_TIMED = "start_timed_preset"
SERVICE_RESPOND = "respond_suggestion"
SERVICE_HOUSE_MODE = "set_house_mode"
SERVICE_INVESTIGATION_REPORT = "generate_investigation_report"


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_RESET):
        return

    def _coordinator(call):
        entry = hass.config_entries.async_get_entry(call.data["entry_id"])
        if entry is None or not hasattr(entry, "runtime_data"):
            raise ValueError("unknown HVAC Horizon entry")
        return entry.runtime_data

    async def reset(call):
        _coordinator(call).reset_learning()

    async def export(call):
        return {"samples": _coordinator(call).export_samples()}

    async def what_if(call):
        return _coordinator(call).what_if(
            float(call.data["room_c"]), float(call.data["target_c"]), float(call.data["outdoor_c"]), float(call.data["power_kw"])
        )

    async def export_csv(call):
        from .collector import entries_to_csv

        return {"csv": entries_to_csv(_coordinator(call)._entries)}

    async def autotune(call):
        _coordinator(call).start_autotune(float(call.data["setpoint"]))

    async def set_lock(call):
        changed = _coordinator(call).set_lock(bool(call.data["locked"]), call.data.get("pin"))
        if not changed:
            raise ValueError("lock not changed: wrong PIN, or no PIN configured")

    async def respond(call):
        _coordinator(call).respond_suggestion(bool(call.data["accept"]))

    async def start_timed(call):
        _coordinator(call).start_timed_preset(
            str(call.data["preset"]), float(call.data["minutes"]), dt_util.utcnow().timestamp() / 60.0
        )

    async def investigation_report(call):
        await _coordinator(call).async_generate_investigation_report()

    async def set_house_mode(call):
        mode = call.data["mode"]
        if mode not in ("home", "away"):
            raise ValueError("house mode must be home or away")
        hass.data.setdefault(DOMAIN, {})["house_mode"] = mode
        for entry in hass.config_entries.async_entries(DOMAIN):
            if hasattr(entry, "runtime_data"):
                await entry.runtime_data.async_request_refresh()

    from homeassistant.core import SupportsResponse

    hass.services.async_register(DOMAIN, SERVICE_RESET, reset)
    hass.services.async_register(DOMAIN, SERVICE_EXPORT, export, supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, SERVICE_AUTOTUNE, autotune)
    hass.services.async_register(DOMAIN, SERVICE_WHAT_IF, what_if, supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, SERVICE_EXPORT_CSV, export_csv, supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, SERVICE_LOCK, set_lock)
    hass.services.async_register(DOMAIN, SERVICE_TIMED, start_timed)
    hass.services.async_register(DOMAIN, SERVICE_RESPOND, respond)
    hass.services.async_register(DOMAIN, SERVICE_HOUSE_MODE, set_house_mode)
    hass.services.async_register(DOMAIN, SERVICE_INVESTIGATION_REPORT, investigation_report)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Version 1 stored eco = DEFAULT_COMFORT_C + 1.0 (25 C) as the form default, whatever the comfort was.

    Drop that value, so an empty house uses away_c. A user-set eco of 25 C is dropped too. The stored value cannot tell the two apart.
    """
    if entry.version == 1:
        data = dict(entry.data)
        eco = data.get(CONF_ECO_C)
        if eco is not None and abs(float(eco) - (DEFAULT_COMFORT_C + 1.0)) < 1e-6:
            del data[CONF_ECO_C]
        hass.config_entries.async_update_entry(entry, data=data, version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _register_services(hass)
    coordinator = HorizonCoordinator(hass, entry)
    await coordinator.async_load()
    await coordinator.async_backfill_from_recorder()
    coordinator.start_power_tracking()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Stop the refresh timer first, so no tick drives the heater while the entry unloads.
    await entry.runtime_data.async_shutdown()
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry.runtime_data.forget_cooling()
        entry.runtime_data.forget_peer_requests()
        await entry.runtime_data.async_heater_off()
        await entry.runtime_data.async_shutdown_store()
    return unloaded
