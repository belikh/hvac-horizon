"""Samples the room every SAMPLE_INTERVAL_S, refits the model and publishes the forecast."""

from __future__ import annotations

import statistics
from typing import Any
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from custom_components.hvac_horizon.investigator import ActionRecord, CallBudget
from .const import (
    CONF_IRRADIANCE,
    WEATHER_REFRESH_MIN,
    BOILER_BELOW_C,
    CONF_AC_HEAT,
    CONF_AWAY_C,
    CONF_BOOST_C,
    CONF_CLIMATE,
    CONF_CO2,
    CONF_COMFORT_ENERGY_WEIGHT,
    CONF_CONTROL,
    CONF_COVER,
    CONF_ECO_C,
    CONF_HEATER_ALGORITHM,
    CONF_HEAT_BOOST_C,
    CONF_HEAT_ECO_C,
    CONF_MAX_ON_PERCENT,
    CONF_MOTION,
    CONF_MOTION_AWAY_MIN,
    CONF_MPC_HEAT_PLAN,
    CONF_OUTDOOR,
    CONF_PRICE,
    CONF_ROOM_HUMIDITY,
    CONF_SCHEDULE_BLOCKS,
    CONF_SHED_LOADS,
    CONF_SHED_MAX_W,
    CONF_TPI_COEF_EXT,
    CONF_TPI_COEF_INT,
    CONF_TPI_THRESHOLD_HIGH,
    CONF_TPI_THRESHOLD_LOW,
    CONF_TRV_TEMPERATURE,
    CONF_VACATION,
    CONF_VACATION_C,
    CONF_VALVE,
    CONF_WHOLE_HOUSE_POWER,
    CONF_WINDOW_DELAY_MIN,
    COVER_SOLAR_THRESHOLD_KW,
    DEFAULT_AWAY_C,
    DEFAULT_COMFORT_C,
    DEFAULT_HEATER_DEADBAND_C,
    DEFAULT_MOTION_AWAY_MIN,
    DEFAULT_TPI_COEF_EXT,
    DEFAULT_TPI_COEF_INT,
    DEFAULT_VACATION_C,
    DEFAULT_WINDOW_DELAY_MIN,
    DOMAIN,
    FIT_EVERY_N_SAMPLES,
    FROST_FLOOR_C,
    HEATER_ALGORITHM_TPI,
    KEEP_DAYS,
    MAX_ENTRIES,
    SAMPLE_INTERVAL_S,
    STORE_VERSION,
    TRV_HISTORY,
)
from custom_components.hvac_horizon.collector import Entry, running_power_kw
from custom_components.hvac_horizon.model import Fit
from custom_components.hvac_horizon.pid import Pid
from custom_components.hvac_horizon.decide import Preset, Settings, cool_above_c, raise_cool_thresholds
from custom_components.hvac_horizon.autotune import RelayTuner
from custom_components.hvac_horizon.devices import RoomFit
from custom_components.hvac_horizon.features import (
    ScheduleBlock,
    band_action,
    boiler_demand,
    boost_output,
    comfort_band,
    comfort_energy_setpoint,
    corrected_setpoint,
    cover_action,
    drying_needed,
    economizer_phase,
    effective_home,
    fan_speed_from_gap,
    heat_on_below,
    heat_setpoint,
    heat_source,
    house_mode_from_store,
    inhibited,
    learned_solar_scale,
    motion_preset,
    mpc_heat_plan,
    mpc_plan,
    outdoor_allows,
    price_shifted_target,
    room_devices,
    safety_output,
    scheduled_preset,
    shed_plan,
    shed_restore,
    stale_price_fallback,
    suggestion_visible,
    switch_feedback,
    trv_offset,
    vacation_target,
    valve_opening_pct,
    ventilation_rate_from_co2,
    weather_day_summary,
)
from custom_components.hvac_horizon.tpi import TpiFit, tpi_on_percent, tpi_threshold_forced_off
from custom_components.hvac_horizon.forecast import price_at
from custom_components.hvac_horizon.outdoor import HourlyPoint, OutdoorBias, OutdoorSeries
from custom_components.hvac_horizon.coordinator_store import StoreMixin
from custom_components.hvac_horizon.coordinator_user import UserMixin
from custom_components.hvac_horizon.coordinator_learning import LearningMixin
from custom_components.hvac_horizon.coordinator_forecast import ForecastMixin
from custom_components.hvac_horizon.coordinator_inputs import InputsMixin
from custom_components.hvac_horizon.coordinator_heater import HeaterMixin
from custom_components.hvac_horizon.coordinator_safety import SafetyMixin
from custom_components.hvac_horizon.coordinator_fleet import FleetMixin
from custom_components.hvac_horizon.coordinator_aux import AuxMixin
from custom_components.hvac_horizon.coordinator_peers import PeersMixin
from custom_components.hvac_horizon.coordinator_investigator import InvestigatorMixin
from custom_components.hvac_horizon.coordinator_ac import AcMixin
from custom_components.hvac_horizon.coordinator_common import _LOGGER, _float, _optional_float, MPC_STEP_MIN, MPC_STEPS, OUTDOOR_AHEAD_H, MPC_HEAT_COST_WEIGHT, SAFETY_FALLBACK_PCT, _HorizonStore


class HorizonCoordinator(StoreMixin, UserMixin, LearningMixin, ForecastMixin, InputsMixin, HeaterMixin, SafetyMixin, FleetMixin, AuxMixin, PeersMixin, InvestigatorMixin, AcMixin, DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(seconds=SAMPLE_INTERVAL_S),
        )
        self.entry = entry
        self._store: Store[dict[str, Any]] = _HorizonStore(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._entries: list[Entry] = []
        self._fit: Fit | None = None
        self._fit_reason = "not enough data"
        self._samples_since_fit = 0
        self._mode_counts: dict[str, int] = {}
        self._window_open_since: float | None = None
        self._state_changed_at: float | None = None
        self._fleet_changed_at: dict[str, float] = {}  # device_id -> when the dispatch last changed it, for minimum times
        self._device_rates: dict[str, dict[str, float]] = {}  # device_id -> fitted {rate, samples}, kept in the store
        self._second_changed_at: float | None = None  # when the second AC last changed mode, for its minimum times
        self._saved_commanded: dict[str, Any] = {}  # the commanded state as last queued for the store
        self._shed_at: dict[str, float] = {}  # load -> when this entry shed it; stored with the record
        self._shed_context: dict[str, str] = {}  # load -> id of the context of this entry's turn_off command; memory only
        self._loaded_at: float = 0.0  # when the store was read; a restored record cannot see changes before it
        self._shed_loads: set[str] = set()  # loads this integration switched off for the power limit, to restore later
        self._price_warned = False  # one warning per coordinator for invalid price thresholds
        self._fleet_warned = False  # one warning per coordinator for unknown fleet keys
        self._heater_on_min: float = 0.0
        self._heater_check_t: float | None = None
        self._heater_gated = False
        self._heater_step_done = False
        self._ac_cooling_tick = False
        self._heating_fail_reported = False
        self._baseline_k: float | None = None
        self._last_home_at: float | None = None
        self._last_cycle_t: float | None = None
        self._in_band_min: float = 0.0
        self._outdoor_history: list[float] = []
        self._last_good_price: float | None = None
        self._tuner: RelayTuner | None = None
        self._tuner_started_s: float = 0.0
        self._autotune_status: str | None = None  # running, done, timed_out, cancelled, reached_cutoff; None before any test
        self._stall_reported = False
        # Time of the last cycle with a valid room reading. Starts at the first cycle, so a setup with no room data counts from then.
        self._last_room_data_s: float | None = None
        self._locked = False
        self._target_override_c: float | None = None  # the target the user set on the climate entity; stored
        self._user_off = False  # the user turned the room off on the climate entity; stored
        self._user_mode = "auto"  # the mode the user chose on the climate entity: "cool", "heat" or "auto"; stored with the hold
        self._manual_preset: str | None = None  # a preset the user chose on the climate entity
        self._manual_scheduled: str | None = None  # the scheduled preset when the manual one started; a change of schedule ends it
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
        self._forecast_daily: list[dict] = []  # the last good daily forecast items
        self._hourly: list[HourlyPoint] = []  # the last good hourly forecast
        self._outdoor_bias = OutdoorBias()  # sensor minus forecast, per hour of day; kept in the store
        self._hourly_at: float | None = None  # epoch seconds of the last good hourly fetch
        self._series_cache: tuple[tuple, OutdoorSeries | None] | None = None
        self._weather_warned: set[str] = set()  # forecast types whose last fetch failed and was logged
        self._weather_checked_min: float | None = None
        self._pwm_output_pct: float = 0.0
        self._last_sent_min: float | None = None
        self._heater_commanded_on: bool | None = None
        # Switch entity and the state last sent to it. Unload uses these, not the current entry options.
        self._heater_switch_driven: str | None = None
        # Optional switches (fan, humidifier, dehumidifier, boiler, whole-house fan) this entry last commanded on. The safety stop turns them off.
        self._switches_on: set[str] = set()
        self._second_ac_commanded_on = False  # the second AC was last commanded to cool
        self._dry_floor_hold = False  # dry mode paused the AC because the room fell DRY_FLOOR_BELOW_HEAT_K below the heat threshold
        self._coil_dry_sent = False  # this entry put the first AC in dry mode and has not turned it off
        self._valve_driven: str | None = None  # valve entity and the opening last sent to it
        self._valve_commanded_pct: float = 0.0
        self._inputs_seen = False  # room and outdoor readings have been valid at least once since setup; no on-command goes out before
        self._pwm_cancel: list[Callable[[], None]] = []
        self._settings: Settings | None = None
        self._someone_home = True
        self._preset_now: str | None = None
        self._cool_above_c: float = DEFAULT_COMFORT_C
        self._window_advice_open = False  # the last window advice was "open"; it carries the deadband
        self._economizer_open = False  # the last economizer phase was not "off"
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
        self._precool_status: str | None = None
        self._precool_warned: str | None = None
        self._lock_notified = False
        self._inhibit_until_min: float | None = None
        self._inhibited_for_setpoint: float | None = None  # the manual value that last armed the inhibition
        self._last_written_setpoint: float | None = None
        self._regulation_drop_k = 0.0  # power fallback: how far the setpoint is moved from the target, K
        self._regulation_mode: str | None = None  # the mode that drop belongs to
        self._regulation_offset_k: float | None = None  # the offset used this cycle, published as ac_sensor_offset_k
        self._regulation_sent_c: float | None = None  # the setpoint sent this cycle, published as ac_setpoint_sent_c
        self._regulation_stepped_s: float | None = None  # when the power fallback last moved the setpoint
        self._pending_waits: list[tuple[float, str | None]] = []  # starts the restart delay holds this cycle, for `pending`
        self._recheck_cancel: Callable[[], None] | None = None  # cancels the re-check set for the end of a restart delay
        self._store_loaded = False  # the store has been read; no tick runs before it
        # The AC setpoint before a peer request first lowered it. The restore writes it back when the request ends with the AC off.
        self._peer_setpoint_original: float | None = None
        self._autotune_result = None
        self._last_good_price_at: float | None = None
        self._total_min: float = 0.0
        self._heater_start_temp: float | None = None
        self._backtest_runs: list = []
        self._forecast_error_min: float | None = None

    async def async_shutdown(self) -> None:
        self._cancel_recheck()
        await super().async_shutdown()

    async def _async_update_data(self) -> dict[str, Any]:
        """Run one tick. The heater safety step runs on every tick, also when an earlier step raises."""
        if not self._store_loaded:
            await self.async_load()  # the stored user hold must be known before any command goes out
        now = dt_util.utcnow().timestamp()
        result: dict[str, Any] = {}
        self._heater_step_done = False
        try:
            return await self._async_update_steps(now, result)
        except Exception:  # KeyboardInterrupt, SystemExit and CancelledError pass through; the unload and stop paths turn devices off
            if not self._heater_step_done:
                # A step failed before the heater step. Close the gate and cancel the PWM timers, then turn the heater off.
                self._heater_gated = True
                self._pwm_output_pct = 0.0
                self._cancel_pwm_timers()
                try:
                    await self._async_heater_step(now, result)
                except Exception:  # the original error is the one to raise
                    _LOGGER.exception("Heater safety step failed after an earlier step raised")
            raise

    async def _async_update_steps(self, now: float, result: dict[str, Any]) -> dict[str, Any]:
        now_min = now / 60.0
        if self._weather_checked_min is None or now_min - self._weather_checked_min >= WEATHER_REFRESH_MIN:
            self._weather_checked_min = now_min
            await self._refresh_weather()
        if self._rooms() and self._outdoor_now() is not None:
            self._inputs_seen = True
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
        result.update(self._forecast(now))
        series = self._outdoor_series()
        result["outdoor_forecast_used"] = series is not None
        result["outdoor_forecast_next_c"] = None if series is None else [round(series(60.0 * h), 1) for h in OUTDOOR_AHEAD_H]
        result["outdoor_source"] = self._outdoor_reading()[1]
        result["outdoor_bias_now_k"] = round(self._bias_at(now), 2)
        result["outdoor_bias_pairs"] = self._outdoor_bias.total
        result["sensors_stalled"] = self._stalled_sensors(now)
        result["rooms_max_age_s"] = self._rooms_max_age_s(now)
        self._report_stalls(result["sensors_stalled"])
        if self._rooms() or self._last_room_data_s is None:
            self._last_room_data_s = now
        result["external_change"] = self._external_change()
        result["day_type"] = self._day_type()
        result["forecast_days"] = weather_day_summary(self._forecast_highs) if self._forecast_highs else None
        result["night_plan"] = self._night_plan()
        result.setdefault("precool_status", self._precool_status)
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
        result["solar_factor"] = self._solar_factor()
        if modelled_kw is not None and result["solar_gain_kw"] is not None and self.entry.data.get(CONF_IRRADIANCE):
            self._solar_pairs.append((modelled_kw, result["solar_gain_kw"]))
            del self._solar_pairs[:-288]
        result["solar_scale"] = learned_solar_scale([m for m, _ in self._solar_pairs], [v for _, v in self._solar_pairs]) if self._solar_pairs else None
        result["window_advice"] = self._window_advice()
        self._window_advice_open = result["window_advice"] == "open"
        outdoor_now = self._outdoor_now()
        rooms_econ = self._rooms()
        if outdoor_now is not None and rooms_econ:
            phase = economizer_phase(
                outdoor_now,
                statistics.median(rooms_econ),
                self.comfort_c,
                self._minutes_in_open,
                cool_above_c=self._cool_above_c,
                is_open=self._economizer_open,
            )
            self._economizer_open = phase != "off"
            if phase == "off":
                self._minutes_in_open = 0.0
            elif self._last_economizer_t is not None:
                self._minutes_in_open += (now - self._last_economizer_t) / 60.0
            self._last_economizer_t = now
            result["economizer"] = phase
            source = heat_source(outdoor_now, BOILER_BELOW_C)
            low_edge = self.comfort_c - 1.0
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
        low, high = comfort_band(self.comfort_c)
        rooms_now = self._rooms()
        result["band_action"] = band_action(statistics.median(rooms_now), low, high) if rooms_now else None
        result["mould_risk"] = self._mould_risk()
        result["compliance_pct"] = self._compliance(now, band_action_value=result["band_action"])
        result["heatwave"] = self._heatwave()
        rooms_fan = self._rooms()
        target_fan = self.comfort_c
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
                        outdoor_series=series,
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
                                outdoor_series=series,
                            )
                        ]
        pid_rooms = self._rooms()
        pid_outdoor = self._outdoor_now()
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
            comfort_target = self.comfort_c
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
        if valve_opening is not None and (self._user_off or not self.heating_permitted) and self.entry.data.get(CONF_CONTROL):
            valve_opening = 0.0  # the room is held off or in cool mode, so the valve gives no heat
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
                if restore is not None and self._inputs_seen and await self._drive_restore(restore):
                    result["restored"] = restore
            self._notify_transition("lock", "_lock_notified", self._locked, "HVAC Horizon control is locked.", "HVAC Horizon control is unlocked.")
            self._ac_cooling_tick = False
            if self.entry.data.get(CONF_CONTROL):
                await self._async_control(now, result)
        finally:
            self._heater_step_done = True
            await self._async_heater_step(now, result)
        await self._publish_investigation(now, result)
        heater_switch = (room_devices(self.entry.data)["heating"] or [None])[0]
        heater_state = self.hass.states.get(heater_switch) if heater_switch else None
        result["heater_feedback"] = switch_feedback(
            self._heater_commanded_on if heater_switch else None,
            heater_state.state if heater_state is not None else None,
        )
        result["safety_output_pct"] = safety_output(self._stall_blocks(result), 100.0, fallback_pct=SAFETY_FALLBACK_PCT)
        result["autotune_status"] = self._autotune_status
        result["autotune"] = None if self._autotune_result is None else {"kp": round(self._autotune_result.kp, 3), "ki": round(self._autotune_result.ki, 3), "kd": round(self._autotune_result.kd, 3)}
        if valve_opening is not None and self._inputs_seen:
            await self._drive_valve(valve_opening)
        self._save_commanded_if_changed()
        return result

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
        comfort = self.comfort_c
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
                cheap_per_kwh=self._price_thresholds()[0],
                expensive_per_kwh=self._price_thresholds()[1],
            ),
        )
        mould = bool(result.get("mould_risk"))
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
                self.comfort_c,
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
        # Mould risk raises the cooling threshold of every preset by one degree. The heat target is unchanged.
        settings = raise_cool_thresholds(settings, mould)
        self._settings = settings
        self._cool_above_c = cool_above_c(settings, someone_home)
        heat_target_c = min(heat_target_c, self._cool_above_c)
        self._heat_target_c = heat_target_c
        self._heat_on_below_c = heat_on_below(heat_target_c, DEFAULT_HEATER_DEADBAND_C)
        result["cool_on_above_c"] = round(self._cool_above_c, 2)
        # The Target temperature sensor shows the temperature the AC cools above, so it follows the preset, the user's
        # target, the price shift, the mould bump and occupancy exactly as decide() does.
        if self.entry.data.get(CONF_CONTROL):
            result["target_c"] = result["cool_on_above_c"]
            # The forecast aims at the same cool threshold. It ran before the plan, so it runs again with the planned target.
            planned = {"preset": result.get("preset"), "target_c": result["target_c"]}
            result.update(self._forecast(now, self._cool_above_c))
            result.update(planned)
        result["dispatch"] = self._dispatch()
        result["heat_on_below_c"] = round(self._heat_on_below_c, 2)
