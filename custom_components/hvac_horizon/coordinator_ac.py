"""AC control: the per-tick AC step, setpoint and mode writes, AC heat, coil dry, the second AC and compressor-group limits."""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store

from custom_components.hvac_horizon.engine.decide import Action, MPC_DEADBAND_C, Mode, RoomState, Settings, decide
from .const import (
    CONF_AC_HEAT,
    CONF_AUTO_FAN,
    CONF_BOILER,
    CONF_CLIMATE,
    CONF_CLIMATE_2,
    CONF_CO2,
    CONF_COMPRESSOR_GROUP,
    CONF_CONTROL,
    CONF_DEHUMIDIFIER,
    CONF_DRY_FOR_HUMIDITY,
    CONF_ECONOMIZER_FAN,
    CONF_FAN,
    CONF_HOUSE_SEALED,
    CONF_HUMIDIFIER,
    CONF_MIN_OFF_MIN,
    CONF_MPC_HEAT_PLAN,
    CONF_NIGHT_PLAN_ACT,
    CONF_OUTDOOR,
    CONF_PM25,
    CONF_POWER,
    CONF_ROOM_HUMIDITY,
    CONF_SETPOINT_REGULATION,
    CONF_WHOLE_HOUSE_FAN,
    CONF_WINDOW_ACTUATOR,
    CONF_WINDOW,
    CONF_WINDOW_DELAY_MIN,
    DEFAULT_WINDOW_DELAY_MIN,
    DOMAIN,
    FAN_PWM_WINDOW_MIN,
    INHIBIT_MIN,
    KEEP_ALIVE_MIN,
    PEER_SETPOINT_STEP_K,
    REGULATION_MAX_DROP_K,
    REGULATION_MIN_HOLD_MIN,
    SAMPLE_INTERVAL_S,
)
from custom_components.hvac_horizon.room_model.collector import RUNNING_MIN_KW
from custom_components.hvac_horizon.engine.features import (
    air_quality_ventilation,
    coil_dry_due,
    daily_briefing,
    effective_home,
    fan_assist_on,
    group_start_allowed,
    house_cooling,
    house_mode_from_store,
    humidity_action,
    inhibited,
    keep_alive_due,
    lock_allows_control,
    mpc_idle,
    pwm_on,
    restart_wait_min,
    round_to_step,
    stagger_allowed,
    ventilation_rate_from_co2,
    whole_house_fan_allowed,
    window_open_pause,
)
from custom_components.hvac_horizon.coordinator_common import AUTO_CHANGEOVER_MIN, AUTO_CHANGEOVER_OVERRIDE_K, DRY_FLOOR_BELOW_HEAT_K, _float


def _attr_float(state: State, name: str) -> float | None:
    """A numeric attribute of a state, or None when it is missing or not a number."""
    try:
        value = state.attributes.get(name)
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


class AcMixin:
    # Instance attributes set in HorizonCoordinator.__init__ and used here.
    _ac_cooling_tick: bool
    _coil_dry_sent: bool
    _dry_floor_hold: bool
    _cool_above_c: float
    _dry_started_min: float | None
    _heat_on_below_c: float
    _inhibit_until_min: float | None
    _inhibited_for_setpoint: float | None
    _inputs_seen: bool
    _heater_stopped_min: float | None
    _heater_was_on: bool
    _last_sent_min: float | None
    _last_start_min: dict[str, float]
    _last_written_mode: str | None
    _last_written_setpoint: float | None
    _locked: bool
    _peer_setpoint_original: float | None
    _pending_waits: list[tuple[float, str | None]]
    _recheck_cancel: Callable[[], None] | None
    _regulation_drop_k: float
    _regulation_mode: str | None
    _regulation_offset_k: float | None
    _regulation_sent_c: float | None
    _regulation_stepped_s: float | None
    _precool_status: str | None
    _second_ac_commanded_on: bool
    _second_changed_at: float | None
    _settings: Settings | None
    _someone_home: bool
    _state_changed_at: float | None
    _store: Store[dict[str, Any]]
    _user_off: bool
    _was_cooling: bool
    _window_open_since: float | None
    entry: ConfigEntry
    hass: HomeAssistant

    async def _async_control(self, now: float, result: dict[str, Any]) -> None:
        """Send one climate action per cycle. Runs only when the control option is on.

        A start that the restart delay holds is recorded while the cycle runs. The cycle ends by publishing it as `pending`
        and by scheduling a re-check for the moment the delay ends. Each cycle cancels the previous re-check first.
        """
        self._cancel_recheck()
        self._pending_waits = []
        self._regulation_sent_c = None
        self._regulation_offset_k = None
        try:
            await self._async_control_cycle(now, result)
        finally:
            self._publish_pending(result)
            result["ac_setpoint_sent_c"] = self._regulation_sent_c
            result["ac_sensor_offset_k"] = self._regulation_offset_k

    async def _async_control_cycle(self, now: float, result: dict[str, Any]) -> None:
        climate_id = self._ac_id
        first_ac = self.hass.states.get(climate_id)
        self._ac_cooling_tick = (first_ac is not None and first_ac.state == "cool") or self._fleet_cooling()
        if not lock_allows_control(self._locked):
            # The lock blocks normal commands. A stall, an open window or an away house still stops the AC.
            result["action"] = "locked"
            await self._locked_safety_stop(now, result, climate_id, first_ac)
            return
        if self._user_off:
            # The user's off goes out first, ahead of every other gate.
            await self._user_off_stop(now, result)
        now_min = now / 60.0
        if not self._user_off and not self._inputs_seen and not (self._stall_blocks(result) or self._no_room_data_stop(now)):
            # Room and outdoor readings have not been valid yet (sensors still starting). Send no command until they are.
            # A stalled sensor still goes on to the stop path, which sends off commands only.
            result["action"] = "waiting for first valid readings"
            return
        if self._external_change():
            # Arm the inhibition once per manual value. Re-arming on every cycle would keep the AC silent
            # for as long as the manual setpoint stands.
            manual = self._device_setpoint()
            if manual != self._inhibited_for_setpoint:
                self._inhibit_until_min = now_min + INHIBIT_MIN
                self._store.async_delay_save(self._save_payload, 0)
                self._inhibited_for_setpoint = manual
        else:
            self._inhibited_for_setpoint = None
        # A stalled sensor stops the AC even inside the inhibit window. The window sends nothing else.
        stalled = self._stall_blocks(result) or self._no_room_data_stop(now)
        inhibit_on = inhibited(now_min, self._inhibit_until_min)
        if inhibit_on and not stalled and not self._user_off:
            result["action"] = "inhibited after a manual change"
            return
        # Keep-alive: resend the last setpoint when the device has not been written to for KEEP_ALIVE_MIN.
        keep_alive_climate = self.hass.states.get(climate_id)
        if (
            not inhibit_on
            and not self._user_off
            and keep_alive_due(self._last_sent_min, now_min, KEEP_ALIVE_MIN)
            and self._last_written_setpoint is not None
            and keep_alive_climate is not None
            and keep_alive_climate.state not in ("unavailable", "unknown")
        ):
            await self.hass.services.async_call(
                "climate", "set_temperature", {"entity_id": climate_id, "temperature": self._last_written_setpoint}, blocking=True
            )
            self._last_sent_min = now_min
            self._inhibited_for_setpoint = None  # the resend answers the manual change
        rooms = self._rooms()
        if not rooms and not stalled:
            return
        window = self._optional(CONF_WINDOW)
        if window is not None and window.state == "on":
            self._window_open_since = self._window_open_since or now
        else:
            self._window_open_since = None
        window_open_min = 0.0 if self._window_open_since is None else (now - self._window_open_since) / 60.0
        window_delay_min = float(self.entry.data.get(CONF_WINDOW_DELAY_MIN, DEFAULT_WINDOW_DELAY_MIN))
        self._notify_transition(
            "window", "_window_paused", window_open_pause(window_open_min, window_delay_min),
            "HVAC Horizon paused control: a window is open.", "HVAC Horizon resumed control: the window is closed.",
        )
        precool = self._precool_start(now, rooms)
        result["precool_status"] = self._precool_status
        outdoor = self._outdoor_now()
        # The night plan acts only with its flag on. Its pre-cool target and hold use the comfort setpoint.
        night = result.get("night_plan") if self.entry.data.get(CONF_NIGHT_PLAN_ACT, False) else None
        comfort_c = self.comfort_c
        if stalled:
            # A stalled sensor cannot show that the room is cool. The AC stops, so it does not cool on a stale reading.
            # No valid room reading for SENSOR_STALL_S stops the AC the same way.
            action = Action(Mode.OFF, None, "sensor stalled" if self._stall_blocks(result) else "no room reading")
        else:
            # The plan's first step is a request. decide() accepts it only when no cool gate applies.
            state = RoomState(
                room_c=statistics.median(rooms),
                window_open_min=window_open_min,
                someone_home=self._someone_home,
                precool_start_in_min=precool,
                outdoor_c=outdoor if self._outdoor_cool_limit_applies() else None,
                night_precool_c=comfort_c if night == "pre-cool" else None,
            )
            cooling_now = self.hass.states.get(climate_id)
            action = decide(state, self._settings, (cooling_now is not None and cooling_now.state == "cool") or self._fleet_cooling())
            if night == "hold" and action.mode is Mode.COOL:
                # The night hold keeps a start off at comfort. A running AC keeps its setpoint at comfort.
                running_now = self.hass.states.get(climate_id)
                if running_now is None or running_now.state != "cool":
                    action = Action(Mode.OFF, None, "night hold: AC does not start")
                else:
                    action = Action(Mode.COOL, comfort_c, "night hold: setpoint holds comfort")
        # Free cooling comes before the AC. The whole-house fan runs when the room is above the cool threshold and
        # outdoors is cooler. While it runs, or while this entry holds the window open, the AC does not cool.
        free_rooms = self._rooms()
        free_room_c = statistics.median(free_rooms) if free_rooms else None
        self._update_free_cooling(free_room_c)
        fan_id = self.entry.data.get(CONF_WHOLE_HOUSE_FAN)
        fan_ok = bool(
            fan_id
            and free_room_c is not None
            and self.cooling_permitted
            and not self._user_off
            and not stalled
            and not self._free_cooling_gave_up
            and whole_house_fan_allowed(
                bool(self.entry.data.get(CONF_HOUSE_SEALED, False)),
                False,
                self._outdoor_now(),
                free_room_c,
                fan_on=self._switch_on(fan_id),
                cool_above_c=self._cool_above_c,
            )
        )
        if self._user_off:
            action = Action(Mode.OFF, None, "user turned the room off")
        elif action.mode is Mode.COOL and not self.cooling_permitted:
            action = Action(Mode.OFF, None, f"mode is {self.user_mode}: no cooling")
        elif action.mode is Mode.COOL and (fan_ok or self._free_cooling_open()):
            action = Action(Mode.OFF, None, "free cooling: window open or whole-house fan")
        elif action.mode is Mode.COOL and rooms and self._changeover_holds_cooling(now, statistics.median(rooms)):
            action = Action(Mode.OFF, None, f"auto: the heater ran in the last {AUTO_CHANGEOVER_MIN:.0f} min")
        self._ac_cooling_tick = self._ac_cooling_tick or action.mode is Mode.COOL
        # The MPC plan can idle a cool decision only when the room is within the deadband of the threshold (or below it)
        # and the next plan steps are all idle. A room more than the deadband above the threshold keeps cooling.
        # Without a plan (no fit or no price slots) the decision stands. An idle step turns cool off. It never starts
        # heat, so the heat branch below is skipped for that tick.
        plan = result.get("mpc_plan")
        # A night pre-cool is judged against its comfort target, so the plan cannot idle it against the away setback.
        mpc_cool_c = comfort_c if night == "pre-cool" else self._cool_above_c
        mpc_idled = bool(rooms and action.mode is Mode.COOL and mpc_idle(plan, statistics.median(rooms), mpc_cool_c, MPC_DEADBAND_C))
        if mpc_idled:
            action = Action(Mode.OFF, None, "mpc idle: room within deadband of target and plan stays idle")
        if not plan:
            result["mpc_status"] = "no plan: normal decision"
        else:
            result["mpc_status"] = f"plan step 0 {'run' if plan[0] else 'idle'}: " + ("changed the decision to idle" if mpc_idled else "decision unchanged")
        result["action"] = f"{action.mode.value}: {action.reason}"
        climate = self.hass.states.get(climate_id)
        result["daily_briefing"] = daily_briefing(
            result.get("day_type") or "unknown",
            self.comfort_c,
            result.get("action"),
            None,
        )
        # Each output keeps its own on-state, so a reading that wobbles across a threshold does not flip it.
        fan_running = self._switch_on(self.entry.data.get(CONF_FAN))
        ventilation = air_quality_ventilation(_float(self._optional(CONF_CO2)), _float(self._optional(CONF_PM25)), running=fan_running)
        room_humidity = _float(self._optional(CONF_ROOM_HUMIDITY))
        dry_running = climate is not None and climate.state == "dry" and not self.entry.data.get(CONF_DEHUMIDIFIER)
        humidity = humidity_action(
            room_humidity,
            low=self.humidify_below_pct,
            high=self.humidity_target_pct,
            dehumidifying=self._switch_on(self.entry.data.get(CONF_DEHUMIDIFIER)) or dry_running,
            humidifying=self._switch_on(self.entry.data.get(CONF_HUMIDIFIER)),
        )
        result["ventilation"] = ventilation.reason
        result["humidity"] = humidity.reason
        # Dry mode with no room humidity sensor dries until the user changes the mode, as the AC's own dry mode does.
        dry_wanted = humidity.action == "dehumidify" or (self.dry_mode and room_humidity is None)
        # The AC dries in dry mode, and in cool or auto when the dry_for_humidity option is on.
        ac_dry_allowed = self.dry_mode or (bool(self.entry.data.get(CONF_DRY_FOR_HUMIDITY, False)) and self.cooling_permitted)
        # Drying cools the room. Without a heater that keeps up, the AC pauses DRY_FLOOR_BELOW_HEAT_K below the heat
        # threshold and resumes once the room is back at the threshold.
        dry_rooms = self._rooms()
        if not self.dry_mode or not dry_rooms:
            self._dry_floor_hold = False
        else:
            dry_room_c = statistics.median(dry_rooms)
            if dry_room_c < self._heat_on_below_c - DRY_FLOOR_BELOW_HEAT_K:
                self._dry_floor_hold = True
            elif dry_room_c >= self._heat_on_below_c:
                self._dry_floor_hold = False
        if self._dry_floor_hold:
            ac_dry_allowed = False
            result["action"] = "off: dry paused, room below heat threshold"
        # A CO2 rate between 0 and 1 runs the fan for that share of each PWM window. PM2.5 still forces it on.
        # At 0 or 1 the fan follows the on-off rule above.
        co2_rate = ventilation_rate_from_co2(_float(self._optional(CONF_CO2)))
        fan_on = ventilation.run
        if 0.0 < co2_rate < 1.0:
            pm25_on = air_quality_ventilation(None, _float(self._optional(CONF_PM25)), running=fan_running).run
            fan_on = pwm_on(co2_rate * 100.0, FAN_PWM_WINDOW_MIN, now / 60.0) or pm25_on
        # The economizer fan_assist phase runs the fan too, when the economizer_fan option is on and the gates pass.
        house_home = effective_home(True, house_mode_from_store(self.hass.data.get(DOMAIN, {})))
        # Free cooling is cooling, so heat mode leaves it out. Ventilation for CO2 and PM2.5 above runs in every mode.
        if self.cooling_permitted and fan_assist_on(
            result.get("economizer"), bool(self.entry.data.get(CONF_ECONOMIZER_FAN, False)), lock_allows_control(self._locked), house_home, stalled
        ):
            fan_on = True
        started = self._inputs_seen  # no switch starts before the first valid room and outdoor readings
        await self._drive_switch(self.entry.data.get(CONF_FAN), fan_on and started, track=True)
        await self._drive_switch(self.entry.data.get(CONF_DEHUMIDIFIER), dry_wanted and started and not self._user_off, track=True)
        await self._drive_switch(self.entry.data.get(CONF_HUMIDIFIER), humidity.action == "humidify" and started and not self._user_off, track=True)
        heat_ok = result.get("heating_allowed", True)
        await self._drive_switch(self.entry.data.get(CONF_BOILER), bool(result.get("boiler_demand")) and heat_ok and started and not self._user_off and self.heating_permitted, track=True)
        result["whole_house_fan"] = fan_ok
        await self._drive_switch(fan_id, fan_ok and started, track=True)
        if self._user_off:
            return  # _user_off_stop already sent the off commands to the ACs, the heater switch and the fleet
        # The first AC's block below returns early on several paths, so the second AC is driven before it.
        if any(d.entity_id for d in self._fleet()):
            # Fleet devices with an entity replace the single-AC path: the dispatch drives every cooler and heater of the room.
            await self._drive_fleet(now, result, action, stalled)
            return
        await self._drive_second_ac(action, result, now, climate, stalled)
        if climate is None or climate.state in ("unavailable", "unknown"):
            return
        self._check_mode(climate.state)
        if rooms and not mpc_idled and self._ac_heat_wanted(action, result, statistics.median(rooms), window_open_pause(window_open_min, window_delay_min)):
            result["action"] = "heat: room below heat threshold"
            if await self._drive_ac_heat(climate_id, climate, now):
                result["action"] = "heat: waiting for restart delay"
            return
        # A stop is never held. A start after a stop waits for the restart delay (min_off_min; 0 disables it).
        wanted_mode = "cool" if action.mode is Mode.COOL else "off"
        if wanted_mode == "cool" and climate.state != "cool" and not self._group_start_ok(now):
            result["action"] = "cool: waiting for compressor group"
            return
        running = climate.state in ("cool", "heat")
        if climate.state != wanted_mode and (running or wanted_mode == "cool"):
            if wanted_mode == "cool":
                wait = self._restart_wait(climate.state in ("cool", "heat", "dry"), climate, self._state_changed_at, now)
                if wait > 0:
                    self._hold_start(wait, None)
                    result["action"] = "cool: waiting for restart delay"
                    return
            self._state_changed_at = now
        # Write only what differs from the device's current state, so a manual change is not overwritten every cycle.
        if action.mode is Mode.OFF and self._peer_setpoint_requested() and not stalled:
            # A warmer peer room asks for a cooler setpoint. The AC is off, so the write only stores the target.
            self._remember_peer_setpoint(climate)
            await self._write_setpoint(climate_id, climate, self._cool_above_c - PEER_SETPOINT_STEP_K, now)
        elif action.mode is Mode.OFF and self._peer_setpoint_original is not None and not self._peer_setpoint_requested() and climate.state != "cool":
            # The request ended while the AC is off. Put back the setpoint the request lowered.
            await self._write_setpoint(climate_id, climate, self._peer_setpoint_original, now)
            self._peer_setpoint_original = None
        if action.mode is Mode.OFF:
            self._regulation_drop_k = 0.0
            # A stall means the AC goes off. Dry mode would keep it running, so it is skipped then.
            if self.fan_only_mode and not stalled:
                # Fan mode: the AC runs its fan. The fan starts no compressor, so no restart delay applies.
                if climate.state != "fan_only":
                    await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate_id, "hvac_mode": "fan_only"}, blocking=True)
                    self._last_sent_min = now / 60.0
                    self._last_written_mode = "fan_only"
                    self._coil_dry_sent = False
                result["action"] = "fan: the AC runs its fan only"
                return
            if not stalled and await self._drive_coil_dry(climate_id, climate, now):
                return
            # No cooling needed. If the room is too humid and the AC has a dry mode, dry the room with it.
            # An AC already in dry mode stays there while the humidity decision (with its deadband) still says dehumidify.
            if (
                not stalled
                and ac_dry_allowed
                and dry_wanted
                and not self.entry.data.get(CONF_DEHUMIDIFIER)
                and climate.state == "dry"
                and self._last_written_mode == "dry"
                and not self._coil_dry_sent
            ):
                return
            if (
                not stalled
                and ac_dry_allowed
                and dry_wanted
                and not self.entry.data.get(CONF_DEHUMIDIFIER)
                and climate.state != "dry"
                and self._group_start_ok(now)
            ):
                # A dry start runs the compressor, so it waits for the restart delay and the group clock like a cool start.
                wait = self._restart_wait(climate.state in ("cool", "heat", "dry"), climate, self._state_changed_at, now)
                if wait > 0:
                    self._hold_start(wait, None)
                    result["action"] = "dry: waiting for restart delay"
                    return
                self._record_group_start(now)
                await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate_id, "hvac_mode": "dry"}, blocking=True)
                self._last_sent_min = now / 60.0
                self._last_written_mode = "dry"
                return
            if climate.state != "off":
                await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate_id, "hvac_mode": "off"}, blocking=True)
                self._last_sent_min = now / 60.0
                self._last_written_mode = "off"
                self._coil_dry_sent = False
            return
        self._was_cooling = climate.state == "cool"
        self._dry_started_min = None
        if climate.state != "cool":
            self._record_group_start(now)
            await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate_id, "hvac_mode": "cool"}, blocking=True)
            self._last_sent_min = now / 60.0
            self._last_written_mode = "cool"
            self._last_start_min["climate_1"] = now / 60.0
        if self._peer_setpoint_requested():
            self._remember_peer_setpoint(climate)
            cool_setpoint = action.setpoint_c - PEER_SETPOINT_STEP_K
        else:
            # A cool setpoint from decide() replaces any lowered one, so there is nothing to put back later.
            cool_setpoint = action.setpoint_c
            self._peer_setpoint_original = None
        cool_setpoint = self._regulated_setpoint(climate, cool_setpoint, statistics.median(rooms) if rooms else None, now, heat=False)
        await self._write_setpoint(climate_id, climate, cool_setpoint, now)
        if climate.state == "cool" and rooms:
            # The fan follows the room gap while the AC was already cooling before this cycle.
            if self.entry.data.get(CONF_AUTO_FAN, False):
                await self._write_fan_mode(climate_id, climate, statistics.median(rooms) - self._cool_above_c)

    async def _write_setpoint(self, climate_id: str, climate: State, target_c: float, now: float) -> float:
        """Send `target_c` to the AC when it differs from the device setpoint. Returns the setpoint after rounding and clamping.

        Rounds to the device's step when it reports one and keeps the value inside the device's min_temp and max_temp.
        """
        current = climate.attributes.get("temperature")
        step = self._device_step(climate)
        setpoint = round_to_step(target_c, step) if step > 0 else float(target_c)
        low, high = _attr_float(climate, "min_temp"), _attr_float(climate, "max_temp")
        if low is not None:
            setpoint = max(setpoint, low)
        if high is not None:
            setpoint = min(setpoint, high)
        if current is None or float(current) != setpoint:
            await self.hass.services.async_call(
                "climate", "set_temperature", {"entity_id": climate_id, "temperature": setpoint}, blocking=True
            )
            self._last_written_setpoint = setpoint
            self._last_sent_min = now / 60.0
            self._inhibited_for_setpoint = None  # our write answers the manual change
        self._regulation_sent_c = setpoint
        return setpoint

    @staticmethod
    def _device_step(climate: State) -> float:
        """The device's own setpoint step, or 0 when it reports none."""
        try:
            raw = climate.attributes.get("target_temp_step")
            return float(raw) if raw is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    def _rooms_median(self) -> float | None:
        rooms = self._rooms()
        return statistics.median(rooms) if rooms else None

    def _regulated_setpoint(self, climate: State, target_c: float, room_c: float | None, now: float, *, heat: bool) -> float:
        """The setpoint to send so the room, not the AC's own sensor, reaches `target_c`.

        The AC's thermostat compares its own temperature reading with the setpoint. When that reading is cooler than the
        room (warmer for heat), the thermostat is satisfied while the room is not. With the `current_temperature` attribute
        the setpoint moves by the offset: target - (room - ac) for cool, target + (ac - room) for heat, only when the offset
        is positive. Without the attribute the measured power decides (see _power_fallback_offset).
        The result is rounded and clamped by _write_setpoint.
        """
        mode = "heat" if heat else "cool"
        if self._regulation_mode != mode:
            self._regulation_mode, self._regulation_drop_k = mode, 0.0
        if room_c is None or not self.entry.data.get(CONF_SETPOINT_REGULATION, True):
            self._regulation_drop_k = 0.0
            return target_c
        ac_c = _attr_float(climate, "current_temperature")
        if ac_c is not None:
            self._regulation_drop_k = 0.0
            offset = max((ac_c - room_c) if heat else (room_c - ac_c), 0.0)
        else:
            offset = self._power_fallback_offset(climate, target_c, room_c, now, heat=heat)
        self._regulation_offset_k = round(offset, 2)
        return target_c + offset if heat else target_c - offset

    def _power_fallback_offset(self, climate: State, target_c: float, room_c: float, now: float, *, heat: bool) -> float:
        """Setpoint offset in K for an AC that reports no temperature, from the power sensor.

        After the AC has been in the mode for REGULATION_MIN_HOLD_MIN, with the room still beyond its threshold and the
        power below RUNNING_MIN_KW (fan only), the offset grows by one device step per tick, up to the device limit.
        When the compressor runs, or the room reaches the threshold, it shrinks by one step per tick.
        """
        mode = "heat" if heat else "cool"
        step = self._device_step(climate) or 1.0
        limit = _attr_float(climate, "max_temp" if heat else "min_temp")
        max_drop = abs(limit - target_c) if limit is not None else REGULATION_MAX_DROP_K
        threshold = self._heat_on_below_c if heat else self._cool_above_c
        needs_more = room_c < threshold if heat else room_c > threshold
        power = self._ac_power_w()
        if power is None:
            return self._regulation_drop_k
        compressor_runs = power / 1000.0 >= RUNNING_MIN_KW
        fresh = self._regulation_stepped_s is None or now - self._regulation_stepped_s >= 0.9 * SAMPLE_INTERVAL_S
        if fresh:
            held_min = self._since_min(climate, self._state_changed_at, now) if climate.state == mode else 0.0
            if climate.state == mode and held_min >= REGULATION_MIN_HOLD_MIN and needs_more and not compressor_runs:
                self._regulation_drop_k = min(self._regulation_drop_k + step, max_drop)
                self._regulation_stepped_s = now
            elif self._regulation_drop_k > 0.0 and (compressor_runs or not needs_more):
                self._regulation_drop_k = max(self._regulation_drop_k - step, 0.0)
                self._regulation_stepped_s = now
        return self._regulation_drop_k

    def _ac_heat_wanted(self, action: Action, result: dict[str, Any], room_c: float, window_paused: bool) -> bool:
        """True when the AC heats this tick.

        The AC heats only when the heat source is the AC, no heater switch is set and someone is home.
        The ac_heat option, or a heat plan whose first step says heat (mpc_heat_plan), allows the start.
        A stalled sensor, a paused window and a closed heating window (outdoor limit) also stop it.
        """
        plan = result.get("mpc_heat_steps")
        plan_heat = bool(self.entry.data.get(CONF_MPC_HEAT_PLAN, False) and plan and plan[0])
        return (
            action.mode is Mode.OFF
            and not self._user_off
            and self.heating_permitted
            and not self.dry_mode  # the AC dries in dry mode, so it cannot heat at the same time
            and (bool(self.entry.data.get(CONF_AC_HEAT, False)) or plan_heat)
            and result.get("heat_source") == "ac"
            and self._heater_switch() is None
            and self._someone_home
            and bool(result.get("heating_allowed", True))
            and not self._stall_blocks(result)
            and not window_paused
            and room_c < self._heat_on_below_c
        )

    async def _drive_ac_heat(self, climate_id: str, climate: State, now: float) -> bool:
        """Heat with the AC: hvac_mode heat, then the heat setpoint. A heat start after the AC stopped waits for the restart delay.

        Returns True when the restart delay or the compressor group holds the start.
        """
        modes = climate.attributes.get("hvac_modes")
        if modes is not None and "heat" not in modes:
            # The device has no heat mode, so the AC cannot heat. Do not send a mode it does not list.
            return False
        if climate.state != "heat":
            wait = self._restart_wait(climate.state in ("cool", "heat", "dry"), climate, self._state_changed_at, now)
            if wait > 0:
                self._hold_start(wait, None)
                return True
            # A heat start is a compressor start too, so it waits for the group clock.
            if not self._group_start_ok(now):
                return True
            self._record_group_start(now)
            await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate_id, "hvac_mode": "heat"}, blocking=True)
            self._state_changed_at = now
            self._last_sent_min = now / 60.0
            self._last_written_mode = "heat"
        heat_setpoint = self._regulated_setpoint(climate, self._heat_on_below_c, self._rooms_median(), now, heat=True)
        await self._write_setpoint(climate_id, climate, heat_setpoint, now)
        return False

    async def _drive_coil_dry(self, climate_id: str, climate: State, now: float) -> bool:
        """Run the AC in dry mode for 20 minutes after cooling stops. Returns True when it handled the cycle.

        Called on the control path after the lock and inhibit checks, and only when the AC lists a dry mode.
        Off by default: the AC's dry mode is a dehumidifier that produces a lot of water, so it runs only
        when the user sets the coil_dry option.
        """
        if not self.entry.data.get("coil_dry", False):
            return False
        now_cooling = climate.state == "cool"
        modes = climate.attributes.get("hvac_modes")
        has_dry = modes is None or "dry" in modes
        handled = False
        if self.entry.data.get(CONF_CONTROL) and has_dry:
            now_min = now / 60.0
            drying = coil_dry_due(self._was_cooling, now_cooling, self._dry_started_min, now_min)
            if drying:
                if self._dry_started_min is None:
                    self._dry_started_min = now_min
                if climate.state != "dry":
                    # A dry start runs the compressor, so it waits for the group clock like a cool start.
                    if not self._group_start_ok(now):
                        self._dry_started_min = None
                        self._was_cooling = now_cooling
                        return False
                    self._record_group_start(now)
                    self._coil_dry_sent = True
                    await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate_id, "hvac_mode": "dry"}, blocking=True)
                handled = True
            elif self._dry_started_min is not None:
                self._dry_started_min = None
                if climate.state == "dry":
                    await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate_id, "hvac_mode": "off"}, blocking=True)
                    self._coil_dry_sent = False
                    handled = True
        self._was_cooling = now_cooling
        return handled

    async def _drive_second_ac(self, action: Any, result: dict[str, Any], now: float, first: State | None, stalled: bool) -> None:
        """Send the first AC's cool or off decision to the optional second AC. Called only on the control path."""
        result["second_ac"] = "idle"
        climate_2_id = self.entry.data.get(CONF_CLIMATE_2)
        if not climate_2_id:
            return
        second = self.hass.states.get(climate_2_id)
        if second is None or second.state in ("unavailable", "unknown"):
            return
        if action.mode is Mode.COOL:
            if second.state != "cool":
                # The first AC is in the queue ahead of the second while it is about to start cooling.
                wanting = ["climate_2"] if first is not None and first.state == "cool" else ["climate_1", "climate_2"]
                if stagger_allowed("climate_2", wanting, self._last_start_min, now / 60.0) and self._group_start_ok(now):
                    wait = self._second_restart_wait(second, now)
                    if wait > 0:
                        self._hold_start(wait, "second AC")
                        result["second_ac"] = "waiting for restart delay"
                        return
                    self._record_group_start(now)
                    self._second_ac_commanded_on = True
                    await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate_2_id, "hvac_mode": "cool"}, blocking=True)
                    self._last_start_min["climate_2"] = now / 60.0
                    self._second_changed_at = now
                    result["second_ac"] = "driven"
                else:
                    result["second_ac"] = "waiting to stagger"
            current = second.attributes.get("temperature")
            if current is None or float(current) != float(action.setpoint_c):
                await self.hass.services.async_call(
                    "climate", "set_temperature", {"entity_id": climate_2_id, "temperature": action.setpoint_c}, blocking=True
                )
                result["second_ac"] = "driven"
        elif action.mode is Mode.OFF and second.state != "off":
            await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate_2_id, "hvac_mode": "off"}, blocking=True)
            self._second_ac_commanded_on = False
            self._second_changed_at = now
            result["second_ac"] = "driven"

    def _compressor_group(self) -> str | None:
        """The compressor group name, or None when the entry has no group. A blank name counts as no group."""
        name = str(self.entry.data.get(CONF_COMPRESSOR_GROUP) or "").strip()
        return name or None

    def _group_start_ok(self, now: float) -> bool:
        """True when no AC in this entry's compressor group started in the last 5 minutes. Always True without a group."""
        group = self._compressor_group()
        if group is None:
            return True
        clock = self.hass.data.setdefault(DOMAIN, {}).setdefault("compressor_clock", {})
        return group_start_allowed(clock.get(group), now / 60.0)

    def _publish_cooling(self, result: dict[str, Any]) -> None:
        """Record whether this entry's AC is cooling, and set result["house_cooling"] from the other entries in scope.

        Runs on every tick, with or without control, so an observe-only entry still reports its AC.
        """
        climate = self.hass.states.get(self._ac_id)
        group = self._compressor_group()
        registry = self.hass.data.setdefault(DOMAIN, {}).setdefault("cooling", {})
        registry[self.entry.entry_id] = (group, (climate is not None and climate.state == "cool") or self._fleet_cooling())
        result["house_cooling"] = house_cooling(registry, self.entry.entry_id, group)

    def forget_cooling(self) -> None:
        """Drop this entry from the cooling registry on unload, so a removed AC cannot keep another heater off."""
        self.hass.data.get(DOMAIN, {}).get("cooling", {}).pop(self.entry.entry_id, None)

    def _record_group_start(self, now: float) -> None:
        """Set the group clock to this start. Call it before the service call, with no await between check and record."""
        group = self._compressor_group()
        if group is not None:
            self.hass.data.setdefault(DOMAIN, {}).setdefault("compressor_clock", {})[group] = now / 60.0

    def _second_restart_wait(self, second: State, now: float) -> float:
        """Minutes the second AC must still wait before it may start. It uses the restart delay of the first AC."""
        return self._restart_wait(second.state in ("cool", "heat", "dry"), second, self._second_changed_at, now)

    def _restart_wait(self, running: bool, state: State | None, own_changed_at: float | None, now: float) -> float:
        """Minutes before a device that is off may start again. 0 when it runs, when the delay is disabled or when it has passed."""
        if running:
            return 0.0
        raw = self.entry.data.get(CONF_MIN_OFF_MIN)
        minutes = 3.0 if raw is None or raw == "" else float(raw)
        return restart_wait_min(self._since_min(state, own_changed_at, now), minutes)

    def _changeover_holds_cooling(self, now: float, room_c: float) -> bool:
        """True when auto mode must not cool yet: the heater runs or ran within AUTO_CHANGEOVER_MIN.

        A heater overshoots its target by a few tenths. Without this wait the AC would cool that heat away and the two
        would take turns. A room AUTO_CHANGEOVER_OVERRIDE_K above the cool threshold is a heat load and cools at once.
        """
        if self.user_mode != "auto" or room_c > self._cool_above_c + AUTO_CHANGEOVER_OVERRIDE_K:
            return False
        if self._heater_was_on:
            return True
        return self._heater_stopped_min is not None and now / 60.0 - self._heater_stopped_min < AUTO_CHANGEOVER_MIN

    def _outdoor_cool_limit_applies(self) -> bool:
        """True when the 16 C outdoor cooling limit applies: auto mode in a room that can cool with outdoor air.

        Below 16 C outdoors a window actuator or a whole-house fan cools the room for less than the AC. A room with
        neither has only the AC, so the AC cools. Once free cooling has failed to hold the room (_free_cooling_gave_up)
        the AC cools too. Cool mode is the user's order to cool and has no outdoor limit.
        """
        data = self.entry.data
        free_air = bool(data.get(CONF_WINDOW_ACTUATOR) or data.get(CONF_WHOLE_HOUSE_FAN))
        return self.user_mode == "auto" and free_air and not self._free_cooling_gave_up

    def _hold_start(self, wait_min: float, name: str | None) -> None:
        """Record a start that the restart delay holds this cycle. `name` labels a device other than the first AC."""
        self._pending_waits.append((wait_min, name))

    def _publish_pending(self, result: dict[str, Any]) -> None:
        """Publish the soonest held start as `pending` and schedule a re-check when its delay ends.

        With no held start, `pending` names the gate that keeps a wanted heater off, or is None.
        """
        if not self._pending_waits:
            result["pending"] = self._heat_hold_text(result)
            return
        wait_min, name = min(self._pending_waits, key=lambda held: held[0])
        text = f"start in {max(1, math.ceil(wait_min - 1e-9))} min: restart delay"
        result["pending"] = text if name is None else f"{text} ({name})"
        self._recheck_cancel = async_call_later(self.hass, wait_min * 60.0 + 1.0, self._async_recheck)

    async def _async_recheck(self, _when: datetime) -> None:
        """The restart delay ended. Run a cycle now instead of waiting for the next tick."""
        self._recheck_cancel = None
        await self.async_refresh()

    def _cancel_recheck(self) -> None:
        if self._recheck_cancel is not None:
            self._recheck_cancel()
            self._recheck_cancel = None

    async def _user_off_stop(self, now: float, result: dict[str, Any]) -> None:
        """Send off to the first AC, the second AC, the heater switch and every fleet device. Nothing delays it.

        Called while the user holds the room off. A device that is already off, unavailable or unknown gets no command.
        The lock is checked by the caller and by set_user_off.
        """
        data = self.entry.data
        climate_id = self._ac_id
        for entity_id in (climate_id, data.get(CONF_CLIMATE_2)):
            state = self.hass.states.get(entity_id) if entity_id else None
            if state is None or state.state in ("off", "unavailable", "unknown"):
                continue
            await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": "off"}, blocking=True)
            if entity_id == climate_id:
                self._last_sent_min = now / 60.0
                self._last_written_mode = "off"
                self._state_changed_at = now
            else:
                self._second_ac_commanded_on = False
                self._second_changed_at = now
        self._coil_dry_sent = False
        self._was_cooling = False
        self._dry_started_min = None
        await self._fleet_all_off(now, everything=True)
        # The heater switch: the logical state is off, and the switch gets its off command with the configured polarity.
        self._cancel_pwm_timers()
        self._pwm_on_now = False
        self._pwm_window_start_s = None
        await self._drive_heater_switch()
        result["action"] = "off: user turned the room off"
