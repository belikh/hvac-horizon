"""Fleet dispatch: choosing and driving the climate and switch devices of a fleet. A stop is immediate; a start waits for the restart delay."""

from __future__ import annotations

import statistics
from typing import Any
from dataclasses import fields, replace

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State

from .const import CONF_DISPATCH_MODE, CONF_FLEET, CONF_MIN_OFF_MIN, CONF_PRICE, DEFAULT_PRICE_CHEAP
from custom_components.hvac_horizon.fleet import COOL, Device, FAST, HEAT, choose_devices, device_kw
from custom_components.hvac_horizon.decide import Mode
from custom_components.hvac_horizon.collector import RUNNING_MIN_KW
from custom_components.hvac_horizon.features import restart_wait_min
from custom_components.hvac_horizon.coordinator_common import _LOGGER, _float


class FleetMixin:
    # Instance attributes set in HorizonCoordinator.__init__ and used here.
    _cool_above_c: float
    _device_rates: dict[str, dict[str, float]]
    _fleet_changed_at: dict[str, float]
    _fleet_warned: bool
    _heat_on_below_c: float
    _heat_target_c: float
    entry: ConfigEntry
    hass: HomeAssistant

    def _fleet_running(self, device: Device) -> bool:
        """True when the device's entity is running: a climate in cool or heat, or a switch that is on."""
        state = self.hass.states.get(device.entity_id) if device.entity_id else None
        if state is None:
            return False
        return state.state == "on" if device.domain == "switch" else state.state in ("cool", "heat")

    def _fleet_cooling(self) -> bool:
        return any(d.kind == COOL and d.domain == "climate" and self._fleet_state(d) == "cool" for d in self._fleet())

    def _fleet_state(self, device: Device) -> str | None:
        state = self.hass.states.get(device.entity_id) if device.entity_id else None
        return None if state is None else state.state

    def _fleet_kw(self) -> dict[str, float] | None:
        """kW of each fleet device now, for the collector reading. None when no fleet is set."""
        devices = self._fleet()
        if not devices:
            return None
        out: dict[str, float] = {}
        for device in devices:
            sensor = _float(self.hass.states.get(device.power_sensor)) if device.power_sensor else None
            kw = device_kw(device, sensor, self._fleet_running(device))
            out[device.device_id] = kw if kw >= RUNNING_MIN_KW or not device.power_sensor else 0.0
        return out

    def _fleet(self) -> list[Device]:
        """Room devices from the fleet option. A device without device_id and kind is skipped. Unknown keys are dropped with one warning."""
        devices: list[Device] = []
        known = {f.name for f in fields(Device)}
        for raw in self.entry.data.get(CONF_FLEET) or []:
            if not isinstance(raw, dict):
                continue
            unknown = sorted(set(raw) - known)
            if unknown and not self._fleet_warned:
                self._fleet_warned = True
                _LOGGER.warning("Fleet device %s has unknown keys %s; they are ignored.", raw.get("device_id"), unknown)
            try:
                devices.append(Device(**{k: v for k, v in raw.items() if k in known}))
            except TypeError:
                continue
        return devices

    def _dispatch(self, cool_allowed: bool = True, heat_allowed: bool = True, cool_floor_k: float = 0.0) -> dict[str, Any]:
        """The devices to run for the current gaps, with the rate and power of each.

        A device uses its fitted rate when it has one and its entered rate otherwise; `devices` says which.
        `cool_floor_k` keeps the first cooler running while the room sits inside the cooling deadband.
        """
        rooms = self._rooms()
        entered = self._fleet()
        if not rooms or not entered:
            return {"cool": [], "heat": [], "power_kw": 0.0, "devices": {}}
        devices: list[Device] = []
        info: dict[str, dict[str, Any]] = {}
        for device in entered:
            fitted = self._device_rates.get(device.device_id)
            rate = fitted["rate"] if fitted else device.rate_k_per_h_per_kw
            devices.append(replace(device, rate_k_per_h_per_kw=rate))
            info[device.device_id] = {
                "rate_k_per_h_per_kw": None if rate is None else round(rate, 4),
                "rate_source": "fitted" if fitted else ("entered" if rate else "missing"),
                "power_source": "sensor" if device.power_sensor and self.hass.states.get(device.power_sensor) is not None else "rated",
                "power_kw": 0.0,
            }
        median = statistics.median(rooms)
        mode = self.entry.data.get(CONF_DISPATCH_MODE, FAST)
        price = _float(self._optional(CONF_PRICE)) or DEFAULT_PRICE_CHEAP
        cool_gap = max(median - self._cool_above_c, cool_floor_k)
        cool = choose_devices(devices, COOL, cool_gap, 1.0, mode, price) if cool_allowed else []
        heat = choose_devices(devices, HEAT, self._heat_target_c - median, 1.0, mode, price) if heat_allowed and median < self._heat_on_below_c else []
        power = 0.0
        by_id = {d.device_id: d for d in devices}
        for device_id in cool + heat:
            device = by_id[device_id]
            sensor = _float(self.hass.states.get(device.power_sensor)) if device.power_sensor else None
            kw = device_kw(device, sensor, running=True)
            info[device_id]["power_kw"] = round(kw, 3)
            power += kw
        return {"cool": cool, "heat": heat, "power_kw": round(power, 3), "devices": info}

    def _changed_at(self, state: State | None, own: float | None) -> float | None:
        """Time of the last on or off change of a device: the later of this entry's own write and the entity's last_changed.

        last_changed covers a restart and a change made by hand. A state that is missing or unavailable adds nothing.
        """
        times = [] if own is None else [own]
        if state is not None and state.state not in ("unavailable", "unknown"):
            times.append(state.last_changed.timestamp())
        return max(times) if times else None

    def _since_min(self, state: State | None, own: float | None, now: float) -> float:
        changed = self._changed_at(state, own)
        return (now - changed) / 60.0 if changed is not None else 1e9

    def _fleet_restart_wait(self, device: Device, running: bool, now: float) -> float:
        """Minutes the device must still wait before it may start. 0 when it runs or the restart delay (min_off_min) is disabled or over."""
        if running:
            return 0.0
        raw = self.entry.data.get(CONF_MIN_OFF_MIN)
        minutes = 3.0 if raw is None or raw == "" else float(raw)
        since_min = self._since_min(self.hass.states.get(device.entity_id), self._fleet_changed_at.get(device.device_id), now)
        return restart_wait_min(since_min, minutes)

    async def _drive_fleet(self, now: float, result: dict[str, Any], action: Any, stalled: bool) -> None:
        """Drive the dispatch through the gated paths. Chosen devices run and every other fleet device is turned off.

        Cooling follows the cool decision (so window, away, lock, user off, night hold and MPC idle apply). Heating is
        gated like the heater switch. Climate devices get the cool setpoint or the heat target. A stop is immediate and
        a start waits for the restart delay.
        """
        cooling = action.mode is Mode.COOL and not stalled
        heating = not stalled and not cooling and not self._heater_gate_closed(result)
        dispatch = self._dispatch(cool_allowed=cooling, heat_allowed=heating, cool_floor_k=0.5 if cooling else 0.0)
        result["dispatch"] = dispatch
        wanted = set(dispatch["cool"]) | set(dispatch["heat"])
        waiting: list[str] = []
        for device in self._fleet():
            if not device.entity_id or self._fleet_state(device) in (None, "unavailable", "unknown"):
                continue
            on = device.device_id in wanted
            setpoint = action.setpoint_c if device.kind == COOL else self._heat_target_c
            if not await self._drive_fleet_device(device, on, setpoint, now):
                waiting.append(device.device_id)
        dispatch["waiting"] = waiting

    async def _drive_fleet_device(self, device: Device, on: bool, setpoint_c: float | None, now: float) -> bool:
        """Bring one device to on or off. Returns False when a start waits for the restart delay or the compressor group."""
        running = self._fleet_running(device)
        state = self._fleet_state(device)
        if device.domain == "switch":
            if (state == "on") == on:
                return True
            if on and not self._hold_fleet_start(device, running, now):
                return False
            self._fleet_changed_at[device.device_id] = now
            await self._drive_switch(device.entity_id, on)
            return True
        mode = "cool" if device.kind == COOL else "heat"
        if state != (mode if on else "off"):
            if on and not self._hold_fleet_start(device, running, now):
                return False
            if on and device.kind == COOL:
                if not self._group_start_ok(now):
                    return False
                self._record_group_start(now)
            self._fleet_changed_at[device.device_id] = now
            await self.hass.services.async_call(
                "climate", "set_hvac_mode", {"entity_id": device.entity_id, "hvac_mode": mode if on else "off"}, blocking=True
            )
        if on and setpoint_c is not None:
            current = self.hass.states.get(device.entity_id).attributes.get("temperature")
            if current is None or float(current) != float(setpoint_c):
                await self.hass.services.async_call(
                    "climate", "set_temperature", {"entity_id": device.entity_id, "temperature": setpoint_c}, blocking=True
                )
        return True

    def _hold_fleet_start(self, device: Device, running: bool, now: float) -> bool:
        """True when the device may start. When the restart delay holds it, record the wait for `pending` and return False."""
        wait = self._fleet_restart_wait(device, running, now)
        if wait > 0:
            self._hold_start(wait, device.device_id)
            return False
        return True

    async def _fleet_all_off(self, now: float, *, everything: bool = False) -> None:
        """Turn fleet devices off at once. Used by the safety stops and by the user's off.

        By default only a running device gets the command. With `everything`, any device that is not off, such as a
        climate in dry mode, gets it too. An unavailable or unknown device gets none.
        """
        for device in self._fleet():
            if not device.entity_id:
                continue
            if self._fleet_running(device) or (everything and self._fleet_state(device) not in (None, "off", "unavailable", "unknown")):
                await self._drive_fleet_device(device, False, None, now)
