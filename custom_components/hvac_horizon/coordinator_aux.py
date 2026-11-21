"""Auxiliary devices: TRV valve, load shedding and restore, window actuator, cover, fan mode and transition notifications."""

from __future__ import annotations

import statistics
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Context, HomeAssistant, State
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.components import persistent_notification

from .const import CONF_CONTROL, CONF_COVER, CONF_OUTDOOR, CONF_WINDOW_ACTUATOR, DOMAIN
from custom_components.hvac_horizon.features import (
    TEMP_ADVICE_DEADBAND_K,
    effective_home,
    fan_mode_for_gap,
    house_mode_from_store,
    inhibited,
    lock_allows_control,
    room_devices,
    transition_message,
    valve_exercise_due,
    window_actuator_command,
)
from custom_components.hvac_horizon.coordinator_common import _float


class AuxMixin:
    # Instance attributes set in HorizonCoordinator.__init__ and used here.
    _actuator_opened_by_us: bool
    _cool_above_c: float
    _heat_on_below_c: float
    _inhibit_until_min: float | None
    _loaded_at: float
    _locked: bool
    _shed_at: dict[str, float]
    _shed_context: dict[str, str]
    _shed_loads: set[str]
    _store: Store[dict[str, Any]]
    _valve_commanded_pct: float
    _valve_driven: str | None
    _valve_moved_min: float | None
    entry: ConfigEntry
    hass: HomeAssistant

    def _notify_transition(self, kind: str, attr: str, current: bool, on_text: str, off_text: str) -> None:
        """Raise a persistent notification when `current` turns True and dismiss it when it turns False."""
        message = transition_message(getattr(self, attr), current, on_text, off_text)
        setattr(self, attr, current)
        if message is None:
            return
        notification_id = f"{self.entry.entry_id}_{kind}"
        if current:
            persistent_notification.async_create(self.hass, message, title="HVAC Horizon", notification_id=notification_id)
        else:
            persistent_notification.async_dismiss(self.hass, notification_id)

    async def _drive_valve(self, opening: float) -> None:
        """Set the valve number entity to `opening` percent. Runs only with control on and a valve configured."""
        valve = (room_devices(self.entry.data)["valves"] or [None])[0]
        if not self.entry.data.get(CONF_CONTROL) or not valve:
            return
        if not lock_allows_control(self._locked):
            return
        if inhibited(dt_util.utcnow().timestamp() / 60.0, self._inhibit_until_min):
            return
        state = self.hass.states.get(valve)
        if state is None or state.state in ("unavailable", "unknown"):
            return
        now_min = dt_util.utcnow().timestamp() / 60.0
        if self._valve_moved_min is None:
            self._valve_moved_min = now_min
        current = _float(state)
        if current is not None and abs(current - opening) <= 1.0:
            # An idle valve can seize. After a week still, step it 10 points for one cycle, then the normal command returns it.
            if valve_exercise_due((now_min - self._valve_moved_min) / (24 * 60.0)):
                step = opening - 10.0 if opening > 90.0 else opening + 10.0
                self._record_valve(valve, step)
                await self.hass.services.async_call("number", "set_value", {"entity_id": valve, "value": step}, blocking=True)
                self._valve_moved_min = now_min
            return
        self._record_valve(valve, opening)
        await self.hass.services.async_call("number", "set_value", {"entity_id": valve, "value": opening}, blocking=True)
        self._valve_moved_min = now_min

    def _record_valve(self, valve: str, opening: float) -> None:
        """Record the valve and the opening about to be sent, so the safety stop can close it."""
        self._valve_driven = valve
        self._valve_commanded_pct = opening

    async def _drive_shed(self, to_shed: list[str]) -> None:
        """Switch off the planned loads. Runs only with control on, unlocked and not inhibited."""
        if not to_shed or not self.entry.data.get(CONF_CONTROL):
            return
        if not lock_allows_control(self._locked):
            return
        if inhibited(dt_util.utcnow().timestamp() / 60.0, self._inhibit_until_min):
            return
        for entity_id in to_shed:
            command = Context()
            await self.hass.services.async_call("switch", "turn_off", {"entity_id": entity_id}, blocking=True, context=command)
            self._shed_loads.add(entity_id)
            self._shed_at[entity_id] = dt_util.utcnow().timestamp()
            self._shed_context[entity_id] = command.id
            self._store.async_delay_save(self._save_payload, 0)

    async def _drive_restore(self, entity_id: str) -> bool:
        """Switch a load back on after the power limit shed it. Runs only with control on, unlocked and not inhibited.

        Returns True when this call sent the turn-on command. The record is dropped once the load is on,
        whether this call turned it on or the user did.
        """
        if not self.entry.data.get(CONF_CONTROL) or not lock_allows_control(self._locked):
            return False
        if inhibited(dt_util.utcnow().timestamp() / 60.0, self._inhibit_until_min):
            return False
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return False  # keep the record until the load reports again
        if state.state != "on" and self._changed_since_shed(entity_id, state):
            # The user or another automation changed the load after the shed. It is not this entry's to turn on.
            self._forget_shed(entity_id)
            return False
        sent = state.state != "on"
        await self._drive_switch(entity_id, True)
        if entity_id in self._shed_loads:
            self._forget_shed(entity_id)
        return sent

    def _changed_since_shed(self, entity_id: str, state: State) -> bool:
        """True when the load changed after this entry shed it, by a context other than this entry's turn_off.

        A record restored from the store has no context. It sees only changes after the store was read, because
        Home Assistant resets last_changed for every entity at start.
        """
        shed_at = self._shed_at.get(entity_id)
        if shed_at is None:
            return False
        own = self._shed_context.get(entity_id)
        since = shed_at if own is not None else max(shed_at, self._loaded_at)
        return state.last_changed.timestamp() > since and state.context.id != own

    def _forget_shed(self, entity_id: str) -> None:
        self._shed_loads.discard(entity_id)
        self._shed_at.pop(entity_id, None)
        self._shed_context.pop(entity_id, None)
        self._store.async_delay_save(self._save_payload, 0)

    async def _drive_window_actuator(self, result: dict[str, Any]) -> None:
        """Open or close the window actuator on the natural ventilation or economizer advice.

        The gates (control on, unlocked, house not away, no stalled sensor, not in the inhibit window after a manual
        change) stop an open only. House away ends the open advice. A close, when this integration opened the actuator
        and the advice has ended, goes ahead whatever the gates say. The actuator is written only on a state change.
        """
        actuator = self.entry.data.get(CONF_WINDOW_ACTUATOR)
        state = self.hass.states.get(actuator) if actuator else None
        if state is None or state.state in ("unavailable", "unknown"):
            return
        rooms = self._rooms()
        outdoor = self._outdoor_now()
        if not rooms or outdoor is None:
            return
        house_away = not effective_home(True, house_mode_from_store(self.hass.data.get(DOMAIN, {})))
        # House away ends the open advice, so an actuator this integration opened closes at once.
        open_advice = (
            result.get("window_advice") == "open" or result.get("economizer") in ("open_windows", "fan_assist")
        ) and not house_away and self.cooling_permitted  # opening a window to cool is cooling; heat mode leaves it shut
        # Open advice needs a room that wants cooling. A wanted or running heater blocks it.
        heater_id = self._heater_switch()
        heater_state = self.hass.states.get(heater_id) if heater_id else None
        heater_on = heater_state is not None and heater_state.state == "on"
        room_c = statistics.median(rooms)
        is_open = state.state in ("open", "on")
        # An open actuator keeps its open advice until the room is TEMP_ADVICE_DEADBAND_K below the cooling threshold.
        cool_floor = self._cool_above_c - (TEMP_ADVICE_DEADBAND_K if is_open else 0.0)
        if room_c <= cool_floor or room_c < self._heat_on_below_c or heater_on:
            open_advice = False
        command = window_actuator_command(
            open_advice, statistics.median(rooms), outdoor, is_open, self._actuator_opened_by_us
        )
        if command is None:
            return
        inhibit_on = inhibited(dt_util.utcnow().timestamp() / 60.0, self._inhibit_until_min)
        gated = not self.entry.data.get(CONF_CONTROL) or not lock_allows_control(self._locked) or house_away or self._stall_blocks(result) or inhibit_on
        if command == "open" and gated:
            return
        domain = actuator.split(".", 1)[0]
        service = {"open": "open_cover" if domain == "cover" else "turn_on", "close": "close_cover" if domain == "cover" else "turn_off"}[command]
        await self.hass.services.async_call(domain, service, {"entity_id": actuator}, blocking=True)
        opened = command == "open"
        if opened != self._actuator_opened_by_us:
            self._actuator_opened_by_us = opened
            self._store.async_delay_save(self._save_payload, 0)

    async def _drive_cover(self, action: str) -> None:
        """Close or open the configured cover. Runs only with control on, unlocked and not inhibited."""
        cover_id = self.entry.data.get(CONF_COVER)
        if action not in ("close", "open") or not cover_id or not self.entry.data.get(CONF_CONTROL):
            return
        if not lock_allows_control(self._locked):
            return
        if inhibited(dt_util.utcnow().timestamp() / 60.0, self._inhibit_until_min):
            return
        await self.hass.services.async_call(
            "cover", "close_cover" if action == "close" else "open_cover", {"entity_id": cover_id}, blocking=True
        )

    async def _write_fan_mode(self, climate_id: str, climate: State, gap_c: float) -> None:
        """Set the AC fan mode from the room gap. Sends only when the device lists the mode and it differs from the current one."""
        mode = fan_mode_for_gap(gap_c, climate.attributes.get("fan_modes"))
        if mode is None or climate.attributes.get("fan_mode") == mode:
            return
        await self.hass.services.async_call("climate", "set_fan_mode", {"entity_id": climate_id, "fan_mode": mode}, blocking=True)
