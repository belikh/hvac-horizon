"""AC control: the per-tick AC step, setpoint and mode writes, AC heat, coil dry, the second AC and compressor-group limits."""

from __future__ import annotations

import statistics
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.storage import Store

from custom_components.hvac_horizon.decide import Action, MPC_DEADBAND_C, Mode, RoomState, Settings, decide
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
    CONF_MIN_ON_MIN,
    CONF_MPC_HEAT_PLAN,
    CONF_NIGHT_PLAN_ACT,
    CONF_OUTDOOR,
    CONF_PM25,
    CONF_ROOM_HUMIDITY,
    CONF_WHOLE_HOUSE_FAN,
    CONF_WINDOW,
    CONF_WINDOW_DELAY_MIN,
    DEFAULT_WINDOW_DELAY_MIN,
    DOMAIN,
    FAN_PWM_WINDOW_MIN,
    INHIBIT_MIN,
    KEEP_ALIVE_MIN,
    PEER_SETPOINT_STEP_K,
)
from custom_components.hvac_horizon.features import (
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
    minimum_time_ok,
    mpc_idle,
    pwm_on,
    round_to_step,
    stagger_allowed,
    ventilation_rate_from_co2,
    whole_house_fan_allowed,
    window_open_pause,
)
from custom_components.hvac_horizon.coordinator_common import _float


class AcMixin:
    # Instance attributes set in HorizonCoordinator.__init__ and used here.
    _ac_cooling_tick: bool
    _coil_dry_sent: bool
    _cool_above_c: float
    _dry_started_min: float | None
    _heat_on_below_c: float
    _inhibit_until_min: float | None
    _inhibited_for_setpoint: float | None
    _inputs_seen: bool
    _last_sent_min: float | None
    _last_start_min: dict[str, float]
    _last_written_mode: str | None
    _last_written_setpoint: float | None
    _locked: bool
    _peer_setpoint_original: float | None
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
        """Send one climate action per cycle. Runs only when the control option is on."""
        climate_id = self.entry.data[CONF_CLIMATE]
        first_ac = self.hass.states.get(climate_id)
        self._ac_cooling_tick = (first_ac is not None and first_ac.state == "cool") or self._fleet_cooling()
        if not lock_allows_control(self._locked):
            # The lock blocks normal commands. A stall, an open window or an away house still stops the AC.
            result["action"] = "locked"
            await self._locked_safety_stop(now, result, climate_id, first_ac)
            return
        now_min = now / 60.0
        if not self._inputs_seen and not (self._stall_blocks(result) or self._no_room_data_stop(now)):
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
        if inhibit_on and not stalled:
            result["action"] = "inhibited after a manual change"
            return
        # Keep-alive: resend the last setpoint when the device has not been written to for KEEP_ALIVE_MIN.
        keep_alive_climate = self.hass.states.get(climate_id)
        if (
            not inhibit_on
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
        outdoor = _float(self.hass.states.get(self.entry.data[CONF_OUTDOOR]))
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
                outdoor_c=outdoor,
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
        if self._user_off:
            action = Action(Mode.OFF, None, "user turned the room off")
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
            dehumidifying=self._switch_on(self.entry.data.get(CONF_DEHUMIDIFIER)) or dry_running,
            humidifying=self._switch_on(self.entry.data.get(CONF_HUMIDIFIER)),
        )
        result["ventilation"] = ventilation.reason
        result["humidity"] = humidity.reason
        # A CO2 rate between 0 and 1 runs the fan for that share of each PWM window. PM2.5 still forces it on.
        # At 0 or 1 the fan follows the on-off rule above.
        co2_rate = ventilation_rate_from_co2(_float(self._optional(CONF_CO2)))
        fan_on = ventilation.run
        if 0.0 < co2_rate < 1.0:
            pm25_on = air_quality_ventilation(None, _float(self._optional(CONF_PM25)), running=fan_running).run
            fan_on = pwm_on(co2_rate * 100.0, FAN_PWM_WINDOW_MIN, now / 60.0) or pm25_on
        # The economizer fan_assist phase runs the fan too, when the economizer_fan option is on and the gates pass.
        house_home = effective_home(True, house_mode_from_store(self.hass.data.get(DOMAIN, {})))
        if fan_assist_on(result.get("economizer"), bool(self.entry.data.get(CONF_ECONOMIZER_FAN, False)), lock_allows_control(self._locked), house_home, stalled):
            fan_on = True
        started = self._inputs_seen  # no switch starts before the first valid room and outdoor readings
        await self._drive_switch(self.entry.data.get(CONF_FAN), fan_on and started, track=True)
        await self._drive_switch(self.entry.data.get(CONF_DEHUMIDIFIER), humidity.action == "dehumidify" and started, track=True)
        await self._drive_switch(self.entry.data.get(CONF_HUMIDIFIER), humidity.action == "humidify" and started, track=True)
        heat_ok = result.get("heating_allowed", True)
        await self._drive_switch(self.entry.data.get(CONF_BOILER), bool(result.get("boiler_demand")) and heat_ok and started, track=True)
        outdoor_now = _float(self.hass.states.get(self.entry.data[CONF_OUTDOOR]))
        rooms_wh = self._rooms()
        fan_ok = whole_house_fan_allowed(
            bool(self.entry.data.get(CONF_HOUSE_SEALED, False)),
            climate is not None and climate.state == "cool",
            outdoor_now,
            statistics.median(rooms_wh) if rooms_wh else 0.0,
            fan_on=self._switch_on(self.entry.data.get(CONF_WHOLE_HOUSE_FAN)),
        )
        result["whole_house_fan"] = fan_ok
        await self._drive_switch(self.entry.data.get(CONF_WHOLE_HOUSE_FAN), fan_ok and started, track=True)
        # The first AC's block below returns early on several paths, so the second AC is driven before it.
        if any(d.entity_id for d in self._fleet()):
            # Fleet devices with an entity replace the single-AC path: the dispatch drives every cooler and heater of the room.
            await self._drive_fleet(now, result, action, stalled)
            return
        await self._drive_second_ac(action, result, now, climate)
        if climate is None or climate.state in ("unavailable", "unknown"):
            return
        self._check_mode(climate.state)
        if rooms and not mpc_idled and self._ac_heat_wanted(action, result, statistics.median(rooms), window_open_pause(window_open_min, window_delay_min)):
            result["action"] = "heat: room below heat threshold"
            if await self._drive_ac_heat(climate_id, climate, now):
                result["action"] = "heat: waiting for minimum time"
            return
        # Minimum on and off times: do not change state until the current state has lasted long enough.
        # Heat counts as running, so stopping heat, or switching heat to cool, waits for the minimum on time.
        wanted_mode = "cool" if action.mode is Mode.COOL else "off"
        if wanted_mode == "cool" and climate.state != "cool" and not self._group_start_ok(now):
            result["action"] = "cool: waiting for compressor group"
            return
        running = climate.state in ("cool", "heat")
        if climate.state != wanted_mode and (running or wanted_mode == "cool"):
            since_min = self._since_min(climate, self._state_changed_at, now)
            if not minimum_time_ok(running, since_min, self.entry.data.get(CONF_MIN_ON_MIN, 5.0), self.entry.data.get(CONF_MIN_OFF_MIN, 3.0)):
                result["action"] = f"{action.mode.value}: waiting for minimum time"
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
            # A stall means the AC goes off. Dry mode would keep it running, so it is skipped then.
            if not stalled and await self._drive_coil_dry(climate_id, climate, now):
                return
            # No cooling needed. If the room is too humid and the AC has a dry mode, dry the room with it.
            # An AC already in dry mode stays there while the humidity decision (with its deadband) still says dehumidify.
            if (
                not stalled
                and self.entry.data.get(CONF_DRY_FOR_HUMIDITY, False)
                and humidity.action == "dehumidify"
                and not self.entry.data.get(CONF_DEHUMIDIFIER)
                and climate.state == "dry"
                and self._last_written_mode == "dry"
                and not self._coil_dry_sent
            ):
                return
            if (
                not stalled
                and self.entry.data.get(CONF_DRY_FOR_HUMIDITY, False)
                and humidity.action == "dehumidify"
                and not self.entry.data.get(CONF_DEHUMIDIFIER)
                and climate.state != "dry"
                and self._group_start_ok(now)
            ):
                # A dry start runs the compressor, so it waits for the group clock like a cool start.
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
        await self._write_setpoint(climate_id, climate, cool_setpoint, now)
        if climate.state == "cool" and rooms:
            # The fan follows the room gap while the AC was already cooling before this cycle.
            if self.entry.data.get(CONF_AUTO_FAN, False):
                await self._write_fan_mode(climate_id, climate, statistics.median(rooms) - self._cool_above_c)

    async def _write_setpoint(self, climate_id: str, climate: State, target_c: float, now: float) -> None:
        """Send `target_c` to the AC when it differs from the device setpoint. Rounds to the device's step when it reports one."""
        current = climate.attributes.get("temperature")
        # Round to the device's own step only when it reports one. A device without a step gets the raw setpoint.
        raw_step = climate.attributes.get("target_temp_step")
        try:
            step = float(raw_step) if raw_step is not None else 0.0
        except (TypeError, ValueError):
            step = 0.0
        setpoint = round_to_step(target_c, step) if step > 0 else float(target_c)
        if current is None or float(current) != setpoint:
            await self.hass.services.async_call(
                "climate", "set_temperature", {"entity_id": climate_id, "temperature": setpoint}, blocking=True
            )
            self._last_written_setpoint = setpoint
            self._last_sent_min = now / 60.0
            self._inhibited_for_setpoint = None  # our write answers the manual change

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
        """Heat with the AC: hvac_mode heat, then the heat setpoint. Starting heat waits for the minimum time.

        Returns True when the minimum time holds the start.
        """
        modes = climate.attributes.get("hvac_modes")
        if modes is not None and "heat" not in modes:
            # The device has no heat mode, so the AC cannot heat. Do not send a mode it does not list.
            return False
        if climate.state != "heat":
            since_min = self._since_min(climate, self._state_changed_at, now)
            running = climate.state in ("cool", "heat")
            if not minimum_time_ok(running, since_min, self.entry.data.get(CONF_MIN_ON_MIN, 5.0), self.entry.data.get(CONF_MIN_OFF_MIN, 3.0)):
                return True
            # A heat start is a compressor start too, so it waits for the group clock.
            if not self._group_start_ok(now):
                return True
            self._record_group_start(now)
            await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate_id, "hvac_mode": "heat"}, blocking=True)
            self._state_changed_at = now
            self._last_sent_min = now / 60.0
            self._last_written_mode = "heat"
        await self._write_setpoint(climate_id, climate, self._heat_on_below_c, now)
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

    async def _drive_second_ac(self, action: Any, result: dict[str, Any], now: float, first: State | None) -> None:
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
                    if not self._second_minimum_ok(second.state, now):
                        result["second_ac"] = "waiting for minimum time"
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
            if not self._second_minimum_ok(second.state, now):
                result["second_ac"] = "waiting for minimum time"
                return
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
        climate = self.hass.states.get(self.entry.data[CONF_CLIMATE])
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

    def _second_minimum_ok(self, state: str, now: float) -> bool:
        """True when the second AC may change mode now. It uses the same minimum on and off times as the first AC."""
        since_min = self._since_min(self.hass.states.get(self.entry.data.get(CONF_CLIMATE_2)), self._second_changed_at, now)
        return minimum_time_ok(
            state in ("cool", "heat"),
            since_min,
            self.entry.data.get(CONF_MIN_ON_MIN, 5.0),
            self.entry.data.get(CONF_MIN_OFF_MIN, 3.0),
        )
