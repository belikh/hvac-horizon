"""HVAC Horizon: learns how fast an AC cools a room and forecasts time and cost to the setpoint."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform
from homeassistant.util import dt as dt_util

from .const import DOMAIN
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

    from homeassistant.core import SupportsResponse

    hass.services.async_register(DOMAIN, SERVICE_RESET, reset)
    hass.services.async_register(DOMAIN, SERVICE_EXPORT, export, supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, SERVICE_AUTOTUNE, autotune)
    hass.services.async_register(DOMAIN, SERVICE_WHAT_IF, what_if, supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, SERVICE_EXPORT_CSV, export_csv, supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, SERVICE_LOCK, set_lock)
    hass.services.async_register(DOMAIN, SERVICE_TIMED, start_timed)
    hass.services.async_register(DOMAIN, SERVICE_RESPOND, respond)


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
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown_store()
    return unloaded
