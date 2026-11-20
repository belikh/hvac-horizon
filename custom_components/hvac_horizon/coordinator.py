"""Samples the room every SAMPLE_INTERVAL_S, refits the model and publishes the forecast."""

from __future__ import annotations

import logging
import statistics
from dataclasses import asdict, fields, replace
from datetime import datetime, timedelta
from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.components import persistent_notification
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from custom_components.hvac_horizon.backfill import backtest, median_abs_error
from custom_components.hvac_horizon.collector import (
    Entry,
    entries_from_history,
    has_enough_cycles,
    price_slots,
    room_samples_from_entries,
    running_power_kw,
    samples_from_entries,
)
from custom_components.hvac_horizon.devices import COOL, fit_room
from custom_components.hvac_horizon.forecast import forecast, price_at
from custom_components.hvac_horizon.decide import Action, Mode, Preset, RoomState, Settings, cool_above_c, decide
from custom_components.hvac_horizon.features import (
    ScheduleBlock,
    weather_day_summary,
    room_devices,
    effective_home,
    classify_day,
    boiler_demand,
    outdoor_allows,
    economizer_phase,
    heat_source,
    mpc_plan,
    motion_preset,
    forecast_highs,
    natural_ventilation_window,
    daily_briefing,
    check_pin,
    heating_failed,
    heat_on_below,
    pwm_on,
    apply_polarity,
    clamp_output,
    switch_feedback,
    inhibited,
    mode_disagrees,
    hot_streak,
    lock_allows_control,
    learning_suggestion,
    suggestion_visible,
    band_action,
    comfort_band,
    compliance_score,
    drying_needed,
    fan_speed_from_gap,
    stale_price_fallback,
    ventilation_rate_from_co2,
    whole_house_fan_allowed,
    mould_risk,
    mould_target,
    surface_humidity,
    presence_mode,
    presence_sources,
    learned_solar_scale,
    sun_heat_gain,
    vacation_target,
    night_plan,
    safety_output,
    scheduled_preset,
    sensor_stalled,
    timed_preset_active,
    timed_preset_name,
    transition_message,
)
from custom_components.hvac_horizon.features import SENSOR_STALL_S, air_quality_ventilation, pid_gain, boost_output, coil_dry_due, comfort_energy_setpoint, valve_exercise_due, trv_offset, corrected_setpoint, residual_correction, cover_action, house_mode_from_store, humidity_action, keep_alive_due, minimum_time_ok, price_shifted_target, round_to_step, shed_plan, stagger_allowed, valve_opening_pct, window_open_pause
from custom_components.hvac_horizon.model import Fit, NotEnoughData, fit
from custom_components.hvac_horizon.control import precool_start_in_min
from custom_components.hvac_horizon.autotune import RelayTuner
from custom_components.hvac_horizon.pid import Pid
from custom_components.hvac_horizon.tpi import TpiFit, learn_tpi
from custom_components.hvac_horizon.investigator import ActionRecord, CallBudget, investigate
from custom_components.hvac_horizon.solar_model import clear_sky_irradiance, solar_elevation_azimuth, window_irradiance

from .const import (
    CONF_BOOST_C,
    CONF_COMFORT_ENERGY_WEIGHT,
    CONF_TRV_TEMPERATURE,
    TRV_HISTORY,
    CONF_RADIATORS,
    CONF_POWER_2,
    CONF_AI_BUDGET,
    CONF_AI_CONVERSATION,
    DEFAULT_RESIDUAL_PEAK_K,
    RESIDUAL_TAU_MIN,
    CONF_ECO_C,
    CONF_HEATER_KP,
    CONF_HEATER_KI,
    CONF_HEATER_KD,
    DEFAULT_HEATER_KP,
    DEFAULT_HEATER_KI,
    DEFAULT_HEATER_KD,
    DEFAULT_HEATER_DEADBAND_C,
    PWM_WINDOW_MIN,
    CONF_AWAY_C,
    CONF_MOTION,
    CONF_CLIMATE,
    CONF_CLIMATE_2,
    CONF_AWAY_DELAY_MIN,
    CONF_CO2,
    CONF_LOCK_PIN,
    CONF_HOUSE_SEALED,
    CONF_WHOLE_HOUSE_FAN,
    CONF_IRRADIANCE,
    CONF_WINDOW_AREA_M2,
    CONF_WINDOW_AZIMUTH,
    DEFAULT_WINDOW_AZIMUTH,
    DEFAULT_AWAY_DELAY_MIN,
    CONF_VACATION,
    CONF_VACATION_C,
    DEFAULT_VACATION_C,
    BOILER_BELOW_C,
    CONF_BOILER,
    CONF_DEHUMIDIFIER,
    CONF_HUMIDIFIER,
    CONF_FAN,
    CONF_HEATER_SWITCH,
    CONF_HEATER_INVERTED,
    CONF_OUTPUT_MIN,
    CONF_OUTPUT_MAX,
    CONF_SCHEDULE_BLOCKS,
    CONF_PRICE_CHEAP,
    CONF_PRICE_EXPENSIVE,
    DEFAULT_PRICE_CHEAP,
    DEFAULT_PRICE_EXPENSIVE,
    CONF_COMFORT_C,
    CONF_MIN_OFF_MIN,
    CONF_PM25,
    CONF_WEATHER,
    CONF_MIN_ON_MIN,
    CONF_CONTROL,
    CONF_COVER,
    CONF_SHED_LOADS,
    CONF_SHED_MAX_W,
    CONF_WHOLE_HOUSE_POWER,
    COVER_SOLAR_THRESHOLD_KW,
    CONF_VALVE,
    CONF_ENERGY,
    CONF_SCHEDULE,
    CONF_HUMIDITY,
    CONF_ROOM_HUMIDITY,
    CONF_OUTDOOR,
    CONF_POWER,
    CONF_PRICE,
    CONF_PRESENCE,
    CONF_ROOM_SENSORS,
    CONF_WINDOW,
    CONF_WINDOW_DELAY_MIN,
    DEFAULT_AWAY_C,
    DEFAULT_COMFORT_C,
    DEFAULT_WINDOW_DELAY_MIN,
    DOMAIN,
    FIT_EVERY_N_SAMPLES,
    INHIBIT_MIN,
    KEEP_ALIVE_MIN,
    MAX_ENTRIES,
    SAMPLE_INTERVAL_S,
    STORE_VERSION,
)

_LOGGER = logging.getLogger(__name__)
_ENTRY_FIELDS = {f.name for f in fields(Entry)}


def _float(state: State | None) -> float | None:
    if state is None or state.state in ("unknown", "unavailable", ""):
        return None
    try:
        return float(state.state)
    except ValueError:
        return None


def _optional_float(value: Any) -> float | None:
    """A stored number, or None when the key is absent or empty."""
    return None if value is None or value == "" else float(value)


def _entry_from_dict(raw: dict[str, Any]) -> Entry:
    """Load a stored record. Unknown keys are dropped, missing optional keys take their defaults."""
    clean = {k: v for k, v in raw.items() if k in _ENTRY_FIELDS}
    if "rooms_c" in clean:
        clean["rooms_c"] = tuple(clean["rooms_c"])
    return Entry(**clean)


MPC_STEP_MIN = 15.0
MPC_STEPS = 16  # 4 hours


class HorizonCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(seconds=SAMPLE_INTERVAL_S),
        )
        self.entry = entry
        self._store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._entries: list[Entry] = []
        self._fit: Fit | None = None
        self._fit_reason = "not enough data"
        self._samples_since_fit = 0
        self._window_open_since: float | None = None
        self._state_changed_at: float | None = None
        self._heater_on_min: float = 0.0
        self._heater_check_t: float | None = None
        self._heater_gated = False
        self._ac_cooling_tick = False
        self._heating_fail_reported = False
        self._baseline_k: float | None = None
        self._last_home_at: float | None = None
        self._last_cycle_t: float | None = None
        self._in_band_min: float = 0.0
        self._outdoor_history: list[float] = []
        self._last_good_price: float | None = None
        self._tuner: RelayTuner | None = None
        self._stall_reported = False
        # Time of the last cycle with a valid room reading. Starts at the first cycle, so a setup with no room data counts from then.
        self._last_room_data_s: float | None = None
        self._locked = False
        self._heater_pid: Pid | None = None
        self._heater_gains: dict[str, float] | None = None  # kp, ki, kd from the last relay test; stored, used for each gain field left at its default
        self._was_cooling: bool = False
        self._dry_started_min: float | None = None
        self._solar_pairs: list[tuple[float, float]] = []
        self._last_motion_at: float | None = None
        self._last_start_min: dict[str, float] = {}
        self._minutes_in_open: float = 0.0
        self._last_economizer_t: float | None = None
        self._valve_moved_min: float | None = None  # when the valve last moved; drives the anti-seize exercise
        self._heater_was_on = False
        self._action_log: list[ActionRecord] = []  # recent AC actions, newest last, for the investigator
        self._budget = CallBudget()  # model calls per day
        self._last_ai_mode: str | None = None
        self._ai_answer: str | None = None
        self._tpi: TpiFit | None = None  # per-state coefficients; the forecast uses them when present
        self._room_fit: dict[str, Any] | None = None  # two-device fit, published when a second power sensor is set
        self._heater_stopped_min: float | None = None  # when the heater last turned off, for residual heat
        self._trv_pairs: list[tuple[float, float]] = []  # (TRV reading, room reading) per cycle, for the offset
        self._forecast_highs: list[float] = []
        self._weather_checked_min: float | None = None
        self._pwm_output_pct: float = 0.0
        self._last_sent_min: float | None = None
        self._heater_commanded_on: bool | None = None
        # Switch entity and the state last sent to it. Unload uses these, not the current entry options.
        self._heater_switch_driven: str | None = None
        self._pwm_cancel: list[Callable[[], None]] = []
        self._settings: Settings | None = None
        self._someone_home = True
        self._preset_now: str | None = None
        self._cool_above_c: float = DEFAULT_COMFORT_C
        self._heat_on_below_c: float = DEFAULT_COMFORT_C - DEFAULT_HEATER_DEADBAND_C
        self._pwm_window_start_s: float | None = None
        self._pwm_on_now = False
        self._pwm_pct_now = 0.0
        self._dismissed_for_k: float | None = None
        self._timed_preset: str | None = None
        self._timed_started_min: float | None = None
        self._timed_duration_min: float = 0.0
        self._last_written_mode: str | None = None
        self._window_paused = False
        self._lock_notified = False
        self._inhibit_until_min: float | None = None
        self._inhibited_for_setpoint: float | None = None  # the manual value that last armed the inhibition
        self._last_written_setpoint: float | None = None
        self._autotune_result = None
        self._last_good_price_at: float | None = None
        self._total_min: float = 0.0
        self._heater_start_temp: float | None = None
        self._backtest_runs: list = []
        self._forecast_error_min: float | None = None

    async def async_backfill_from_recorder(self, days: float = 10.0) -> int:
        """Learn from history the recorder already holds: room, outdoor and power states on the 5-minute grid.

        Runs the database read in the recorder's executor. Returns the number of readings added. If the
        recorder is not available, nothing is added and the integration keeps learning from live samples.
        """
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.history import get_significant_states
        except ImportError:
            return 0

        now = dt_util.utcnow()
        start = now - timedelta(days=days)
        room_ids = list(self.entry.data[CONF_ROOM_SENSORS])
        outdoor_id = self.entry.data[CONF_OUTDOOR]
        power_id = self.entry.data[CONF_POWER]
        entity_ids = room_ids + [outdoor_id, power_id]
        try:
            states = await get_instance(self.hass).async_add_executor_job(
                lambda: get_significant_states(
                    self.hass, start, now, entity_ids,
                    include_start_time_state=True, significant_changes_only=False, minimal_response=False, no_attributes=True,
                )
            )
        except Exception:  # recorder not ready or not loaded
            _LOGGER.debug("recorder history not available for backfill", exc_info=True)
            return 0

        def points(entity: str) -> list[tuple[float, float]]:
            out = []
            for st in states.get(entity, []):
                try:
                    out.append((st.last_changed.timestamp(), float(st.state)))
                except (ValueError, TypeError, AttributeError):
                    continue
            return out

        rooms = [points(e) for e in room_ids]
        built = entries_from_history(
            rooms, points(outdoor_id), points(power_id),
            start.timestamp(), now.timestamp(),
        )
        if not built:
            return 0
        known = {round(e.t / SAMPLE_INTERVAL_S) for e in self._entries}
        added = [e for e in built if round(e.t / SAMPLE_INTERVAL_S) not in known]
        self._entries = sorted(self._entries + added, key=lambda e: e.t)[-MAX_ENTRIES:]
        self._refit()
        self._store.async_delay_save(self._save_payload, 0)
        return len(added)

    def start_power_tracking(self) -> None:
        """Integrate the power sensor between samples, so each sample gets a true interval mean."""
        now = dt_util.utcnow().timestamp()
        self._last_power_w = _float(self.hass.states.get(self.entry.data[CONF_POWER])) or 0.0
        self._last_t = now
        self._ws_total = 0.0
        self._ws_at_sample = 0.0
        self._t_at_sample = now
        entity = self.entry.data[CONF_POWER]

        @callback
        def _changed(event) -> None:
            new_value = _float(event.data.get("new_state"))
            self._accumulate(dt_util.utcnow().timestamp())
            if new_value is not None:
                self._last_power_w = new_value

        self.entry.async_on_unload(async_track_state_change_event(self.hass, [entity], _changed))

    def _accumulate(self, now: float) -> None:
        self._ws_total += self._last_power_w * (now - self._last_t)
        self._last_t = now

    def _take_interval_mean(self, now: float) -> float | None:
        """Mean power in watts since the previous sample, or None when no time has passed."""
        self._accumulate(now)
        dt = now - self._t_at_sample
        if dt <= 0:
            return None
        mean = (self._ws_total - self._ws_at_sample) / dt
        self._ws_at_sample = self._ws_total
        self._t_at_sample = now
        return mean

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self._entries = [_entry_from_dict(e) for e in data.get("entries", [])]
        self._heater_gains = data.get("heater_gains")
        self._refit()

    def what_if(self, room_c: float, target_c: float, outdoor_c: float, power_kw: float) -> dict[str, Any]:
        """Forecast minutes, energy and cost for given conditions, using the current fit and price slots."""
        active = self._tpi.to_fit() if self._tpi is not None else self._fit
        if active is None:
            return {"status": "learning", "reason": self._fit_reason}
        f = forecast(active, room_c, target_c, outdoor_c, power_kw, self._price_forecast(dt_util.utcnow().timestamp()))
        return {
            "status": "reachable" if f.reachable else "unreachable",
            "minutes": None if f.minutes is None else round(f.minutes, 1),
            "energy_kwh": None if f.energy_kwh is None else round(f.energy_kwh, 3),
            "cost": None if f.cost is None else round(f.cost, 3),
            "reason": f.reason,
        }

    def start_timed_preset(self, preset: str, minutes: float, now_min: float) -> None:
        """Hold `preset` for `minutes`, then fall back to the normal preset."""
        self._timed_preset = preset
        self._timed_started_min = now_min
        self._timed_duration_min = minutes

    def _effective_preset(self, now_min: float, normal: str) -> str:
        active = timed_preset_active(self._timed_started_min, now_min, self._timed_duration_min)
        return timed_preset_name(active, self._timed_preset, normal)

    def set_lock(self, locked: bool, pin: str | None) -> bool:
        """Lock or unlock control. Unlocking needs the configured PIN. Returns True when the lock changed."""
        configured = self.entry.data.get(CONF_LOCK_PIN)
        if not locked:
            if not check_pin(pin, configured):
                return False
        if locked and not configured:
            return False
        self._locked = locked
        return True

    def reset_learning(self) -> None:
        """Forget the stored samples and the fit. The next cycles start learning again."""
        self._entries = []
        self._fit = None
        self._fit_reason = "learning reset"
        self._backtest_runs = []
        self._forecast_error_min = None
        self._samples_since_fit = 0
        self._store.async_delay_save(self._save_payload, 0)

    def export_samples(self) -> list[dict[str, Any]]:
        """All stored readings, for the export service."""
        return [asdict(e) for e in self._entries]

    def start_autotune(self, setpoint_c: float) -> None:
        """Begin a relay test at `setpoint_c`. The heater follows the test only while control is on and no gate is closed."""
        self._tuner = RelayTuner(setpoint_c=setpoint_c)
        self._autotune_result = None

    def _relay_tick(self, now: float, room_c: float | None, may_drive: bool) -> bool:
        """One relay test step. Returns the heater state to command.

        `may_drive` is False when control is off or a gate is closed. The test then records nothing and the
        heater stays off, because the room is not under relay control.
        """
        if self._tuner is None or room_c is None or not may_drive:
            return False
        want_on = self._tuner.step(room_c, now / 3600.0)
        if self._tuner.done:
            self._finish_relay()
            return False
        return want_on

    def _finish_relay(self) -> None:
        """End the test and keep its gains. The heater stays off this step and the PWM path resumes on the next."""
        result = self._tuner.result()
        self._tuner = None
        self._autotune_result = result
        if result is not None:
            self._heater_gains = {"kp": result.kp, "ki": result.ki, "kd": result.kd}
            if self._heater_pid is not None:
                self._heater_pid.set_gains(kp=result.kp, ki=result.ki, kd=result.kd)
            self._store.async_delay_save(self._save_payload, 0)

    def _save_payload(self) -> dict[str, Any]:
        return {"entries": [asdict(e) for e in self._entries], "heater_gains": self._heater_gains}

    async def async_shutdown_store(self) -> None:
        await self._store.async_save(self._save_payload())

    def _refit_room_model(self) -> None:
        """Fit one coefficient per AC from the readings, when a second power sensor is set. The forecast keeps the single-device fit."""
        self._room_fit = None
        if not (self.entry.data.get(CONF_CLIMATE_2) and self.entry.data.get(CONF_POWER_2)):
            return
        try:
            found = fit_room(room_samples_from_entries(self._entries), {"ac": COOL, "ac_2": COOL})
        except NotEnoughData as err:
            self._room_fit = {"reason": str(err)}
            return
        self._room_fit = {
            "k_per_h": round(found.k, 4),
            "g_k_per_h": round(found.g, 4),
            "ac_k_per_h_per_kw": round(found.coef["ac"], 4),
            "ac_2_k_per_h_per_kw": round(found.coef["ac_2"], 4),
            "samples": found.samples,
        }

    def _refit(self) -> None:
        samples = samples_from_entries(self._entries)
        self._refit_room_model()
        try:
            self._tpi = learn_tpi(samples)
        except NotEnoughData:
            self._tpi = None
        self._backtest_runs = []
        if not has_enough_cycles(samples):
            self._fit = None
            self._fit_reason = "need at least 3 hours of AC on and off time over 3 runs"
            return
        try:
            self._fit = fit(samples)
            self._fit_reason = "fitted"
        except NotEnoughData as err:
            self._fit = None
            self._fit_reason = str(err)
            return
        target = float(self.entry.data.get(CONF_COMFORT_C, DEFAULT_COMFORT_C))
        self._backtest_runs = backtest(samples, target_c=target, min_train=60, margin_c=1.0)
        self._forecast_error_min = median_abs_error(self._backtest_runs)

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

    def _heater_gate_closed(self, result: dict[str, Any]) -> bool:
        """True when a gate forces the heater off: window open, house away, heating not allowed, lock on or a stalled sensor."""
        window = self._optional(CONF_WINDOW)
        return (
            (window is not None and window.state == "on")
            or house_mode_from_store(self.hass.data.get(DOMAIN, {})) == "away"
            or not result.get("heating_allowed", True)
            or not lock_allows_control(self._locked)
            or bool(result["sensors_stalled"])
        )

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
        return (room_devices(self.entry.data)["heating"] or [None])[0]

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
        if driving and (self._heater_gated or self._ac_cooling_tick or gate_c is None or gate_c >= self._heat_on_below_c):
            pct = 0.0
        relay_running = self._tuner is not None
        if relay_running:
            # The relay test replaces the PWM output. The PWM timers stay cancelled until the test ends.
            self._cancel_pwm_timers()
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
        failed = self._heating_check(now, driving and pct > 0 and not relay_running, heater_on, room_c)
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

    async def _refresh_weather(self) -> None:
        weather_id = self.entry.data.get(CONF_WEATHER)
        if not weather_id:
            return
        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": weather_id, "type": "daily"},
                blocking=True,
                return_response=True,
            )
            self._forecast_highs = forecast_highs(response[weather_id]["forecast"])
        except Exception:  # a missing weather service must not break the cycle
            _LOGGER.debug("Weather forecast unavailable for %s", weather_id, exc_info=True)

    def _heatwave(self) -> bool:
        """True when the outdoor readings have reached 30 C on at least three cycles in a row.

        Uses the recent outdoor readings as the forecast until a weather entity is wired in.
        """
        if self._forecast_highs:
            return hot_streak(self._forecast_highs)
        outdoor = _float(self.hass.states.get(self.entry.data[CONF_OUTDOOR]))
        if outdoor is None:
            return False
        self._outdoor_history.append(outdoor)
        self._outdoor_history = self._outdoor_history[-24:]
        return hot_streak(self._outdoor_history)

    def _compliance(self, now: float, band_action_value: str | None) -> float | None:
        """Share of time in band since the coordinator started. Tracked from each cycle's band action."""
        if band_action_value is None:
            return None
        if self._last_cycle_t is not None:
            dt = (now - self._last_cycle_t) / 60.0
            self._total_min += dt
            if band_action_value == "none":
                self._in_band_min += dt
        self._last_cycle_t = now
        return compliance_score(self._in_band_min, self._total_min)

    def _mould_risk(self) -> bool:
        """Mould risk from the room humidity and the outdoor temperature as a cold-surface proxy.

        Needs the room humidity sensor. Without it the risk is reported as not present.
        """
        room_rh = _float(self._optional(CONF_ROOM_HUMIDITY))
        rooms = self._rooms()
        outdoor = _float(self.hass.states.get(self.entry.data[CONF_OUTDOOR]))
        if room_rh is None or not rooms or outdoor is None:
            return False
        surface = surface_humidity(room_rh, statistics.median(rooms), outdoor)
        return mould_risk(surface)

    def _solar_gain(self) -> float | None:
        """Solar gain from the optional irradiance sensor and window area. None when either is not configured."""
        irradiance = _float(self._optional(CONF_IRRADIANCE))
        area = self.entry.data.get(CONF_WINDOW_AREA_M2)
        if irradiance is None or area is None:
            return None
        return round(sun_heat_gain(irradiance, float(area)), 3)

    def _modelled_solar_kw(self) -> float | None:
        """Clear-sky gain in kW on the window from the sun position at the house location. None without a window area."""
        area = self.entry.data.get(CONF_WINDOW_AREA_M2)
        if area is None:
            return None
        utc = dt_util.utcnow()
        elevation, azimuth = solar_elevation_azimuth(
            self.hass.config.latitude, self.hass.config.longitude,
            utc.hour + utc.minute / 60.0, utc.timetuple().tm_yday,
        )
        window_az = float(self.entry.data.get(CONF_WINDOW_AZIMUTH, DEFAULT_WINDOW_AZIMUTH))
        irradiance = window_irradiance(clear_sky_irradiance(elevation), elevation, azimuth, window_az)
        return sun_heat_gain(irradiance, float(area))

    def _occupancy_mode(self, now: float) -> str:
        """Home or away from the optional presence sources. Tracks the last time any source was home."""
        states = [self.hass.states.get(entity) for entity in presence_sources(self.entry.data.get(CONF_PRESENCE))]
        home = [s.state in ("on", "home") for s in states if s is not None]
        if any(home) or self._last_home_at is None:
            # The away clock starts at the first cycle when no source has been home yet, so an away house goes away.
            self._last_home_at = now
        since = (now - self._last_home_at) / 60.0
        return presence_mode(home, float(self.entry.data.get(CONF_AWAY_DELAY_MIN, DEFAULT_AWAY_DELAY_MIN)), since)

    def _learning_suggestion(self) -> str | None:
        """Compare the fitted heat gain with the current model gain (the first fit, kept per run)."""
        if self._fit is None:
            return None
        if self._baseline_k is None:
            self._baseline_k = self._fit.k
            return None
        return learning_suggestion(self._baseline_k, self._fit.k)

    def set_heater_output(self, pct: float) -> None:
        self._pwm_output_pct = clamp_output(
            pct,
            float(self.entry.data.get(CONF_OUTPUT_MIN, 0.0)),
            float(self.entry.data.get(CONF_OUTPUT_MAX, 100.0)),
        )

    def respond_suggestion(self, accept: bool) -> None:
        """Accept adopts the learned heat gain as the baseline; dismiss hides the suggestion until the gain drifts."""
        if accept:
            if self._fit is not None:
                self._baseline_k = self._fit.k
            self._dismissed_for_k = None
        elif self._fit is not None:
            self._dismissed_for_k = self._fit.k

    def _night_plan(self) -> str | None:
        """Overnight plan from the room and outdoor readings. Uses the current outdoor reading as the low until a forecast is wired in."""
        rooms = self._rooms()
        outdoor = _float(self.hass.states.get(self.entry.data[CONF_OUTDOOR]))
        if not rooms or outdoor is None:
            return None
        comfort = float(self.entry.data.get(CONF_COMFORT_C, DEFAULT_COMFORT_C))
        return night_plan(outdoor, statistics.median(rooms), comfort)

    def _window_advice(self) -> str | None:
        """Advice to open, close or hold windows from the outdoor reading and the room median. Publishes advice only."""
        rooms = self._rooms()
        outdoor = _float(self.hass.states.get(self.entry.data[CONF_OUTDOOR]))
        if not rooms or outdoor is None:
            return None
        return natural_ventilation_window(outdoor, statistics.median(rooms))

    def _day_type(self) -> str | None:
        """Day class from the outdoor sensor's reading. Uses the current reading until a forecast is wired in."""
        if self._forecast_highs:
            return classify_day(self._forecast_highs[0]).value
        outdoor = _float(self.hass.states.get(self.entry.data[CONF_OUTDOOR]))
        return None if outdoor is None else classify_day(outdoor).value

    async def _drive_switch(self, entity_id: str | None, on: bool) -> None:
        """Turn an optional switch on or off, only when it differs from its state."""
        if not entity_id:
            return
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return
        if (state.state == "on") == on:
            return
        service = "turn_on" if on else "turn_off"
        await self.hass.services.async_call("switch", service, {"entity_id": entity_id}, blocking=True)

    def _report_stalls(self, stalled: list[str]) -> None:
        """Repair issue and notification when sensors stall. Cleared when they recover."""
        issue_id = f"{self.entry.entry_id}_stalled"
        if stalled and not self._stall_reported:
            ir.async_create_issue(
                self.hass, DOMAIN, issue_id, is_fixable=False, severity=ir.IssueSeverity.WARNING,
                translation_key="sensors_stalled", translation_placeholders={"sensors": ", ".join(stalled)},
            )
            persistent_notification.async_create(
                self.hass, f"HVAC Horizon: sensors not updating: {', '.join(stalled)}", title="HVAC Horizon", notification_id=issue_id
            )
            self._stall_reported = True
        elif not stalled and self._stall_reported:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            persistent_notification.async_dismiss(self.hass, issue_id)
            self._stall_reported = False

    def _device_setpoint(self) -> float | None:
        """The setpoint the climate entity reports, or None when it reports none."""
        climate = self.hass.states.get(self.entry.data[CONF_CLIMATE])
        if climate is None or climate.attributes.get("temperature") is None:
            return None
        return float(climate.attributes["temperature"])

    def _external_change(self) -> bool:
        """True when the climate setpoint differs from the last one this integration wrote (someone changed it by hand)."""
        current = self._device_setpoint()
        if current is None or self._last_written_setpoint is None:
            return False
        return abs(current - self._last_written_setpoint) > 0.05

    def _check_mode(self, actual_mode: str | None) -> None:
        """Raise a repair when the AC mode differs from the last one written and the setpoint is unchanged."""
        issue_id = f"{self.entry.entry_id}_mode_mismatch"
        if mode_disagrees(self._last_written_mode, actual_mode, setpoint_changed=self._external_change()):
            ir.async_create_issue(
                self.hass, DOMAIN, issue_id, is_fixable=False, severity=ir.IssueSeverity.WARNING,
                translation_key="mode_mismatch",
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def _stalled_sensors(self, now: float) -> list[str]:
        """Room and outdoor sensors that have not reported recently. Published so the user can see them.

        Reads last_reported. Home Assistant keeps last_updated when a sensor repeats its value,
        so a steady reading would look stalled.
        """
        names = list(self.entry.data[CONF_ROOM_SENSORS]) + [self.entry.data[CONF_OUTDOOR]]
        stalled = []
        for name in names:
            state = self.hass.states.get(name)
            # A missing or unavailable sensor is not a stall. _rooms() and _read_inputs() already treat it as no reading.
            if state is None or state.state in ("unavailable", "unknown"):
                continue
            if sensor_stalled(state.last_reported.timestamp(), now):
                stalled.append(name)
        return stalled

    def _no_room_data_stop(self, now: float) -> bool:
        """True when no room has had a valid reading for SENSOR_STALL_S. The AC then stops."""
        return self._last_room_data_s is not None and now - self._last_room_data_s >= SENSOR_STALL_S

    def _rooms(self) -> list[float]:
        """Room readings for the median: the room sensors only.

        The AC's own temperature reading is whole degrees, so it is kept out of the median.
        """
        states = [self.hass.states.get(e) for e in self.entry.data[CONF_ROOM_SENSORS]]
        return [v for v in (_float(s) for s in states) if v is not None]

    def _optional(self, key: str) -> State | None:
        entity = self.entry.data.get(key)
        return self.hass.states.get(entity) if entity else None

    def _read_inputs(self, now: float) -> Entry | None:
        rooms = self._rooms()
        outdoor = _float(self.hass.states.get(self.entry.data[CONF_OUTDOOR]))
        power_w = _float(self.hass.states.get(self.entry.data[CONF_POWER]))
        if not rooms or outdoor is None or power_w is None:
            return None
        climate = self.hass.states.get(self.entry.data[CONF_CLIMATE])
        setpoint = climate.attributes.get("temperature") if climate else None
        price = _float(self._optional(CONF_PRICE))
        second_w = _float(self._optional(CONF_POWER_2)) if self.entry.data.get(CONF_CLIMATE_2) else None
        return Entry(
            t=now,
            room_c=statistics.median(rooms),
            outdoor_c=outdoor,
            power_w=power_w,
            power_2_w=second_w,
            setpoint_c=float(setpoint) if setpoint is not None else None,
            mode=climate.state if climate else None,
            rooms_c=tuple(rooms),
            humidity_pct=_float(self._optional(CONF_HUMIDITY)),
            price=price,
            energy_kwh=_float(self._optional(CONF_ENERGY)),
        )

    def _price_forecast(self, now: float) -> list:
        state = self._optional(CONF_PRICE)
        if state is None:
            return []
        pairs = []
        for item in state.attributes.get("forecast", []) or []:
            try:
                pairs.append((datetime.fromisoformat(item["time"]).timestamp(), float(item["value"])))
            except (KeyError, TypeError, ValueError):
                continue
        return price_slots(pairs, now)

    async def _async_update_data(self) -> dict[str, Any]:
        now = dt_util.utcnow().timestamp()
        now_min = now / 60.0
        if self._weather_checked_min is None or now_min - self._weather_checked_min >= 30:
            self._weather_checked_min = now_min
            await self._refresh_weather()
        reading = self._read_inputs(now)
        interval_mean = self._take_interval_mean(now)
        if reading is not None:
            reading = replace(reading, power_mean_w=interval_mean)
            self._entries.append(reading)
            self._entries = self._entries[-MAX_ENTRIES:]
            self._samples_since_fit += 1
            if self._samples_since_fit >= FIT_EVERY_N_SAMPLES or self._fit is None:
                self._refit()
                self._samples_since_fit = 0
            self._store.async_delay_save(self._save_payload, 900)  # write at most every 15 minutes
        result = self._forecast(now)
        result["sensors_stalled"] = self._stalled_sensors(now)
        self._report_stalls(result["sensors_stalled"])
        if self._rooms() or self._last_room_data_s is None:
            self._last_room_data_s = now
        result["external_change"] = self._external_change()
        result["day_type"] = self._day_type()
        result["forecast_days"] = weather_day_summary(self._forecast_highs) if self._forecast_highs else None
        result["night_plan"] = self._night_plan()
        result["learning_suggestion"] = suggestion_visible(
            self._learning_suggestion(),
            dismissed_for_k=self._dismissed_for_k,
            learned_k=self._fit.k if self._fit is not None else None,
        )
        vacation_on = self._optional(CONF_VACATION) is not None and self._optional(CONF_VACATION).state == "on"
        result["vacation"] = vacation_on
        result["occupancy"] = self._occupancy_mode(now)
        result["solar_gain_kw"] = self._solar_gain()
        modelled_kw = self._modelled_solar_kw()
        if modelled_kw is not None and result["solar_gain_kw"] is not None:
            self._solar_pairs.append((modelled_kw, result["solar_gain_kw"]))
            del self._solar_pairs[:-288]
        result["solar_scale"] = learned_solar_scale([m for m, _ in self._solar_pairs], [v for _, v in self._solar_pairs]) if self._solar_pairs else None
        result["window_advice"] = self._window_advice()
        outdoor_now = _float(self.hass.states.get(self.entry.data[CONF_OUTDOOR]))
        rooms_econ = self._rooms()
        if outdoor_now is not None and rooms_econ:
            phase = economizer_phase(
                outdoor_now,
                statistics.median(rooms_econ),
                float(self.entry.data.get(CONF_COMFORT_C, DEFAULT_COMFORT_C)),
                self._minutes_in_open,
            )
            if phase == "off":
                self._minutes_in_open = 0.0
            elif self._last_economizer_t is not None:
                self._minutes_in_open += (now - self._last_economizer_t) / 60.0
            self._last_economizer_t = now
            result["economizer"] = phase
            source = heat_source(outdoor_now, BOILER_BELOW_C)
            low_edge = float(self.entry.data.get(CONF_COMFORT_C, DEFAULT_COMFORT_C)) - 1.0
            result["heat_source"] = source
            result["boiler_demand"] = boiler_demand(statistics.median(rooms_econ), low_edge, source)
            result["heating_allowed"] = outdoor_allows("heat", outdoor_now)
        if self._fit is not None and outdoor_now is not None and rooms_econ:
            slots = self._price_forecast(now)
            if slots:
                step_prices: list[float] = []
                last = slots[0].price_per_kwh
                for step in range(MPC_STEPS):
                    found = price_at(slots, step * MPC_STEP_MIN)
                    last = last if found is None else found
                    step_prices.append(last)
                result["mpc_plan"] = [
                    bool(a)
                    for a in mpc_plan(
                        self._fit.k,
                        self._fit.c,
                        self._fit.g,
                        statistics.median(rooms_econ),
                        float(self.entry.data.get(CONF_COMFORT_C, DEFAULT_COMFORT_C)),
                        outdoor_now,
                        running_power_kw(self._entries, now) or 0.0,
                        step_prices,
                        step_min=MPC_STEP_MIN,
                    )
                ]
        # Motion preset is published only; it does not yet drive the setpoint preset.
        motion_state = self._optional(CONF_MOTION)
        motion_on: bool | None = None
        if motion_state is not None and motion_state.state in ("on", "off"):
            motion_on = motion_state.state == "on"
        if motion_on:
            self._last_motion_at = now
        minutes_since = (now - self._last_motion_at) / 60.0 if self._last_motion_at is not None else 1e9
        result["motion_preset"] = motion_preset(motion_on, minutes_since)
        low, high = comfort_band(float(self.entry.data.get(CONF_COMFORT_C, DEFAULT_COMFORT_C)))
        rooms_now = self._rooms()
        result["band_action"] = band_action(statistics.median(rooms_now), low, high) if rooms_now else None
        result["mould_risk"] = self._mould_risk()
        result["compliance_pct"] = self._compliance(now, band_action_value=result["band_action"])
        result["heatwave"] = self._heatwave()
        rooms_fan = self._rooms()
        target_fan = float(self.entry.data.get(CONF_COMFORT_C, DEFAULT_COMFORT_C))
        result["fan_speed"] = fan_speed_from_gap(statistics.median(rooms_fan), target_fan) if rooms_fan else None
        room_gap = (statistics.median(rooms_fan) - target_fan) if rooms_fan else 1.0
        result["drying_needed"] = drying_needed(_float(self._optional(CONF_ROOM_HUMIDITY)), cool_gap_c=room_gap)
        price_now = _float(self._optional(CONF_PRICE))
        if price_now is not None:
            self._last_good_price = price_now
            self._last_good_price_at = now
        age_min = 0.0 if self._last_good_price_at is None else (now - self._last_good_price_at) / 60.0
        result["price_used_per_kwh"] = stale_price_fallback(price_now, self._last_good_price, age_min)
        result["ventilation_rate"] = round(ventilation_rate_from_co2(_float(self._optional(CONF_CO2))), 3)
        self._plan_control(now, result)
        pid_rooms = self._rooms()
        pid_outdoor = _float(self.hass.states.get(self.entry.data[CONF_OUTDOOR]))
        heater_configured = bool(self.entry.data.get(CONF_CONTROL)) and bool(self._heater_switch())
        inhibit_active = inhibited(dt_util.utcnow().timestamp() / 60.0, self._inhibit_until_min)
        self._heater_gated = heater_configured and self._heater_gate_closed(result)
        if self._heater_gated:
            self._pwm_output_pct = 0.0
            if self._heater_pid is not None:
                self._heater_pid.reset()
        elif heater_configured and pid_rooms and pid_outdoor is not None and not inhibit_active:
            if self._heater_pid is None:
                tuned = self._heater_gains or {}
                self._heater_pid = Pid(
                    pid_gain(float(self.entry.data.get(CONF_HEATER_KP, DEFAULT_HEATER_KP)), DEFAULT_HEATER_KP, tuned.get("kp")),
                    pid_gain(float(self.entry.data.get(CONF_HEATER_KI, DEFAULT_HEATER_KI)), DEFAULT_HEATER_KI, tuned.get("ki")),
                    pid_gain(float(self.entry.data.get(CONF_HEATER_KD, DEFAULT_HEATER_KD)), DEFAULT_HEATER_KD, tuned.get("kd")),
                    ke=0.0,
                    out_min=0.0,
                    out_max=100.0,
                )
            pid_room = statistics.median(pid_rooms)
            climate_now = self.hass.states.get(self.entry.data[CONF_CLIMATE])
            # The heater output is forced to 0 in the heat band and while the AC cools. Hold the integral then,
            # so the positive error of that time does not wind it up.
            forced_off = pid_room >= self._heat_on_below_c or (climate_now is not None and climate_now.state == "cool")
            pid_out = self._heater_pid.update(
                self._cool_above_c, pid_room, SAMPLE_INTERVAL_S / 3600.0, outdoor=pid_outdoor, freeze_integral=forced_off
            )
            self.set_heater_output(boost_output(self._preset_now or "", pid_out))
            result["heater_pid_output"] = round(pid_out, 2)
        elif heater_configured and not inhibit_active:
            # The PID cannot run (no room or outdoor reading). A stale output must not keep the heater on.
            # Boost still heats: boost_output returns 100 in boost and 0 otherwise.
            self.set_heater_output(boost_output(self._preset_now or "", 0.0))
        valve_rooms = self._rooms()
        valve_opening = None
        if valve_rooms:
            comfort_target = float(self.entry.data.get(CONF_COMFORT_C, DEFAULT_COMFORT_C))
            room_for_valve = statistics.median(valve_rooms)
            trv_c = _float(self._optional(CONF_TRV_TEMPERATURE))
            if self.entry.data.get(CONF_VALVE) and trv_c is not None:
                self._trv_pairs = (self._trv_pairs + [(trv_c, room_for_valve)])[-TRV_HISTORY:]
            # With a valve and a TRV sensor, the valve command aims at the setpoint corrected by the learned offset.
            offset = trv_offset([t for t, _ in self._trv_pairs], [r for _, r in self._trv_pairs]) if self._trv_pairs else None
            if self.entry.data.get(CONF_VALVE):
                result["trv_offset_k"] = None if offset is None else round(offset, 2)
                comfort_target = corrected_setpoint(comfort_target, offset)
            valve_opening = valve_opening_pct(room_for_valve, comfort_target)
        result["valve_opening_pct"] = valve_opening
        try:
            cover_id = self.entry.data.get(CONF_COVER)
            solar = self._solar_gain()
            cover_state = self.hass.states.get(cover_id) if cover_id else None
            if solar is not None and cover_state is not None and cover_state.state not in ("unavailable", "unknown"):
                action = cover_action(solar, COVER_SOLAR_THRESHOLD_KW, cover_state.state == "open")
                result["cover_action"] = action
                await self._drive_cover(action)
            max_w = self.entry.data.get(CONF_SHED_MAX_W)
            house_id = self.entry.data.get(CONF_WHOLE_HOUSE_POWER)
            total_w = _float(self.hass.states.get(house_id)) if house_id else None
            if max_w and total_w is not None:
                loads: list[tuple[str, float, bool]] = []
                for spec in self.entry.data.get(CONF_SHED_LOADS) or []:
                    entity_id, _, watts_text = str(spec).partition("|")
                    try:
                        watts = float(watts_text)
                    except ValueError:
                        result["shed_skipped"] = f"malformed load: {spec}"
                        continue
                    load_state = self.hass.states.get(entity_id.strip())
                    loads.append((entity_id.strip(), watts, load_state is not None and load_state.state == "on"))
                to_shed = shed_plan(total_w, float(max_w), loads)
                result["shed"] = to_shed
                await self._drive_shed(to_shed)
            self._notify_transition("lock", "_lock_notified", self._locked, "HVAC Horizon control is locked.", "HVAC Horizon control is unlocked.")
            self._ac_cooling_tick = False
            if self.entry.data.get(CONF_CONTROL):
                await self._async_control(now, result)
        finally:
            await self._async_heater_step(now, result)
        await self._publish_investigation(now, result)
        heater_switch = (room_devices(self.entry.data)["heating"] or [None])[0]
        heater_state = self.hass.states.get(heater_switch) if heater_switch else None
        result["heater_feedback"] = switch_feedback(
            self._heater_commanded_on if heater_switch else None,
            heater_state.state if heater_state is not None else None,
        )
        result["safety_output_pct"] = safety_output(bool(result["sensors_stalled"]), 100.0, fallback_pct=0.0)
        result["autotune"] = None if self._autotune_result is None else {"kp": round(self._autotune_result.kp, 3), "ki": round(self._autotune_result.ki, 3), "kd": round(self._autotune_result.kd, 3)}
        if valve_opening is not None:
            await self._drive_valve(valve_opening)
        return result

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
                await self.hass.services.async_call("number", "set_value", {"entity_id": valve, "value": step}, blocking=True)
                self._valve_moved_min = now_min
            return
        await self.hass.services.async_call("number", "set_value", {"entity_id": valve, "value": opening}, blocking=True)
        self._valve_moved_min = now_min

    async def _drive_shed(self, to_shed: list[str]) -> None:
        """Switch off the planned loads. Runs only with control on, unlocked and not inhibited."""
        if not to_shed or not self.entry.data.get(CONF_CONTROL):
            return
        if not lock_allows_control(self._locked):
            return
        if inhibited(dt_util.utcnow().timestamp() / 60.0, self._inhibit_until_min):
            return
        for entity_id in to_shed:
            await self.hass.services.async_call("switch", "turn_off", {"entity_id": entity_id}, blocking=True)

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

    def _plan_control(self, now: float, result: dict[str, Any]) -> None:
        """Work out the preset and the one effective target for this tick.

        The AC cools above `_cool_above_c` (decide) and the heater PID aims at it. The heater heats below
        `_heat_on_below_c`, one deadband lower. Both come from the same settings, so they never disagree.
        """
        now_min = now / 60.0
        # Home when any presence source is home. Away only after every source has been away for the away delay.
        someone_home = self._occupancy_mode(now) == "home"
        someone_home = effective_home(someone_home, house_mode_from_store(self.hass.data.get(DOMAIN, {})))
        comfort = float(self.entry.data.get(CONF_COMFORT_C, DEFAULT_COMFORT_C))
        # Comfort-versus-energy weight: 0 keeps comfort, 1 moves the target to the eco setpoint. Off without an eco setpoint.
        energy_weight = float(self.entry.data.get(CONF_COMFORT_ENERGY_WEIGHT) or 0.0)
        eco_c = _optional_float(self.entry.data.get(CONF_ECO_C))
        if energy_weight > 0 and eco_c is not None:
            comfort = comfort_energy_setpoint(comfort, eco_c, min(max(energy_weight, 0.0), 1.0))
        price = _float(self._optional(CONF_PRICE))
        vacation_on = self._optional(CONF_VACATION) is not None and self._optional(CONF_VACATION).state == "on"
        target_c = vacation_target(
            vacation_on,
            float(self.entry.data.get(CONF_VACATION_C, DEFAULT_VACATION_C)),
            price_shifted_target(
                comfort,
                price,
                cheap_per_kwh=float(self.entry.data.get(CONF_PRICE_CHEAP, DEFAULT_PRICE_CHEAP)),
                expensive_per_kwh=float(self.entry.data.get(CONF_PRICE_EXPENSIVE, DEFAULT_PRICE_EXPENSIVE)),
            ),
        )
        if self.entry.data.get(CONF_CONTROL):
            result["target_c"] = round(target_c, 2)
        target_c = mould_target(target_c, bool(result.get("mould_risk")))
        blocks = [
            ScheduleBlock(float(b["start_h"]), float(b["end_h"]), str(b["preset"]))
            for b in (self.entry.data.get(CONF_SCHEDULE_BLOCKS) or [])
        ]
        preset_name = scheduled_preset(dt_util.now().hour + dt_util.now().minute / 60.0, blocks)
        preset_name = self._effective_preset(now_min, preset_name)
        if self.entry.data.get(CONF_CONTROL):
            result["preset"] = preset_name
        self._preset_now = preset_name
        settings = Settings(
            comfort_c=target_c,
            preset=Preset(preset_name) if preset_name in {p.value for p in Preset} else Preset.COMFORT,
            away_c=float(self.entry.data.get(CONF_AWAY_C, DEFAULT_AWAY_C)),
            window_delay_min=float(self.entry.data.get(CONF_WINDOW_DELAY_MIN, DEFAULT_WINDOW_DELAY_MIN)),
            eco_c=_optional_float(self.entry.data.get(CONF_ECO_C)),
            boost_c=_optional_float(self.entry.data.get(CONF_BOOST_C)),
        )
        self._settings = settings
        self._someone_home = someone_home
        self._cool_above_c = cool_above_c(settings, someone_home)
        self._heat_on_below_c = heat_on_below(self._cool_above_c, DEFAULT_HEATER_DEADBAND_C)
        result["cool_on_above_c"] = round(self._cool_above_c, 2)
        result["heat_on_below_c"] = round(self._heat_on_below_c, 2)

    async def _async_control(self, now: float, result: dict[str, Any]) -> None:
        """Send one climate action per cycle. Runs only when the control option is on."""
        climate_id = self.entry.data[CONF_CLIMATE]
        first_ac = self.hass.states.get(climate_id)
        self._ac_cooling_tick = first_ac is not None and first_ac.state == "cool"
        if not lock_allows_control(self._locked):
            result["action"] = "locked"
            return
        now_min = now / 60.0
        if self._external_change():
            # Arm the inhibition once per manual value. Re-arming on every cycle would keep the AC silent
            # for as long as the manual setpoint stands.
            manual = self._device_setpoint()
            if manual != self._inhibited_for_setpoint:
                self._inhibit_until_min = now_min + INHIBIT_MIN
                self._inhibited_for_setpoint = manual
        else:
            self._inhibited_for_setpoint = None
        # A stalled sensor stops the AC even inside the inhibit window. The window sends nothing else.
        stalled = bool(result.get("sensors_stalled")) or self._no_room_data_stop(now)
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
        outdoor = _float(self.hass.states.get(self.entry.data[CONF_OUTDOOR]))
        if stalled:
            # A stalled sensor cannot show that the room is cool. The AC stops, so it does not cool on a stale reading.
            # No valid room reading for SENSOR_STALL_S stops the AC the same way.
            action = Action(Mode.OFF, None, "sensor stalled" if result.get("sensors_stalled") else "no room reading")
        else:
            state = RoomState(
                room_c=statistics.median(rooms),
                window_open_min=window_open_min,
                someone_home=self._someone_home,
                precool_start_in_min=precool,
                outdoor_c=outdoor,
            )
            action = decide(state, self._settings)
        self._ac_cooling_tick = self._ac_cooling_tick or action.mode is Mode.COOL
        result["action"] = f"{action.mode.value}: {action.reason}"
        climate = self.hass.states.get(climate_id)
        result["daily_briefing"] = daily_briefing(
            result.get("day_type") or "unknown",
            float(self.entry.data.get(CONF_COMFORT_C, DEFAULT_COMFORT_C)),
            result.get("action"),
            None,
        )
        ventilation = air_quality_ventilation(_float(self._optional(CONF_CO2)), _float(self._optional(CONF_PM25)))
        humidity = humidity_action(_float(self._optional(CONF_ROOM_HUMIDITY)))
        result["ventilation"] = ventilation.reason
        result["humidity"] = humidity.reason
        await self._drive_switch(self.entry.data.get(CONF_FAN), ventilation.run)
        await self._drive_switch(self.entry.data.get(CONF_DEHUMIDIFIER), humidity.action == "dehumidify")
        await self._drive_switch(self.entry.data.get(CONF_HUMIDIFIER), humidity.action == "humidify")
        heat_ok = result.get("heating_allowed", True)
        await self._drive_switch(self.entry.data.get(CONF_BOILER), bool(result.get("boiler_demand")) and heat_ok)
        outdoor_now = _float(self.hass.states.get(self.entry.data[CONF_OUTDOOR]))
        rooms_wh = self._rooms()
        fan_ok = whole_house_fan_allowed(
            bool(self.entry.data.get(CONF_HOUSE_SEALED, False)),
            climate is not None and climate.state == "cool",
            outdoor_now,
            statistics.median(rooms_wh) if rooms_wh else 0.0,
        )
        result["whole_house_fan"] = fan_ok
        await self._drive_switch(self.entry.data.get(CONF_WHOLE_HOUSE_FAN), fan_ok)
        # The first AC's block below returns early on several paths, so the second AC is driven before it.
        await self._drive_second_ac(action, result, now, climate)
        if climate is None or climate.state in ("unavailable", "unknown"):
            return
        self._check_mode(climate.state)
        if rooms and self._ac_heat_wanted(action, result, statistics.median(rooms), window_open_pause(window_open_min, window_delay_min)):
            result["action"] = "heat: room below heat threshold"
            if await self._drive_ac_heat(climate_id, climate, now):
                result["action"] = "heat: waiting for minimum time"
            return
        # Minimum on and off times: do not change state until the current state has lasted long enough.
        # Heat counts as running, so stopping heat, or switching heat to cool, waits for the minimum on time.
        wanted_mode = "cool" if action.mode is Mode.COOL else "off"
        running = climate.state in ("cool", "heat")
        if climate.state != wanted_mode and (running or wanted_mode == "cool"):
            since_min = (now - self._state_changed_at) / 60.0 if self._state_changed_at is not None else 1e9
            if not minimum_time_ok(running, since_min, self.entry.data.get(CONF_MIN_ON_MIN, 5.0), self.entry.data.get(CONF_MIN_OFF_MIN, 3.0)):
                result["action"] = f"{action.mode.value}: waiting for minimum time"
                return
            self._state_changed_at = now
        # Write only what differs from the device's current state, so a manual change is not overwritten every cycle.
        if action.mode is Mode.OFF:
            # A stall means the AC goes off. Dry mode would keep it running, so it is skipped then.
            if not stalled and await self._drive_coil_dry(climate_id, climate, now):
                return
            # No cooling needed. If the room is too humid and the AC has a dry mode, dry the room with it.
            if not stalled and humidity.action == "dehumidify" and not self.entry.data.get(CONF_DEHUMIDIFIER) and climate.state != "dry":
                await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate_id, "hvac_mode": "dry"}, blocking=True)
                self._last_sent_min = now / 60.0
                self._last_written_mode = "dry"
                return
            if climate.state != "off":
                await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate_id, "hvac_mode": "off"}, blocking=True)
                self._last_sent_min = now / 60.0
                self._last_written_mode = "off"
            return
        self._was_cooling = climate.state == "cool"
        self._dry_started_min = None
        if climate.state != "cool":
            await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate_id, "hvac_mode": "cool"}, blocking=True)
            self._last_sent_min = now / 60.0
            self._last_written_mode = "cool"
            self._last_start_min["climate_1"] = now / 60.0
        await self._write_setpoint(climate_id, climate, action.setpoint_c, now)

    async def _publish_investigation(self, now: float, result: dict[str, Any]) -> None:
        """Explain the AC action from its inputs. Ask the conversation agent when the mode changes, within the daily budget."""
        action_text = result.get("action")
        if action_text and (not self._action_log or self._action_log[-1].text != action_text):
            self._action_log = (self._action_log + [ActionRecord(now / 60.0, action_text)])[-20:]
        rooms = self._rooms()
        window_open_min = 0.0 if self._window_open_since is None else (now - self._window_open_since) / 60.0
        investigation = investigate(
            action_text,
            statistics.median(rooms) if rooms else None,
            self._cool_above_c,
            self._someone_home,
            window_open_min,
            self._action_log,
        )
        result["investigation"] = investigation
        mode_now = action_text.partition(":")[0] if action_text else None
        if mode_now and mode_now != self._last_ai_mode and self.entry.data.get(CONF_AI_CONVERSATION):
            self._last_ai_mode = mode_now
            answer = await self._ask_model(f"In one sentence, explain why the AC is {mode_now}. {investigation['answer']}")
            if answer:
                self._ai_answer = answer
        result["investigation_ai"] = self._ai_answer
        result["ai_calls_today"] = self._budget.used(dt_util.now().date())

    async def _ask_model(self, text: str) -> str | None:
        """One call to the conversation agent, if the budget allows. Returns the reply text, or None."""
        agent = self.entry.data.get(CONF_AI_CONVERSATION)
        limit = int(self.entry.data.get(CONF_AI_BUDGET) or 0)
        if not agent or not self._budget.try_take(dt_util.now().date(), limit):
            return None
        try:
            response = await self.hass.services.async_call(
                "conversation", "process", {"agent_id": agent, "text": text}, blocking=True, return_response=True
            )
        except Exception:  # the agent may be missing or unavailable; the rule-based answer still stands
            _LOGGER.debug("conversation agent %s failed", agent, exc_info=True)
            return None
        speech = ((response or {}).get("response") or {}).get("speech", {}).get("plain", {}).get("speech")
        return str(speech) if speech else None

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
        A stalled sensor, a paused window and a closed heating window (outdoor limit) also stop it.
        """
        return (
            action.mode is Mode.OFF
            and result.get("heat_source") == "ac"
            and self._heater_switch() is None
            and self._someone_home
            and bool(result.get("heating_allowed", True))
            and not result.get("sensors_stalled")
            and not window_paused
            and room_c < self._heat_on_below_c
        )

    async def _drive_ac_heat(self, climate_id: str, climate: State, now: float) -> bool:
        """Heat with the AC: hvac_mode heat, then the heat setpoint. Starting heat waits for the minimum time.

        Returns True when the minimum time holds the start.
        """
        if climate.state != "heat":
            since_min = (now - self._state_changed_at) / 60.0 if self._state_changed_at is not None else 1e9
            running = climate.state in ("cool", "heat")
            if not minimum_time_ok(running, since_min, self.entry.data.get(CONF_MIN_ON_MIN, 5.0), self.entry.data.get(CONF_MIN_OFF_MIN, 3.0)):
                return True
            await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate_id, "hvac_mode": "heat"}, blocking=True)
            self._state_changed_at = now
            self._last_sent_min = now / 60.0
            self._last_written_mode = "heat"
        await self._write_setpoint(climate_id, climate, self._cool_above_c, now)
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
                    await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate_id, "hvac_mode": "dry"}, blocking=True)
                handled = True
            elif self._dry_started_min is not None:
                self._dry_started_min = None
                if climate.state == "dry":
                    await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate_id, "hvac_mode": "off"}, blocking=True)
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
                if stagger_allowed("climate_2", wanting, self._last_start_min, now / 60.0):
                    await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate_2_id, "hvac_mode": "cool"}, blocking=True)
                    self._last_start_min["climate_2"] = now / 60.0
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
            result["second_ac"] = "driven"

    def _precool_start(self, now: float, rooms: list[float]) -> float | None:
        """Pre-cool start from the planner. Needs a schedule entity whose next_event attribute is an ISO time."""
        schedule = self._optional(CONF_SCHEDULE)
        if schedule is None or self._fit is None:
            return None
        raw = schedule.attributes.get("next_event")
        try:
            deadline = datetime.fromisoformat(raw).timestamp() if raw else None
        except (TypeError, ValueError):
            return None
        if deadline is None:
            return None
        climate = self.hass.states.get(self.entry.data[CONF_CLIMATE])
        outdoor = _float(self.hass.states.get(self.entry.data[CONF_OUTDOOR]))
        setpoint = float(self.entry.data.get(CONF_COMFORT_C, DEFAULT_COMFORT_C))
        if climate is None or outdoor is None or not rooms:
            return None
        power_kw = running_power_kw(self._entries, now) or 0.0
        if power_kw <= 0:
            return None
        return precool_start_in_min(
            self._fit,
            statistics.median(rooms),
            setpoint,
            outdoor,
            power_kw,
            (deadline - now) / 60.0,
            self._price_forecast(now),
        )

    def _forecast(self, now: float) -> dict[str, Any]:
        # Observe-only values: the control path overwrites target_c and preset with the full decision.
        comfort_default = float(self.entry.data.get(CONF_COMFORT_C, DEFAULT_COMFORT_C))
        result: dict[str, Any] = {
            "target_c": round(comfort_default, 2),
            "preset": "comfort",
            "samples": len(self._entries),
            "status": "learning",
            "minutes": None,
            "energy_kwh": None,
            "cost": None,
            "reason": self._fit_reason,
            "k": None,
            "c": None,
            "g": None,
            "forecast_error_min": self._forecast_error_min,
            "backtest_runs": len([r for r in self._backtest_runs if r.predicted_min is not None]),
            "room_fit": self._room_fit,
            "tpi": None if self._tpi is None else {"k_per_h": round(self._tpi.k, 4), "g_k_per_h": round(self._tpi.g, 4), "c_k_per_h_per_kw": round(self._tpi.c, 4), "samples_off": self._tpi.samples_off, "samples_on": self._tpi.samples_on},
        }
        active = self._tpi.to_fit() if self._tpi is not None else self._fit
        if active is None:
            return result

        result.update(k=round(active.k, 4), c=round(active.c, 4), g=round(active.g, 4))
        climate = self.hass.states.get(self.entry.data[CONF_CLIMATE])
        rooms = self._rooms()
        outdoor = _float(self.hass.states.get(self.entry.data[CONF_OUTDOOR]))
        # Observe-only uses the climate entity's own setpoint, as the original request asked.
        # Control mode uses the comfort setting, because the integration writes that setpoint.
        if self.entry.data.get(CONF_CONTROL):
            target = float(self.entry.data.get(CONF_COMFORT_C, DEFAULT_COMFORT_C))
        else:
            setpoint = climate.attributes.get("temperature") if climate else None
            target = float(setpoint) if setpoint is not None else float(self.entry.data.get(CONF_COMFORT_C, DEFAULT_COMFORT_C))
        if not rooms or outdoor is None or climate is None:
            result.update(status="no input")
            return result

        room = statistics.median(rooms)
        if room <= target:
            result.update(status="at setpoint", minutes=0.0, energy_kwh=0.0, cost=0.0)
            return result

        power_kw = (_float(self.hass.states.get(self.entry.data[CONF_POWER])) or 0.0) / 1000.0
        if power_kw < 0.2:
            power_kw = running_power_kw(self._entries, now) or 0.0
        if power_kw <= 0:
            result.update(status="no running power yet")
            return result

        f = forecast(active, room, target, outdoor, power_kw, self._price_forecast(now))
        result.update(
            status="reachable" if f.reachable else "unreachable",
            minutes=None if f.minutes is None else round(f.minutes, 1),
            energy_kwh=None if f.energy_kwh is None else round(f.energy_kwh, 3),
            cost=None if f.cost is None else round(f.cost, 3),
            reason=f.reason,
        )
        return result
