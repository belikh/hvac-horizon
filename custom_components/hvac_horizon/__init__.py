"""HVAC Horizon: learns how fast an AC cools a room and forecasts time and cost to the setpoint."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform

from .const import DOMAIN
from .coordinator import HorizonCoordinator
from .forecast import Forecast, PriceSlot, forecast
from .model import Fit, NotEnoughData, Sample, fit

__all__ = ["Fit", "Forecast", "NotEnoughData", "PriceSlot", "Sample", "fit", "forecast"]

PLATFORMS = [Platform.SENSOR]


SERVICE_RESET = "reset_learning"
SERVICE_EXPORT = "export_samples"
SERVICE_AUTOTUNE = "start_autotune"


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

    async def autotune(call):
        _coordinator(call).start_autotune(float(call.data["setpoint"]))

    from homeassistant.core import SupportsResponse

    hass.services.async_register(DOMAIN, SERVICE_RESET, reset)
    hass.services.async_register(DOMAIN, SERVICE_EXPORT, export, supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, SERVICE_AUTOTUNE, autotune)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _register_services(hass)
    coordinator = HorizonCoordinator(hass, entry)
    await coordinator.async_load()
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
