"""Safety stop: switching every driven device off on unload or when a lock or stalled sensors require it."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CLIMATE,
    CONF_CLIMATE_2,
    CONF_CONTROL,
    CONF_WINDOW,
    CONF_WINDOW_DELAY_MIN,
    DEFAULT_WINDOW_DELAY_MIN,
    DOMAIN,
    SAMPLE_INTERVAL_S,
)
from custom_components.hvac_horizon.engine.features import effective_home, house_mode_from_store, window_open_pause
from custom_components.hvac_horizon.coordinator_common import _LOGGER, _float

WATCHDOG_CHECK_S = 60.0  # how often the watchdog looks at the time of the last finished tick
WATCHDOG_MAX_S = 3 * SAMPLE_INTERVAL_S + 60.0  # no finished tick for this long turns every output off


class SafetyMixin:
    # Instance attributes set in HorizonCoordinator.__init__ and used here.
    _coil_dry_sent: bool
    _heater_commanded_on: bool | None
    _heater_gated: bool
    _heater_switch_driven: str | None
    _last_tick_done_s: float | None
    _tick_running: bool
    _pwm_output_pct: float
    _unconfirmed_seen: set[str]
    _unconfirmed_warned: set[str]
    _watchdog_stale_checks: int
    _watchdog_trips: int
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
        await self._outputs_off()

    def start_watchdog(self) -> Callable[[], None]:
        """Start the watchdog timer and return the function that cancels it.

        The timer is separate from the refresh timer and from the tick. It reads one number, the time of the last
        finished tick, so a tick that raises or never returns does not keep it from running.
        """
        self._last_tick_done_s = dt_util.utcnow().timestamp()
        return async_track_time_interval(self.hass, self._watchdog_check, timedelta(seconds=WATCHDOG_CHECK_S))

    async def _watchdog_check(self, _when: Any = None) -> None:
        """Turn every output off when no tick has finished for WATCHDOG_MAX_S and a tick run from here does not finish either.

        The first stale check runs one tick itself, unless one is running. After a clock jump or a suspend that tick
        finishes and nothing trips. When it fails, or a tick hangs, the second stale check turns the outputs off.
        The next finished tick resumes control.
        """
        if not self._watchdog_stale():
            self._watchdog_stale_checks = 0
            return
        if self._watchdog_stale_checks == 0 and not self._tick_running:
            await self.async_refresh()
            if not self._watchdog_stale():
                return
        self._watchdog_stale_checks += 1
        if self._watchdog_stale_checks != 2:
            return
        now = dt_util.utcnow().timestamp()
        self._watchdog_trips += 1
        _LOGGER.error("No control tick has finished for %d s. Turning every output of %s off.", int(now - self._last_tick_done_s), self.entry.title)
        self._cancel_recheck()
        self._cancel_pwm_timers()
        self._heater_gated = True
        self._pwm_output_pct = 0.0
        await self._outputs_off()

    def _watchdog_stale(self) -> bool:
        return self._last_tick_done_s is not None and dt_util.utcnow().timestamp() - self._last_tick_done_s > WATCHDOG_MAX_S

    def _unconfirmed_commands(self) -> list[str]:
        """Devices whose state differs from the last command on two ticks in a row, as "entity: expected X, is Y".

        A command is sent and the device answers later, so one tick of difference is normal. A device someone
        changed by hand is listed too, because its state is not what this entry last wrote.
        """
        if not self.entry.data.get(CONF_CONTROL):
            return []
        found: dict[str, str] = {}

        def check(entity_id: str | None, expected: str, matches: Callable[[str], bool]) -> None:
            state = self.hass.states.get(entity_id) if entity_id else None
            if state is not None and not matches(state.state):
                found[entity_id] = f"{entity_id}: expected {expected}, is {state.state}"

        if self._last_written_mode is not None:
            mode = self._last_written_mode
            check(self._ac_id, mode, lambda s: s == mode or (mode == "off" and s == "fan_only"))
        if self._second_ac_commanded_on:
            check(self.entry.data.get(CONF_CLIMATE_2), "on", lambda s: s in ("cool", "heat", "dry"))
        if self._heater_commanded_on is not None:
            expected = "on" if self._heater_commanded_on else "off"
            check(self._heater_switch_driven, expected, lambda s: s == expected)
        for entity_id in sorted(self._switches_on):
            check(entity_id, "on", lambda s: s == "on")
        repeated = sorted(text for entity_id, text in found.items() if entity_id in self._unconfirmed_seen)
        self._unconfirmed_seen = set(found)
        for entity_id in self._unconfirmed_seen - self._unconfirmed_warned:
            if found[entity_id] in repeated:
                _LOGGER.warning("Command not confirmed by the device: %s", found[entity_id])
                self._unconfirmed_warned.add(entity_id)
        self._unconfirmed_warned &= self._unconfirmed_seen
        return repeated

    async def _outputs_off(self) -> None:
        """Turn off everything this instance last commanded on, whatever the lock and the restart delay say."""
        await self.async_heater_off()
        await self._fleet_all_off(dt_util.utcnow().timestamp())
        if not self.entry.data.get(CONF_CONTROL):
            return
        climate_id = self._ac_id
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
