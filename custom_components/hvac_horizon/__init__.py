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


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
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
