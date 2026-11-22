"""HVAC Horizon: learns how fast an AC cools a room and forecasts time and cost to the setpoint."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.core import Event, HomeAssistant
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.util import dt as dt_util

from homeassistant.helpers import entity_registry as er

from .const import CONF_ECO_C, DEFAULT_COMFORT_C, DOMAIN
from .binary_sensor import BINARY_KEYS
from .config_flow import split_settings
from .accuracy_api import async_register_accuracy_page, sync_accuracy_panel
from .coordinator import HorizonCoordinator
from .room_model.forecast import Forecast, PriceSlot, forecast
from .room_model.model import Fit, NotEnoughData, Sample, fit

__all__ = ["Fit", "Forecast", "NotEnoughData", "PriceSlot", "Sample", "fit", "forecast"]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.CLIMATE]


SERVICE_RESET = "reset_learning"
SERVICE_EXPORT = "export_samples"
SERVICE_AUTOTUNE = "start_autotune"
SERVICE_CANCEL_AUTOTUNE = "cancel_autotune"
SERVICE_WHAT_IF = "what_if"
SERVICE_EXPORT_CSV = "export_csv"
SERVICE_LOCK = "set_lock"
SERVICE_TIMED = "start_timed_preset"
SERVICE_RESPOND = "respond_suggestion"
SERVICE_HOUSE_MODE = "set_house_mode"
SERVICE_INVESTIGATION_REPORT = "generate_investigation_report"


def _checked(handler):
    """Run an action and turn a refused value into ServiceValidationError, so Home Assistant shows the reason to the user."""

    async def run(call):
        try:
            return await handler(call)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

    return run


def _register_services(hass: HomeAssistant) -> None:
    """Register the actions. Called from async_setup, so they exist before an entry is loaded."""

    def _coordinator(call):
        entry = hass.config_entries.async_get_entry(call.data["entry_id"])
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError("unknown HVAC Horizon entry")
        if entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(f"the HVAC Horizon entry {entry.title} is not loaded")
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
        from .room_model.collector import entries_to_csv

        return {"csv": entries_to_csv(_coordinator(call)._entries)}

    async def autotune(call):
        _coordinator(call).start_autotune(float(call.data["setpoint"]))

    async def cancel_autotune(call):
        await _coordinator(call).async_cancel_autotune()

    async def set_lock(call):
        changed = _coordinator(call).set_lock(bool(call.data["locked"]), call.data.get("pin"))
        if not changed:
            raise ServiceValidationError("lock not changed: wrong PIN, or no PIN configured")

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
            raise ServiceValidationError("house mode must be home or away")
        hass.data.setdefault(DOMAIN, {})["house_mode"] = mode
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state is ConfigEntryState.LOADED:
                entry.runtime_data.save_soon()
                await entry.runtime_data.async_request_refresh()

    from homeassistant.core import SupportsResponse

    from homeassistant.helpers.service import async_register_admin_service

    # Admin only: these clear or export the stored history, drive the heater outside normal control, change the
    # learned baseline, spend the model-call budget or lock control.
    import voluptuous as vol

    fields = vol.Schema({}, extra=vol.ALLOW_EXTRA)  # each handler reads and checks its own fields
    async_register_admin_service(hass, DOMAIN, SERVICE_RESET, _checked(reset), schema=fields)
    async_register_admin_service(hass, DOMAIN, SERVICE_EXPORT, _checked(export), schema=fields, supports_response=SupportsResponse.ONLY)
    async_register_admin_service(hass, DOMAIN, SERVICE_EXPORT_CSV, _checked(export_csv), schema=fields, supports_response=SupportsResponse.ONLY)
    async_register_admin_service(hass, DOMAIN, SERVICE_AUTOTUNE, _checked(autotune), schema=fields)
    async_register_admin_service(hass, DOMAIN, SERVICE_RESPOND, _checked(respond), schema=fields)
    async_register_admin_service(hass, DOMAIN, SERVICE_INVESTIGATION_REPORT, _checked(investigation_report), schema=fields)
    async_register_admin_service(hass, DOMAIN, SERVICE_LOCK, _checked(set_lock), schema=fields)
    # Any user: what a wall thermostat offers. Cancelling a relay test only turns the heater off.
    hass.services.async_register(DOMAIN, SERVICE_CANCEL_AUTOTUNE, _checked(cancel_autotune))
    hass.services.async_register(DOMAIN, SERVICE_WHAT_IF, _checked(what_if), supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, SERVICE_TIMED, _checked(start_timed))
    hass.services.async_register(DOMAIN, SERVICE_HOUSE_MODE, _checked(set_house_mode))


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
    if entry.version == 2:
        # Version 3 keeps the room (area, devices, sensors) in the data and moves every tunable setting to the options.
        data, moved = split_settings(dict(entry.data))
        hass.config_entries.async_update_entry(entry, data=data, options={**moved, **entry.options}, version=3)
    return True


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    _register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await async_register_accuracy_page(hass)
    coordinator = HorizonCoordinator(hass, entry)
    await coordinator.async_load()
    await coordinator.async_backfill_from_recorder()
    coordinator.start_power_tracking()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    async def _on_stop(_event: Event) -> None:
        # Home Assistant stop does not unload entries, so switch off what this entry turned on.
        await coordinator.async_safety_stop()

    entry.async_on_unload(hass.bus.async_listen(EVENT_HOMEASSISTANT_STOP, _on_stop))
    entry.async_on_unload(coordinator.start_watchdog())
    sync_accuracy_panel(hass)
    _remove_sensors_that_became_binary_sensors(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _remove_sensors_that_became_binary_sensors(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the sensor entities of values that are binary sensors since 0.7.0, so no dead entity stays in the registry."""
    registry = er.async_get(hass)
    for key in BINARY_KEYS:
        if entity_id := registry.async_get_entity_id(Platform.SENSOR, DOMAIN, f"{entry.entry_id}_{key}"):
            registry.async_remove(entity_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the entry's stored readings and state when the entry is removed."""
    from homeassistant.helpers.storage import Store

    from .const import STORE_VERSION

    await Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Stop the refresh timer first, so no tick drives the heater while the entry unloads.
    await entry.runtime_data.async_shutdown()
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        sync_accuracy_panel(hass, exclude_entry_id=entry.entry_id)
        entry.runtime_data.forget_cooling()
        entry.runtime_data.forget_peer_requests()
        await entry.runtime_data.async_safety_stop()
        await entry.runtime_data.async_shutdown_store()
    return unloaded
