"""Safety stop: switching every driven device off on unload or when a lock or stalled sensors require it."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CLIMATE,
    CONF_CLIMATE_2,
    CONF_CONTROL,
    CONF_WINDOW,
    CONF_WINDOW_DELAY_MIN,
    DEFAULT_WINDOW_DELAY_MIN,
    DOMAIN,
)
from custom_components.hvac_horizon.features import effective_home, house_mode_from_store, window_open_pause
from custom_components.hvac_horizon.coordinator_common import _float


class SafetyMixin:
    # Instance attributes set in HorizonCoordinator.__init__ and used here.
    _coil_dry_sent: bool
    _last_sent_min: float | None
    _last_written_mode: str | None
    _second_ac_commanded_on: bool
    _switches_on: set[str]
    _valve_commanded_pct: float
    _valve_driven: str | None
    _window_open_since: float | None
    entry: ConfigEntry
    hass: HomeAssistant

    async def async_safety_stop(self) -> None:
        """Turn off everything this instance last commanded on. Used at Home Assistant stop and unload.

        Ignores the lock and the restart delay. Cancels the PWM timers and the refresh timer,
        so no tick turns a device on again.
        """
        self._unschedule_refresh()
        self._cancel_recheck()
        await self.async_heater_off()
        await self._fleet_all_off(dt_util.utcnow().timestamp())
        if not self.entry.data.get(CONF_CONTROL):
            return
        climate_id = self.entry.data[CONF_CLIMATE]
        if self._last_written_mode in ("cool", "heat", "dry") or self._coil_dry_sent:
            climate = self.hass.states.get(climate_id)
            if climate is not None and climate.state in ("cool", "heat", "dry"):
                await self.hass.services.async_call(
                    "climate", "set_hvac_mode", {"entity_id": climate_id, "hvac_mode": "off"}, blocking=True
                )
            self._last_written_mode = "off"
            self._coil_dry_sent = False
        climate_2_id = self.entry.data.get(CONF_CLIMATE_2)
        if climate_2_id and self._second_ac_commanded_on:
            second = self.hass.states.get(climate_2_id)
            if second is not None and second.state in ("cool", "heat", "dry"):
                await self.hass.services.async_call(
                    "climate", "set_hvac_mode", {"entity_id": climate_2_id, "hvac_mode": "off"}, blocking=True
                )
            self._second_ac_commanded_on = False
        for entity_id in sorted(self._switches_on):
            await self._drive_switch(entity_id, False, track=True)
        valve = self._valve_driven
        if valve is not None and self._valve_commanded_pct > 0.0:
            state = self.hass.states.get(valve)
            if state is not None and state.state not in ("unavailable", "unknown") and _float(state) != 0.0:
                await self.hass.services.async_call("number", "set_value", {"entity_id": valve, "value": 0.0}, blocking=True)
            self._valve_commanded_pct = 0.0

    async def _drive_switch(self, entity_id: str | None, on: bool, *, track: bool = False) -> None:
        """Turn an optional switch on or off, only when it differs from its state.

        With `track`, record the command before the call, so a call cancelled part way is still undone by the safety stop.
        """
        if not entity_id:
            return
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return
        if (state.state == "on") == on:
            return
        service = "turn_on" if on else "turn_off"
        if track:
            if on:
                self._switches_on.add(entity_id)
            else:
                self._switches_on.discard(entity_id)
        await self.hass.services.async_call("switch", service, {"entity_id": entity_id}, blocking=True)

    async def _locked_safety_stop(self, now: float, result: dict[str, Any], climate_id: str, climate: State | None) -> None:
        """Turn the AC off while locked when a stall, an open window or an away house calls for it."""
        window = self._optional(CONF_WINDOW)
        if window is not None and window.state == "on":
            self._window_open_since = self._window_open_since or now
        else:
            self._window_open_since = None
        window_open_min = 0.0 if self._window_open_since is None else (now - self._window_open_since) / 60.0
        window_delay_min = float(self.entry.data.get(CONF_WINDOW_DELAY_MIN, DEFAULT_WINDOW_DELAY_MIN))
        house_away = not effective_home(True, house_mode_from_store(self.hass.data.get(DOMAIN, {})))
        reason = None
        if self._stall_blocks(result) or self._no_room_data_stop(now):
            reason = "sensor stalled"
        elif window_open_pause(window_open_min, window_delay_min):
            reason = "window open"
        elif house_away:
            reason = "house away"
        if reason is None:
            return
        await self._fleet_all_off(now)
        if climate is None or climate.state in ("off", "unavailable", "unknown"):
            return
        await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate_id, "hvac_mode": "off"}, blocking=True)
        self._last_sent_min = now / 60.0
        self._last_written_mode = "off"
        self._coil_dry_sent = False
        result["action"] = f"locked: off, {reason}"
