"""Heater control: the heating-failure check, PWM windows and timers, the heater switch, the per-tick heater step and output limits."""

from __future__ import annotations

import statistics
from typing import Any
from collections.abc import Callable
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util
from homeassistant.helpers import issue_registry as ir

from .const import (
    AUTOTUNE_TIME_LIMIT_MIN,
    CONF_CONTROL,
    CONF_HEATER_ALGORITHM,
    CONF_HEATER_INVERTED,
    CONF_TPI_THRESHOLD_HIGH,
    CONF_TPI_THRESHOLD_LOW,
    HEATER_ALGORITHM_TPI,
    CONF_OUTPUT_MAX,
    CONF_OUTPUT_MAX_ENTITY,
    CONF_OUTPUT_MIN,
    CONF_OUTPUT_MIN_ENTITY,
    CONF_RADIATORS,
    CONF_WINDOW,
    DEFAULT_RESIDUAL_PEAK_K,
    DOMAIN,
    PWM_WINDOW_MIN,
    RESIDUAL_TAU_MIN,
    SAMPLE_INTERVAL_S,
)
from custom_components.hvac_horizon.autotune import RelayTuner
from custom_components.hvac_horizon.features import (
    apply_polarity,
    clamp_output,
    heating_failed,
    house_mode_from_store,
    inhibited,
    lock_allows_control,
    pwm_on,
    residual_correction,
    room_devices,
    safety_output,
)
from custom_components.hvac_horizon.coordinator_common import _float, _optional_float, SAFETY_FALLBACK_PCT


class HeaterMixin:
    # Instance attributes set in HorizonCoordinator.__init__ and used here.
    _ac_cooling_tick: bool
    _autotune_status: str | None
    _heat_on_below_c: float
    _heater_check_t: float | None
    _heater_commanded_on: bool | None
    _heater_gated: bool
    _heater_on_min: float
    _heater_start_temp: float | None
    _heater_stopped_min: float | None
    _heater_switch_driven: str | None
    _heater_was_on: bool
    _heating_fail_reported: bool
    _inhibit_until_min: float | None
    _inputs_seen: bool
    _locked: bool
    _pwm_cancel: list[Callable[[], None]]
    _pwm_on_now: bool
    _pwm_output_pct: float
    _pwm_pct_now: float
    _pwm_window_start_s: float | None
    _tuner: RelayTuner | None
    _tuner_started_s: float
    _user_off: bool
    entry: ConfigEntry
    hass: HomeAssistant

    def _heating_check(self, now: float, demand: bool, heater_on: bool, room_c: float | None) -> bool:
        """True when the heater has been on for the failure window without the room rising.

        `demand` is true while the heater output is above 0. On-minutes accumulate only while the switch is
        commanded on, and reset with the start temperature when demand ends.
        """
        elapsed_min = 0.0 if self._heater_check_t is None else min(now - self._heater_check_t, 2 * SAMPLE_INTERVAL_S) / 60.0
        self._heater_check_t = now
        if not demand or room_c is None:
            self._heater_on_min = 0.0
            self._heater_start_temp = None
            return False
        if self._heater_start_temp is None:
            self._heater_start_temp = room_c
        if heater_on:
            self._heater_on_min += elapsed_min
        return heating_failed(self._heater_on_min, room_c - self._heater_start_temp)

    def _report_heating_failure(self, failed: bool) -> None:
        """Repair issue while the heater is on without warming the room. Cleared when it recovers."""
        issue_id = f"{self.entry.entry_id}_heating_failure"
        if failed and not self._heating_fail_reported:
            ir.async_create_issue(
                self.hass, DOMAIN, issue_id, is_fixable=False, severity=ir.IssueSeverity.WARNING,
                translation_key="heating_failure",
            )
            self._heating_fail_reported = True
        elif not failed and self._heating_fail_reported:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            self._heating_fail_reported = False

    def _heater_gate_reason(self, result: dict[str, Any]) -> str | None:
        """The gate that forces the heater off, as text, or None when no gate is closed.

        Gates: window open, house away, the outdoor heating limit, another entry of the house group cooling, the lock,
        the room off, a mode with no heating, or a stalled sensor.
        """
        window = self._optional(CONF_WINDOW)
        if window is not None and window.state == "on":
            return "window open"
        if house_mode_from_store(self.hass.data.get(DOMAIN, {})) == "away":
            return "house mode is away"
        if not result.get("heating_allowed", True):
            return "outdoors above 22 °C"
        if result.get("house_cooling"):
            return "another room is cooling"
        if not lock_allows_control(self._locked):
            return "locked"
        if self._user_off:
            return "room is off"
        if not self.heating_permitted:
            return f"mode is {self.user_mode}"
        if self._stall_blocks(result):
            return "a sensor has stalled"
        return None

    def _heater_gate_closed(self, result: dict[str, Any]) -> bool:
        """True when a gate forces the heater off."""
        return self._heater_gate_reason(result) is not None

    def _heat_hold_text(self, result: dict[str, Any]) -> str | None:
        """`heat held: <gate>` when the room is below the heat threshold, the mode allows heating and a gate keeps the heater off."""
        if not (self.entry.data.get(CONF_CONTROL) and self._heater_switch()) or self._user_off or not self.heating_permitted:
            return None
        rooms = self._rooms()
        if not rooms or statistics.median(rooms) >= self._heat_on_below_c:
            return None
        reason = self._heater_gate_reason(result)
        return None if reason is None else f"heat held: {reason}"

    def _heater_off_at_c(self) -> float:
        """Room temperature at which the heater output is forced to 0.

        The PID stops one deadband below the heat target (`_heat_on_below_c`). TPI is proportional to the gap and
        reaches 0 at the target by itself, so it runs up to the target. With both activation thresholds set it runs
        up to the larger one above the target, and tpi_threshold_forced_off decides inside that range. The limit
        stays at or below the cool threshold.
        """
        if self.entry.data.get(CONF_HEATER_ALGORITHM) != HEATER_ALGORITHM_TPI:
            return self._heat_on_below_c
        high = _optional_float(self.entry.data.get(CONF_TPI_THRESHOLD_HIGH)) or 0.0
        low = _optional_float(self.entry.data.get(CONF_TPI_THRESHOLD_LOW)) or 0.0
        above = max(high, low) if high > 0 and low > 0 else 0.0
        return min(self._heat_target_c + above, self._cool_above_c)

    def _cancel_pwm_timers(self) -> None:
        while self._pwm_cancel:
            self._pwm_cancel.pop()()

    def _pwm_step(self, now: float) -> None:
        """Set the heater's logical on or off state for `now`, and schedule the next edge.

        A window starts with the on edge. The off edge falls at pct / 100 of the window and the window
        ends after PWM_WINDOW_MIN minutes. Timers fire at each edge, so the switch changes on time
        between coordinator ticks. Output 0 or 100, and a gate, cancel the timers and hold a steady state.
        """
        self._cancel_pwm_timers()
        window_s = PWM_WINDOW_MIN * 60.0
        pct = self._pwm_pct_now
        if self._heater_gated or pct <= 0.0:
            self._pwm_window_start_s = None
            self._pwm_on_now = False
            return
        if pct >= 100.0:
            self._pwm_window_start_s = None
            self._pwm_on_now = True
            return
        if self._pwm_window_start_s is None or now - self._pwm_window_start_s >= window_s:
            self._pwm_window_start_s = now
        elapsed_s = max(0.0, now - self._pwm_window_start_s)
        self._pwm_on_now = pwm_on(pct, PWM_WINDOW_MIN, elapsed_s / 60.0)
        on_s = pct / 100.0 * window_s
        if self._pwm_on_now:
            self._pwm_cancel.append(async_call_later(self.hass, on_s - elapsed_s, self._pwm_timer))
        self._pwm_cancel.append(async_call_later(self.hass, window_s - elapsed_s, self._pwm_timer))

    async def _pwm_timer(self, _when: datetime) -> None:
        """Runs at an off edge or a window end. Recomputes the state and drives the switch when control is on."""
        if self._tuner is not None:
            return  # a relay test owns the heater switch until it ends
        self._pwm_step(dt_util.utcnow().timestamp())
        if self.entry.data.get(CONF_CONTROL) and self._heater_switch():
            await self._drive_heater_switch()

    def _heater_switch(self) -> str | None:
        """The heater switch of the entry. A switch that is also a fleet device belongs to the fleet dispatch."""
        switch = (room_devices(self.entry.data)["heating"] or [None])[0]
        return None if switch in {d.entity_id for d in self._fleet()} else switch

    async def _command_heater(self, switch: str, command_on: bool) -> None:
        """Send the switch command and record the switch and the state, so unload can undo it."""
        self._heater_switch_driven = switch
        self._heater_commanded_on = command_on
        await self._drive_switch(switch, command_on)

    async def _drive_heater_switch(self) -> None:
        """Command the heater switch to the logical PWM state, with the configured polarity."""
        switch = self._heater_switch()
        if not switch:
            return
        if self._pwm_on_now and inhibited(dt_util.utcnow().timestamp() / 60.0, self._inhibit_until_min):
            # The inhibit window after a manual change blocks heat on only. Every off command goes through.
            return
        command_on = apply_polarity(self._pwm_on_now, bool(self.entry.data.get(CONF_HEATER_INVERTED, False)))
        await self._command_heater(switch, command_on)

    async def _async_heater_step(self, now: float, result: dict[str, Any]) -> None:
        """Publish the PWM state and, with control on and a heater switch set, drive the switch.

        Gates, an AC that is cooling and a room at or above the heat threshold force the output to 0.
        """
        switch = self._heater_switch()
        driving = bool(self.entry.data.get(CONF_CONTROL)) and bool(switch)
        rooms = self._rooms()
        room_c = statistics.median(rooms) if rooms else None
        gate_c = room_c
        if room_c is not None and self.entry.data.get(CONF_RADIATORS) and self._heater_stopped_min is not None:
            # Radiators keep giving heat after the heater stops. The residual correction reads the room warmer by that heat.
            gate_c = residual_correction(room_c, DEFAULT_RESIDUAL_PEAK_K, now / 60.0 - self._heater_stopped_min, RESIDUAL_TAU_MIN)
        pct = self._pwm_output_pct
        if driving and (self._heater_gated or self._ac_cooling_tick or gate_c is None or gate_c >= self._heater_off_at_c()):
            pct = 0.0
        if driving:
            # A stalled sensor sets the fallback output, whatever the gates say (features.safety_output).
            pct = safety_output(self._stall_blocks(result), pct, fallback_pct=SAFETY_FALLBACK_PCT)
            if not self._inputs_seen:
                pct = 0.0  # no heat command before the first valid room and outdoor readings
        relay_running = self._tuner is not None
        if relay_running:
            # The relay test replaces the PWM output. The PWM timers stay cancelled until the test ends.
            # It obeys the cutoffs of normal heating and a time limit; reaching either ends the test with the heater off.
            self._cancel_pwm_timers()
            ended = None
            if now - self._tuner_started_s >= AUTOTUNE_TIME_LIMIT_MIN * 60.0:
                ended = "timed_out"
            elif driving and (self._ac_cooling_tick or (gate_c is not None and gate_c >= self._heat_on_below_c)):
                ended = "reached_cutoff"
            if ended is not None:
                self._tuner = None
                self._autotune_status = ended
                relay_running = False
                self._pwm_on_now = False
                self._pwm_pct_now = 0.0
                self._pwm_window_start_s = None
            else:
                self._pwm_on_now = self._relay_tick(now, room_c, driving and not self._heater_gated)
        else:
            self._pwm_pct_now = pct
            self._pwm_step(now)
        heater_on = self._pwm_on_now
        if heater_on:
            self._heater_stopped_min = None
        elif self._heater_was_on:
            self._heater_stopped_min = now / 60.0
        self._heater_was_on = heater_on
        result["heater_pwm_on"] = heater_on
        # During a relay test the demand is the relay output, so the check sees a heater that stays on without warming the room.
        demand = heater_on if relay_running else pct > 0
        failed = self._heating_check(now, driving and demand, heater_on, room_c)
        result["heating_failure"] = failed
        self._report_heating_failure(failed)
        if driving:
            await self._drive_heater_switch()

    async def async_heater_off(self) -> None:
        """Stop the PWM timers and turn off the switch this instance last drove.

        Uses the recorded switch and state, not the current options, so a reconfigure or a disabled
        control does not leave the heater on. Does nothing when the last command was off.
        """
        self._cancel_pwm_timers()
        self._pwm_on_now = False
        self._pwm_window_start_s = None
        switch = self._heater_switch_driven
        if switch is None or not self._heater_commanded_on:
            return
        await self._command_heater(switch, False)

    def _output_limit(self, entity_key: str, config_key: str, default: float) -> float:
        """A heater output limit: the number entity's value when it has one, else the config value."""
        value = _float(self._optional(entity_key))
        return value if value is not None else float(self.entry.data.get(config_key, default))

    def set_heater_output(self, pct: float) -> None:
        lo = self._output_limit(CONF_OUTPUT_MIN_ENTITY, CONF_OUTPUT_MIN, 0.0)
        hi = max(lo, self._output_limit(CONF_OUTPUT_MAX_ENTITY, CONF_OUTPUT_MAX, 100.0))
        self._pwm_output_pct = clamp_output(pct, lo, hi)
