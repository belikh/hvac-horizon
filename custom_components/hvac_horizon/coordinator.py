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

from custom_components.hvac_horizon.backfill import accuracy_pairs, backtest, median_abs_error
from custom_components.hvac_horizon.collector import (
    Entry,
    heat_pairs_from_entries,
    entries_from_history,
    has_enough_cycles,
    price_slots,
    room_samples_from_entries,
    running_power_kw,
    samples_from_entries,
)
from custom_components.hvac_horizon.devices import COOL, HEAT, HEATER, RoomFit, fit_room, forecast_fit
from custom_components.hvac_horizon.forecast import forecast, price_at
from custom_components.hvac_horizon.decide import MPC_DEADBAND_C, Action, Mode, Preset, RoomState, Settings, cool_above_c, decide
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
    mpc_heat_plan,
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
from custom_components.hvac_horizon.features import SENSOR_STALL_S, fan_assist_on, peer_warmer, window_actuator_command, group_start_allowed, house_cooling, heat_setpoint, air_quality_ventilation, pid_gain, boost_output, coil_dry_due, comfort_energy_setpoint, valve_exercise_due, trv_offset, corrected_setpoint, residual_correction, cover_action, house_mode_from_store, humidity_action, keep_alive_due, minimum_time_ok, price_shifted_target, round_to_step, shed_plan, shed_restore, fan_mode_for_gap, mpc_idle, stagger_allowed, valve_opening_pct, window_open_pause, minutes_until_preset
from custom_components.hvac_horizon.model import Fit, NotEnoughData, fit
from custom_components.hvac_horizon.control import precool_start_in_min
from custom_components.hvac_horizon.autotune import RelayTuner
from custom_components.hvac_horizon.pid import Pid
from custom_components.hvac_horizon.tpi import TpiFit, adjust_cool_gain, adjust_heat_gain, heat_up_minutes, learn_heat_rate, learn_room_rates, tpi_on_percent, tpi_threshold_forced_off
from custom_components.hvac_horizon.investigator import REPORT_INSTRUCTION, REPORT_WINDOW_MIN, ActionRecord, CallBudget, DayReading, day_context, day_summary, investigate
from custom_components.hvac_horizon.solar_model import clear_sky_irradiance, solar_elevation_azimuth, window_irradiance

from .const import (
    CONF_DRY_FOR_HUMIDITY,
    CONF_AC_HEAT,
    CONF_MPC_HEAT_PLAN,
    CONF_AUTO_FAN,
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
    CONF_HEAT_BOOST_C,
    CONF_HEAT_ECO_C,
    CONF_HEATER_KP,
    CONF_HEATER_KI,
    CONF_HEATER_KD,
    CONF_HEATER_ALGORITHM,
    CONF_MAX_ON_PERCENT,
    CONF_NIGHT_PLAN_ACT,
    CONF_WINDOW_ACTUATOR,
    CONF_PEER_ENTRY,
    CONF_PEER_SETPOINT_MODE,
    PEER_SETPOINT_STEP_K,
    CONF_TPI_COEF_EXT,
    CONF_TPI_COEF_INT,
    CONF_ECONOMIZER_FAN,
    CONF_TPI_THRESHOLD_HIGH,
    CONF_TPI_THRESHOLD_LOW,
    DEFAULT_TPI_COEF_EXT,
    DEFAULT_TPI_COEF_INT,
    HEATER_ALGORITHM_TPI,
    DEFAULT_HEATER_KP,
    DEFAULT_HEATER_KI,
    DEFAULT_HEATER_KD,
    DEFAULT_HEATER_DEADBAND_C,
    FROST_FLOOR_C,
    PWM_WINDOW_MIN,
    FAN_PWM_WINDOW_MIN,
    CONF_AWAY_C,
    CONF_MOTION,
    CONF_CLIMATE,
    CONF_CLIMATE_2,
    CONF_COMPRESSOR_GROUP,
    CONF_AWAY_DELAY_MIN,
    CONF_MOTION_AWAY_MIN,
    DEFAULT_MOTION_AWAY_MIN,
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
    CONF_OUTPUT_MIN_ENTITY,
    CONF_OUTPUT_MAX_ENTITY,
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
    CONF_STALL_WINDOW_S,
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
    KEEP_DAYS,
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
# The heat plan's energy cost is multiplied by this. At 1.0, one 15 min step at $0.18/kWh costs about 0.05 units, and
# one K of discomfort costs 1 unit per step, so price rarely changes step 0. At 50, a $0.28/kWh step costs about 3.9 units.
# Picked by a sweep of 1 to 119 on one room at 23.4 C. It is not calibrated against metered data.
MPC_HEAT_COST_WEIGHT = 50.0
SAFETY_FALLBACK_PCT = 0.0  # heater output when a sensor stalls; safety_output applies it


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
        self._second_changed_at: float | None = None  # when the second AC last changed mode, for its minimum times
        self._shed_loads: set[str] = set()  # loads this integration switched off for the power limit, to restore later
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
        self._ai_answer_mode: str | None = None  # the AC mode the stored answer explains
        self._report_budget = CallBudget()  # on-demand report calls per day; separate from the mode-change budget
        self._report: dict[str, Any] = {"investigation_report": None, "investigation_report_source": None, "investigation_report_at": None}
        self._tpi: TpiFit | None = None  # room rate coefficients k, g, c; the forecast uses them when present
        self._heat_rate: float | None = None  # heater rate, K/h at full output, learned from heating intervals; drives preheat
        self._auto_tpi_gain = 1.0  # online gain on the fitted cooling c, learned each interval; stored
        self._auto_heat_gain = 1.0  # online gain on the learned heat rate, learned on heating intervals; stored
        self._actuator_opened_by_us = False  # set when this integration opens the window actuator; only then does it close it
        self._prev_room_c: float | None = None  # room median at the previous TPI tick; its change is the trend the thresholds read
        self._room_fit: dict[str, Any] | None = None  # two-device fit, published when a second power sensor is set
        self._room_model: RoomFit | None = None  # the valid two-device fit the forecast uses when a second AC is set
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
        self._heat_target_c: float = DEFAULT_COMFORT_C
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
        # The AC setpoint before a peer request first lowered it. The restore writes it back when the request ends with the AC off.
        self._peer_setpoint_original: float | None = None
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
        stored = data.get("investigation_report")
        if isinstance(stored, dict):
            # The last on-demand report and its time survive a restart. Keys missing from the store stay None.
            self._report = {key: stored.get(key) for key in self._report}
        self._heater_gains = data.get("heater_gains")
        self._auto_tpi_gain = float((data.get("auto_tpi_gains") or {}).get("cool", 1.0))
        self._auto_heat_gain = float((data.get("auto_tpi_gains") or {}).get("heat", 1.0))
        self._refit()

    def what_if(self, room_c: float, target_c: float, outdoor_c: float, power_kw: float) -> dict[str, Any]:
        """Forecast minutes, energy and cost for given conditions, using the current fit and price slots."""
        active = self._active_fit()
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
        self._auto_tpi_gain = 1.0
        self._auto_heat_gain = 1.0
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
                self._heater_pid.set_gains(*self._heater_gain_values())
            self._store.async_delay_save(self._save_payload, 0)

    def _heater_gain_values(self) -> tuple[float, float, float]:
        """kp, ki, kd for the heater PID. Each gain field the user set wins. A field at its default takes the tuned value."""
        tuned = self._heater_gains or {}
        return (
            pid_gain(float(self.entry.data.get(CONF_HEATER_KP, DEFAULT_HEATER_KP)), DEFAULT_HEATER_KP, tuned.get("kp")),
            pid_gain(float(self.entry.data.get(CONF_HEATER_KI, DEFAULT_HEATER_KI)), DEFAULT_HEATER_KI, tuned.get("ki")),
            pid_gain(float(self.entry.data.get(CONF_HEATER_KD, DEFAULT_HEATER_KD)), DEFAULT_HEATER_KD, tuned.get("kd")),
        )

    def _save_payload(self) -> dict[str, Any]:
        return {
            "entries": [asdict(e) for e in self._entries],
            "heater_gains": self._heater_gains,
            "auto_tpi_gains": {"cool": self._auto_tpi_gain, "heat": self._auto_heat_gain},
            "investigation_report": self._report,
        }

    async def async_shutdown_store(self) -> None:
        await self._store.async_save(self._save_payload())

    def _refit_room_model(self) -> None:
        """Fit one coefficient per AC, and one for the heater when a heater switch is set, from the readings.

        Runs only when a second AC and its power sensor are set. Without a heater switch the fit has the two AC terms only.
        """
        self._room_fit = None
        self._room_model = None
        if not (self.entry.data.get(CONF_CLIMATE_2) and self.entry.data.get(CONF_POWER_2)):
            return
        kinds = {"ac": COOL, "ac_2": COOL}
        if self.entry.data.get(CONF_HEATER_SWITCH):
            kinds[HEATER] = HEAT
        try:
            found = fit_room(room_samples_from_entries(self._entries), kinds)
        except NotEnoughData as err:
            self._room_fit = {"reason": str(err)}
            return
        self._room_model = found
        self._room_fit = {
            "k_per_h": round(found.k, 4),
            "g_k_per_h": round(found.g, 4),
            "ac_k_per_h_per_kw": round(found.coef["ac"], 4),
            "ac_2_k_per_h_per_kw": round(found.coef["ac_2"], 4),
            "samples": found.samples,
        }
        if HEATER in kinds:
            self._room_fit["heater_k_per_h_on"] = round(found.coef[HEATER], 4)

    def _active_fit(self) -> Fit | None:
        """The room model for the forecast: the fitted rates with the online cooling gain, else the single-device fit."""
        if self._tpi is not None:
            return self._tpi.to_fit(self._auto_tpi_gain)
        return self._fit

    def _learn_cool_gain(self) -> None:
        """Move the cool gain toward the value that predicts the last interval. Cool mode, same setpoint, AC running."""
        if self._tpi is None or len(self._entries) < 2:
            return
        a, b = self._entries[-2], self._entries[-1]
        pairs = samples_from_entries([a, b])
        if not pairs or a.mode != "cool" or b.mode != "cool" or a.setpoint_c is None or b.setpoint_c is None:
            return
        if abs(a.setpoint_c - b.setpoint_c) > 0.1:
            return
        self._auto_tpi_gain = adjust_cool_gain(self._auto_tpi_gain, pairs[0], self._tpi)

    def _learn_heat_gain(self) -> None:
        """Move the heat gain toward the value that predicts the last interval. Heater running, AC not cooling."""
        if self._tpi is None or self._heat_rate is None or len(self._entries) < 2:
            return
        a, b = self._entries[-2], self._entries[-1]
        pairs = heat_pairs_from_entries([a, b])
        if not pairs or a.mode == "cool" or b.mode == "cool":
            return
        sample, duty = pairs[0]
        self._auto_heat_gain = adjust_heat_gain(self._auto_heat_gain, sample, duty, self._heat_rate, self._tpi.k, self._tpi.g)

    def _refit(self) -> None:
        samples = samples_from_entries(self._entries)
        self._refit_room_model()
        try:
            self._tpi = learn_room_rates(samples)
        except NotEnoughData:
            self._tpi = None
        self._heat_rate = None
        if self._tpi is not None:
            try:
                self._heat_rate = learn_heat_rate(heat_pairs_from_entries(self._entries), self._tpi.k, self._tpi.g)
            except NotEnoughData:
                self._heat_rate = None
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
        """True when a gate forces the heater off: window open, house away, heating not allowed, lock on, a stalled sensor
        or another entry in the same house group cooling."""
        window = self._optional(CONF_WINDOW)
        return (
            (window is not None and window.state == "on")
            or house_mode_from_store(self.hass.data.get(DOMAIN, {})) == "away"
            or not result.get("heating_allowed", True)
            or bool(result.get("house_cooling"))
            or not lock_allows_control(self._locked)
            or self._stall_blocks(result)
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
        if driving and (self._heater_gated or self._ac_cooling_tick or gate_c is None or gate_c >= self._heat_on_below_c):
            pct = 0.0
        if driving:
            # A stalled sensor sets the fallback output, whatever the gates say (features.safety_output).
            pct = safety_output(self._stall_blocks(result), pct, fallback_pct=SAFETY_FALLBACK_PCT)
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

    def _output_limit(self, entity_key: str, config_key: str, default: float) -> float:
        """A heater output limit: the number entity's value when it has one, else the config value."""
        value = _float(self._optional(entity_key))
        return value if value is not None else float(self.entry.data.get(config_key, default))

    def set_heater_output(self, pct: float) -> None:
        lo = self._output_limit(CONF_OUTPUT_MIN_ENTITY, CONF_OUTPUT_MIN, 0.0)
        hi = max(lo, self._output_limit(CONF_OUTPUT_MAX_ENTITY, CONF_OUTPUT_MAX, 100.0))
        self._pwm_output_pct = clamp_output(pct, lo, hi)

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

    def _stall_blocks(self, result: dict[str, Any]) -> bool:
        """True when a stall stops the AC and the heater. A stalled outdoor sensor does, and so does every room sensor being stale.

        One stale room sensor drops out of the median, and a fresh one keeps control running.
        """
        stalled = set(result.get("sensors_stalled") or [])
        if self.entry.data[CONF_OUTDOOR] in stalled:
            return True
        rooms = list(self.entry.data.get(CONF_ROOM_SENSORS) or [])
        fresh = [r for r in rooms if r not in stalled and _float(self.hass.states.get(r)) is not None]
        return not fresh and any(r in stalled for r in rooms)

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
            if sensor_stalled(state.last_reported.timestamp(), now, self._stall_window_s()):
                stalled.append(name)
        return stalled

    def _no_room_data_stop(self, now: float) -> bool:
        """True when no room has had a valid reading for SENSOR_STALL_S. The AC then stops."""
        return self._last_room_data_s is not None and now - self._last_room_data_s >= self._stall_window_s()

    def _stall_window_s(self) -> float:
        """The stall window in seconds, from the stall_window_s option. Defaults to SENSOR_STALL_S."""
        return float(self.entry.data.get(CONF_STALL_WINDOW_S, SENSOR_STALL_S))

    def _rooms(self) -> list[float]:
        """Room readings for the median: the room sensors only.

        The AC's own temperature reading is whole degrees, so it is kept out of the median.
        """
        stalled = set(self._stalled_sensors(dt_util.utcnow().timestamp()))
        every = [self.hass.states.get(e) for e in self.entry.data[CONF_ROOM_SENSORS]]
        fresh = [s for e, s in zip(self.entry.data[CONF_ROOM_SENSORS], every) if e not in stalled]
        # Stale room sensors drop out only when a fresh one exists. With none, the stop rule in _stall_blocks decides.
        states = fresh if any(_float(s) is not None for s in fresh) else every
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
            heat_pct=self._pwm_pct_now,
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
            self._learn_cool_gain()
            self._learn_heat_gain()
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
        await self._drive_window_actuator(result)
        # The motion preset sets occupancy in _plan_control. The idle clock starts at the first tick, as presence does.
        motion_state = self._optional(CONF_MOTION)
        motion_on: bool | None = None
        if motion_state is not None and motion_state.state in ("on", "off"):
            motion_on = motion_state.state == "on"
        if self._last_motion_at is None:
            self._last_motion_at = now
        if motion_on:
            self._last_motion_at = now
        motion_delay_min = max(1.0, float(self.entry.data.get(CONF_MOTION_AWAY_MIN, DEFAULT_MOTION_AWAY_MIN)))
        result["motion_preset"] = motion_preset(motion_on, (now - self._last_motion_at) / 60.0, away_delay_min=motion_delay_min)
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
        # The plan aims at the effective cool threshold that _plan_control just set, so it matches the decision.
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
                        self._cool_above_c,
                        outdoor_now,
                        running_power_kw(self._entries, now) or 0.0,
                        step_prices,
                        step_min=MPC_STEP_MIN,
                    )
                ]
                if self.entry.data.get(CONF_MPC_HEAT_PLAN, False):
                    # The heat side reuses the cooling coefficient c with the AC's running power. With no running reading
                    # in the last two hours, the mean running power of the kept history stands in for it.
                    heat_kw = running_power_kw(self._entries, now) or running_power_kw(self._entries, now, window_s=KEEP_DAYS * 86400.0)
                    if heat_kw:
                        result["mpc_heat_steps"] = [
                            bool(a)
                            for a in mpc_heat_plan(
                                self._fit.k,
                                self._fit.c,
                                self._fit.g,
                                statistics.median(rooms_econ),
                                self._heat_target_c,
                                outdoor_now,
                                heat_kw,
                                step_prices,
                                step_min=MPC_STEP_MIN,
                                cost_weight=MPC_HEAT_COST_WEIGHT,
                            )
                        ]
        pid_rooms = self._rooms()
        pid_outdoor = _float(self.hass.states.get(self.entry.data[CONF_OUTDOOR]))
        heater_configured = bool(self.entry.data.get(CONF_CONTROL)) and bool(self._heater_switch())
        inhibit_active = inhibited(dt_util.utcnow().timestamp() / 60.0, self._inhibit_until_min)
        self._publish_cooling(result)
        self._heater_gated = heater_configured and self._heater_gate_closed(result)
        if self._heater_gated:
            self._pwm_output_pct = 0.0
            if self._heater_pid is not None:
                self._heater_pid.reset()
        elif heater_configured and pid_rooms and pid_outdoor is not None and not inhibit_active:
            pid_room = statistics.median(pid_rooms)
            if self.entry.data.get(CONF_HEATER_ALGORITHM) == HEATER_ALGORITHM_TPI:
                # The TPI duty cycle replaces the PID output. The PID is reset so a switch back starts clean.
                room_slope = None if self._prev_room_c is None else pid_room - self._prev_room_c
                self._prev_room_c = pid_room
                if tpi_threshold_forced_off(
                    pid_room,
                    self._heat_target_c,
                    room_slope,
                    _optional_float(self.entry.data.get(CONF_TPI_THRESHOLD_HIGH)) or 0.0,
                    _optional_float(self.entry.data.get(CONF_TPI_THRESHOLD_LOW)) or 0.0,
                ):
                    tpi_pct = 0.0
                else:
                    tpi_pct = tpi_on_percent(
                        self._heat_target_c,
                        pid_room,
                        pid_outdoor,
                        float(self.entry.data.get(CONF_TPI_COEF_INT, DEFAULT_TPI_COEF_INT)),
                        float(self.entry.data.get(CONF_TPI_COEF_EXT, DEFAULT_TPI_COEF_EXT)),
                        max_pct=_optional_float(self.entry.data.get(CONF_MAX_ON_PERCENT)),
                    )
                if self._heater_pid is not None:
                    self._heater_pid.reset()
                self.set_heater_output(boost_output(self._preset_now or "", tpi_pct))
                result["tpi_output_pct"] = round(tpi_pct, 2)
            else:
                if self._heater_pid is None:
                    kp, ki, kd = self._heater_gain_values()
                    self._heater_pid = Pid(
                        kp,
                        ki,
                        kd,
                        ke=0.0,
                        out_min=0.0,
                        out_max=100.0,
                    )
                climate_now = self.hass.states.get(self.entry.data[CONF_CLIMATE])
                # The heater output is forced to 0 in the heat band and while the AC cools. Hold the integral then,
                # so the positive error of that time does not wind it up.
                forced_off = pid_room >= self._heat_on_below_c or (climate_now is not None and climate_now.state == "cool")
                pid_out = self._heater_pid.update(
                    self._heat_target_c, pid_room, SAMPLE_INTERVAL_S / 3600.0, outdoor=pid_outdoor, freeze_integral=forced_off
                )
                self.set_heater_output(boost_output(self._preset_now or "", pid_out))
                result["heater_pid_output"] = round(pid_out, 2)
        elif heater_configured and not inhibit_active:
            # The PID cannot run (no room or outdoor reading). A stale output must not keep the heater on.
            # Boost still heats: boost_output returns 100 in boost and 0 otherwise.
            self.set_heater_output(boost_output(self._preset_now or "", 0.0))
        result["heater_output_pct"] = round(self._pwm_output_pct, 2)
        valve_rooms = self._rooms()
        valve_opening = None
        if valve_rooms:
            comfort_target = float(self.entry.data.get(CONF_COMFORT_C, DEFAULT_COMFORT_C))
            room_for_valve = statistics.median(valve_rooms)
            trv_c = _float(self._optional(CONF_TRV_TEMPERATURE))
            if self.entry.data.get(CONF_VALVE) and trv_c is not None:
                self._trv_pairs = (self._trv_pairs + [(trv_c, room_for_valve)])[-TRV_HISTORY:]
            # With a valve and a TRV sensor, the valve command aims at the setpoint plus the learned offset.
            # The offset is TRV minus room. A TRV that reads warmer closes its valve early, so the target rises
            # and the valve opens more. Scale: the offset shifts the target in kelvin, and valve_opening_pct turns
            # the shift into points. 1 K of offset moves the opening by 100 / VALVE_BAND_C points (100 at the 1 K band),
            # clamped to 0 to 100.
            offset = trv_offset([t for t, _ in self._trv_pairs], [r for _, r in self._trv_pairs]) if self._trv_pairs else None
            if self.entry.data.get(CONF_VALVE):
                result["trv_offset_k"] = None if offset is None else round(offset, 2)
                comfort_target = corrected_setpoint(comfort_target, offset)
            valve_opening = valve_opening_pct(room_for_valve, comfort_target)
            if self._peer_open_requested():
                # Another entry's room is at least 2 K warmer than this one. The valve opens fully (see _request_peer_valve).
                valve_opening = 100.0
        self._request_peer_valve()
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
                shed_now = [(entity_id, watts) for entity_id, watts, _ in loads if entity_id in self._shed_loads]
                restore = shed_restore(total_w, float(max_w), shed_now)
                if restore is not None and await self._drive_restore(restore):
                    result["restored"] = restore
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
        result["safety_output_pct"] = safety_output(self._stall_blocks(result), 100.0, fallback_pct=SAFETY_FALLBACK_PCT)
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
            self._shed_loads.add(entity_id)

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
        sent = state.state != "on"
        await self._drive_switch(entity_id, True)
        self._shed_loads.discard(entity_id)
        return sent

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
        outdoor = _float(self.hass.states.get(self.entry.data[CONF_OUTDOOR]))
        if not rooms or outdoor is None:
            return
        house_away = not effective_home(True, house_mode_from_store(self.hass.data.get(DOMAIN, {})))
        # House away ends the open advice, so an actuator this integration opened closes at once.
        open_advice = (
            result.get("window_advice") == "open" or result.get("economizer") in ("open_windows", "fan_assist")
        ) and not house_away
        command = window_actuator_command(
            open_advice, statistics.median(rooms), outdoor, state.state in ("open", "on"), self._actuator_opened_by_us
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
        self._actuator_opened_by_us = command == "open"

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

        The AC cools above `_cool_above_c` (decide). The heater PID aims at `_heat_target_c`, and the heater
        and the AC heat setpoint use `_heat_on_below_c`, one deadband lower. All three come from one planned target.
        """
        now_min = now / 60.0
        # Home when any presence source is home. Away only after every source has been away for the away delay.
        someone_home = self._occupancy_mode(now) == "home"
        # Motion idle past its delay is away for cooling and gets the eco heat setpoint. Presence away and house away get the frost floor.
        motion_idle = result.get("motion_preset") == "away"
        if result.get("motion_preset") is not None:
            # With a motion sensor, motion decides: on or recent is home, idle past the delay is away.
            someone_home = result["motion_preset"] == "home"
        house_mode = house_mode_from_store(self.hass.data.get(DOMAIN, {}))
        house_away = not effective_home(True, house_mode)
        someone_home = effective_home(someone_home, house_mode)
        comfort = float(self.entry.data.get(CONF_COMFORT_C, DEFAULT_COMFORT_C))
        comfort_raw = comfort
        # Comfort-versus-energy weight: 0 keeps comfort, 1 moves the target to the eco setpoint. Off without an eco setpoint.
        energy_weight = float(self.entry.data.get(CONF_COMFORT_ENERGY_WEIGHT) or 0.0)
        eco_c = _optional_float(self.entry.data.get(CONF_ECO_C))
        if energy_weight > 0 and eco_c is not None:
            comfort = comfort_energy_setpoint(comfort, eco_c, min(max(energy_weight, 0.0), 1.0))
        # The last good price stands in for a dropped price sensor for up to 60 minutes (stale_price_fallback).
        price = result.get("price_used_per_kwh")
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
        now_h = dt_util.now().hour + dt_util.now().minute / 60.0
        scheduled_name = scheduled_preset(now_h, blocks)
        preset_name = self._effective_preset(now_min, scheduled_name)
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
        self._someone_home = someone_home
        # One heat target per tick. Vacation, house away, presence away and the away preset heat only to the frost floor.
        # Otherwise the heat setpoint follows the preset (heat_setpoint). It never uses the eco or away cooling setpoint.
        if vacation_on or house_away or preset_name == Preset.AWAY.value or (not someone_home and not motion_idle):
            heat_target_c = FROST_FLOOR_C
        else:
            heat_target_c = heat_setpoint(
                preset_name,
                float(self.entry.data.get(CONF_COMFORT_C, DEFAULT_COMFORT_C)),
                _optional_float(self.entry.data.get(CONF_HEAT_ECO_C)),
                _optional_float(self.entry.data.get(CONF_HEAT_BOOST_C)),
                motion_idle,
            )
        result["preheat"] = "off"
        result["heat_up_min"] = None
        if scheduled_name != Preset.COMFORT.value and not vacation_on and not house_away and heat_target_c < comfort_raw:
            heat_target_c, result["preheat"], result["heat_up_min"] = self._preheat(now_h, blocks, heat_target_c, comfort_raw)
        # Boost cools to boost_c. When heating is allowed and the heat target sits above boost_c, the heat side
        # wins: boost_c lifts to the heat target plus the deadband, so the AC does not cool while the heater heats.
        # Outside that case the heater never aims above the cooling threshold (the cap below).
        if (
            preset_name == Preset.BOOST.value
            and bool(result.get("heating_allowed", True))
            and (self._heater_switch() is not None or bool(self.entry.data.get(CONF_AC_HEAT)) or bool(self.entry.data.get(CONF_MPC_HEAT_PLAN)))
            and settings.boost_c is not None
            and heat_target_c + DEFAULT_HEATER_DEADBAND_C > settings.boost_c
        ):
            settings = replace(settings, boost_c=heat_target_c + DEFAULT_HEATER_DEADBAND_C)
        self._settings = settings
        self._cool_above_c = cool_above_c(settings, someone_home)
        heat_target_c = min(heat_target_c, self._cool_above_c)
        self._heat_target_c = heat_target_c
        self._heat_on_below_c = heat_on_below(heat_target_c, DEFAULT_HEATER_DEADBAND_C)
        result["cool_on_above_c"] = round(self._cool_above_c, 2)
        result["heat_on_below_c"] = round(self._heat_on_below_c, 2)

    def _preheat(self, now_h: float, blocks: list[ScheduleBlock], heat_target_c: float, comfort_c: float) -> tuple[float, str, float | None]:
        """Heat to comfort early when the next comfort block starts within the learned heat-up time.

        Returns the heat target, a state and the heat-up time in minutes. The state is "active", "waiting",
        "no heat model", "cannot reach comfort" or "no comfort block". The heat-up time uses the learned heat rate
        times the online heat gain. Without a heat rate the heat target stays as it is.
        """
        if self._tpi is None or self._heat_rate is None:
            return heat_target_c, "no heat model", None
        lead_min = minutes_until_preset(now_h, blocks, Preset.COMFORT.value)
        if lead_min is None:
            return heat_target_c, "no comfort block", None
        rooms = self._rooms()
        outdoor = _float(self.hass.states.get(self.entry.data[CONF_OUTDOOR]))
        if not rooms or outdoor is None:
            return heat_target_c, "no heat model", None
        rate = self._heat_rate * self._auto_heat_gain
        needed = heat_up_minutes(statistics.median(rooms), comfort_c, outdoor, self._tpi.k, self._tpi.g, rate)
        if needed is None:
            return heat_target_c, "cannot reach comfort", None
        if lead_min <= needed:
            return comfort_c, "active", needed
        return heat_target_c, "waiting", needed

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
        outdoor = _float(self.hass.states.get(self.entry.data[CONF_OUTDOOR]))
        # The night plan acts only with its flag on. Its pre-cool target and hold use the comfort setpoint.
        night = result.get("night_plan") if self.entry.data.get(CONF_NIGHT_PLAN_ACT, False) else None
        comfort_c = float(self.entry.data.get(CONF_COMFORT_C, DEFAULT_COMFORT_C))
        if stalled:
            # A stalled sensor cannot show that the room is cool. The AC stops, so it does not cool on a stale reading.
            # No valid room reading for SENSOR_STALL_S stops the AC the same way.
            action = Action(Mode.OFF, None, "sensor stalled" if result.get("sensors_stalled") else "no room reading")
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
            action = decide(state, self._settings)
            if night == "hold" and action.mode is Mode.COOL:
                # The night hold keeps a start off at comfort. A running AC keeps its setpoint at comfort.
                running_now = self.hass.states.get(climate_id)
                if running_now is None or running_now.state != "cool":
                    action = Action(Mode.OFF, None, "night hold: AC does not start")
                else:
                    action = Action(Mode.COOL, comfort_c, "night hold: setpoint holds comfort")
        self._ac_cooling_tick = self._ac_cooling_tick or action.mode is Mode.COOL
        # The MPC plan's first step can idle a cool decision, such as a pre-cool start, when the room is well below the threshold.
        # Without a plan (no fit or no price slots) the decision stands.
        # An idle step turns cool off. It never starts heat, so the heat branch below is skipped for that tick.
        plan = result.get("mpc_plan")
        # A night pre-cool is judged against its comfort target, so the plan cannot idle it against the away setback.
        mpc_cool_c = comfort_c if night == "pre-cool" else self._cool_above_c
        mpc_idled = bool(plan and rooms and action.mode is Mode.COOL and mpc_idle(plan[0], statistics.median(rooms), mpc_cool_c, MPC_DEADBAND_C))
        if mpc_idled:
            action = Action(Mode.OFF, None, "mpc idle: room below target")
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
        # A CO2 rate between 0 and 1 runs the fan for that share of each PWM window. PM2.5 still forces it on.
        # At 0 or 1 the fan follows the on-off rule above.
        co2_rate = ventilation_rate_from_co2(_float(self._optional(CONF_CO2)))
        fan_on = ventilation.run
        if 0.0 < co2_rate < 1.0:
            pm25_on = air_quality_ventilation(None, _float(self._optional(CONF_PM25))).run
            fan_on = pwm_on(co2_rate * 100.0, FAN_PWM_WINDOW_MIN, now / 60.0) or pm25_on
        # The economizer fan_assist phase runs the fan too, when the economizer_fan option is on and the gates pass.
        house_home = effective_home(True, house_mode_from_store(self.hass.data.get(DOMAIN, {})))
        if fan_assist_on(result.get("economizer"), bool(self.entry.data.get(CONF_ECONOMIZER_FAN, False)), lock_allows_control(self._locked), house_home, stalled):
            fan_on = True
        await self._drive_switch(self.entry.data.get(CONF_FAN), fan_on)
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
            since_min = (now - self._state_changed_at) / 60.0 if self._state_changed_at is not None else 1e9
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

    async def _write_fan_mode(self, climate_id: str, climate: State, gap_c: float) -> None:
        """Set the AC fan mode from the room gap. Sends only when the device lists the mode and it differs from the current one."""
        mode = fan_mode_for_gap(gap_c, climate.attributes.get("fan_modes"))
        if mode is None or climate.attributes.get("fan_mode") == mode:
            return
        await self.hass.services.async_call("climate", "set_fan_mode", {"entity_id": climate_id, "fan_mode": mode}, blocking=True)

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
            answer = await self._ask_model(f"In one sentence, explain why the AC is {mode_now}. {investigation['answer']}", self._budget)
            # A failed or budget-blocked call clears the stored answer, so it never shows under a different mode.
            # The mode is recorded only after a reply, so the next tick asks again while the budget allows.
            self._ai_answer = answer or None
            self._ai_answer_mode = mode_now if answer else None
            if answer:
                self._last_ai_mode = mode_now
        result["investigation_ai"] = self._ai_answer
        result["investigation_ai_mode"] = self._ai_answer_mode
        result["ai_calls_today"] = self._budget.used(dt_util.now().date())
        result.update(self._report)

    def _day_readings(self, now_min: float) -> list[DayReading]:
        """Stored readings from the last 24 hours."""
        cutoff = now_min - REPORT_WINDOW_MIN
        return [DayReading(e.t / 60.0, e.room_c, e.outdoor_c, e.mode) for e in self._entries if e.t / 60.0 >= cutoff]

    async def async_generate_investigation_report(self) -> None:
        """Build the 24-hour report. Ask the conversation agent when one is set and the on-demand budget allows.

        Otherwise, or when the agent gives no answer, the report is the rule-based summary and its source is "rule".
        """
        now_min = dt_util.utcnow().timestamp() / 60.0
        readings = self._day_readings(now_min)
        text, source = day_summary(readings, now_min), "rule"
        if readings and self.entry.data.get(CONF_AI_CONVERSATION):
            answer = await self._ask_model(f"{REPORT_INSTRUCTION}\n\n{day_context(readings, now_min)}", self._report_budget)
            if answer:
                text, source = answer, "model"
        self._report = {"investigation_report": text, "investigation_report_source": source, "investigation_report_at": dt_util.now().isoformat()}
        if self.data is not None:
            self.data.update(self._report)
        self.async_update_listeners()

    async def _ask_model(self, text: str, budget: CallBudget) -> str | None:
        """One call to the conversation agent, if `budget` allows. Returns the reply text, or None."""
        agent = self.entry.data.get(CONF_AI_CONVERSATION)
        limit = int(self.entry.data.get(CONF_AI_BUDGET) or 0)
        if not agent or not budget.try_take(dt_util.now().date(), limit):
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
        The ac_heat option, or a heat plan whose first step says heat (mpc_heat_plan), allows the start.
        A stalled sensor, a paused window and a closed heating window (outdoor limit) also stop it.
        """
        plan = result.get("mpc_heat_steps")
        plan_heat = bool(self.entry.data.get(CONF_MPC_HEAT_PLAN, False) and plan and plan[0])
        return (
            action.mode is Mode.OFF
            and (bool(self.entry.data.get(CONF_AC_HEAT, False)) or plan_heat)
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
        modes = climate.attributes.get("hvac_modes")
        if modes is not None and "heat" not in modes:
            # The device has no heat mode, so the AC cannot heat. Do not send a mode it does not list.
            return False
        if climate.state != "heat":
            since_min = (now - self._state_changed_at) / 60.0 if self._state_changed_at is not None else 1e9
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
                if stagger_allowed("climate_2", wanting, self._last_start_min, now / 60.0) and self._group_start_ok(now):
                    if not self._second_minimum_ok(second.state, now):
                        result["second_ac"] = "waiting for minimum time"
                        return
                    self._record_group_start(now)
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
        registry[self.entry.entry_id] = (group, climate is not None and climate.state == "cool")
        result["house_cooling"] = house_cooling(registry, self.entry.entry_id, group)

    def median_room_c(self) -> float | None:
        """Median of the room readings, or None when there are none."""
        rooms = self._rooms()
        return statistics.median(rooms) if rooms else None

    def set_peer_request(self, requester_id: str, active: bool) -> None:
        """Record whether the entry `requester_id` asks this entry's valve to open. Its room is at least 2 K warmer."""
        requests = self.hass.data.setdefault(DOMAIN, {}).setdefault("peer_requests", {}).setdefault(self.entry.entry_id, set())
        if active:
            requests.add(requester_id)
        else:
            requests.discard(requester_id)

    def _peer_open_requested(self) -> bool:
        return bool(self.hass.data.get(DOMAIN, {}).get("peer_requests", {}).get(self.entry.entry_id))

    def set_peer_setpoint_request(self, requester_id: str, active: bool) -> None:
        """Record whether the entry `requester_id` asks this entry to lower its cool setpoint. Its room is at least 2 K warmer."""
        requests = self.hass.data.setdefault(DOMAIN, {}).setdefault("peer_setpoint_requests", {}).setdefault(self.entry.entry_id, set())
        if active:
            requests.add(requester_id)
        else:
            requests.discard(requester_id)

    def _peer_setpoint_requested(self) -> bool:
        return bool(self.hass.data.get(DOMAIN, {}).get("peer_setpoint_requests", {}).get(self.entry.entry_id))

    def _remember_peer_setpoint(self, climate: State) -> None:
        """Keep the AC setpoint from before the first peer lowering. A later lowering keeps the first value."""
        current = climate.attributes.get("temperature")
        if self._peer_setpoint_original is None and current is not None:
            self._peer_setpoint_original = float(current)

    def _peer_coordinator(self) -> HorizonCoordinator | None:
        """The coordinator of the entry named by peer_entry (entry_id or title), or None when it is unset or not loaded."""
        ref = str(self.entry.data.get(CONF_PEER_ENTRY) or "").strip()
        if not ref:
            return None
        peer = self.hass.config_entries.async_get_entry(ref)
        if peer is None:
            peer = next((e for e in self.hass.config_entries.async_entries(DOMAIN) if e.title == ref), None)
        if peer is None or peer.entry_id == self.entry.entry_id or not hasattr(peer, "runtime_data"):
            return None
        return peer.runtime_data

    def _request_peer_valve(self) -> None:
        """Ask the peer entry to open its valve while this room is at least 2 K warmer than the peer's room.

        The peer applies the request in its own valve command, so the two entries do not write the same valve.
        A request is withdrawn when the condition ends.
        """
        peer = self._peer_coordinator()
        if peer is None:
            return
        own, other = self.median_room_c(), peer.median_room_c()
        warmer = own is not None and other is not None and peer_warmer(own, other)
        # With peer_setpoint_mode the request lowers the peer's setpoint instead. Each request kind is withdrawn when unused.
        setpoint_mode = bool(self.entry.data.get(CONF_PEER_SETPOINT_MODE, False))
        peer.set_peer_request(self.entry.entry_id, warmer and not setpoint_mode)
        peer.set_peer_setpoint_request(self.entry.entry_id, warmer and setpoint_mode)

    def forget_peer_requests(self) -> None:
        """Withdraw this entry's valve requests on unload, so a removed room cannot keep another valve open."""
        for kind in ("peer_requests", "peer_setpoint_requests"):
            for requests in self.hass.data.get(DOMAIN, {}).get(kind, {}).values():
                requests.discard(self.entry.entry_id)

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
        since_min = (now - self._second_changed_at) / 60.0 if self._second_changed_at is not None else 1e9
        return minimum_time_ok(
            state in ("cool", "heat"),
            since_min,
            self.entry.data.get(CONF_MIN_ON_MIN, 5.0),
            self.entry.data.get(CONF_MIN_OFF_MIN, 3.0),
        )

    def _precool_start(self, now: float, rooms: list[float]) -> float | None:
        """Pre-cool start from the planner. Needs a schedule entity whose next_event attribute is an ISO time."""
        schedule = self._optional(CONF_SCHEDULE)
        active = self._active_fit()
        if schedule is None or active is None:
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
            active,
            statistics.median(rooms),
            setpoint,
            outdoor,
            power_kw,
            (deadline - now) / 60.0,
            self._price_forecast(now),
        )

    def _forecast_model(self, active: Fit, power_kw: float, now: float) -> tuple[Fit, float]:
        """With a second AC and a valid two-device fit, the forecast uses the combined fit and both running powers.

        Otherwise the single-device fit and the power already chosen stand.
        """
        if self._room_model is None:
            return active, power_kw
        means = {
            "ac": running_power_kw(self._entries, now) or 0.0,
            "ac_2": running_power_kw(self._entries, now, field="power_2_w") or 0.0,
        }
        combined = forecast_fit(self._room_model, means)
        if combined is None or combined[0].c <= 0:
            return active, power_kw
        return combined

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
            "accuracy_pairs": accuracy_pairs(self._backtest_runs),
            "room_fit": self._room_fit,
            "room_rate_coefficients": None if self._tpi is None else {"k_per_h": round(self._tpi.k, 4), "g_k_per_h": round(self._tpi.g, 4), "c_k_per_h_per_kw": round(self._tpi.c, 4), "samples_off": self._tpi.samples_off, "samples_on": self._tpi.samples_on},
            "auto_tpi_gains": {"cool": round(self._auto_tpi_gain, 4), "heat": round(self._auto_heat_gain, 4)},
        }
        active = self._active_fit()
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

        active, power_kw = self._forecast_model(active, power_kw, now)
        f = forecast(active, room, target, outdoor, power_kw, self._price_forecast(now))
        result.update(
            status="reachable" if f.reachable else "unreachable",
            minutes=None if f.minutes is None else round(f.minutes, 1),
            energy_kwh=None if f.energy_kwh is None else round(f.energy_kwh, 3),
            cost=None if f.cost is None else round(f.cost, 3),
            reason=f.reason,
        )
        return result
