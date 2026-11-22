"""Learning: refits of the room model and fleet rates, online cooling and heating gains, the gain suggestion and the signals derived from learned state."""

from __future__ import annotations

import statistics
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_CLIMATE_2,
    CONF_HEATER_KD,
    CONF_HEATER_KI,
    CONF_HEATER_KP,
    CONF_HEATER_SWITCH,
    CONF_POWER_2,
    DEFAULT_HEATER_KD,
    DEFAULT_HEATER_KI,
    DEFAULT_HEATER_KP,
    KEEP_DAYS,
)
from custom_components.hvac_horizon.engine.fleet import COOL, HEAT, calibrate_rates
from custom_components.hvac_horizon.room_model.collector import (
    Entry,
    fleet_samples_from_entries,
    has_enough_cycles,
    heat_pairs_from_entries,
    humidity_rates_from_entries,
    mode_interval_counts,
    mode_samples_from_entries,
    room_samples_from_entries,
    samples_from_entries,
)
from custom_components.hvac_horizon.room_model.model import Fit, NotEnoughData, Sample, fit
from custom_components.hvac_horizon.room_model.corrections import Corrections, learn_corrections
from custom_components.hvac_horizon.room_model.kept import Kept
from custom_components.hvac_horizon.room_model.mode_models import RateFit, learn_ac_heat, learn_dry
from custom_components.hvac_horizon.room_model.devices import HEATER, RoomFit, fit_room
from custom_components.hvac_horizon.room_model.tpi import (
    TpiFit,
    adjust_cool_gain,
    adjust_heat_gain,
    learn_heat_rate,
    learn_room_rates,
)
from custom_components.hvac_horizon.room_model.backfill import backtest, median_abs_error
from custom_components.hvac_horizon.engine.features import compliance_score, learning_suggestion, pid_gain

RECENT_FIT_DAYS = 14  # the fit uses the readings of this many days when they are enough


class LearningMixin:
    # Instance attributes set in HorizonCoordinator.__init__ and used here.
    _ac_heat_fit: RateFit | None
    _ac_heat_kw: float | None
    _auto_heat_gain: float
    _auto_tpi_gain: float
    _backtest_runs: list
    _corrections: Corrections | None
    _dry_fit: RateFit | None
    _dry_kw: float | None
    _fit_window_days: int
    _baseline_k: float | None
    _device_rates: dict[str, dict[str, float]]
    _entries: list[Entry]
    _fit: Fit | None
    _fit_reason: str
    _forecast_error_min: float | None
    _heat_rate: float | None
    _heater_gains: dict[str, float] | None
    _in_band_min: float
    _kept: Kept
    _kept_used: set[str]
    _last_cycle_t: float | None
    _mode_counts: dict[str, int]
    _room_fit: dict[str, Any] | None
    _room_model: RoomFit | None
    _total_min: float
    _tpi: TpiFit | None
    entry: ConfigEntry

    def _heater_gain_values(self) -> tuple[float, float, float]:
        """kp, ki, kd for the heater PID. Each gain field the user set wins. A field at its default takes the tuned value."""
        tuned = self._heater_gains or {}
        return (
            pid_gain(float(self.entry.data.get(CONF_HEATER_KP, DEFAULT_HEATER_KP)), DEFAULT_HEATER_KP, tuned.get("kp")),
            pid_gain(float(self.entry.data.get(CONF_HEATER_KI, DEFAULT_HEATER_KI)), DEFAULT_HEATER_KI, tuned.get("ki")),
            pid_gain(float(self.entry.data.get(CONF_HEATER_KD, DEFAULT_HEATER_KD)), DEFAULT_HEATER_KD, tuned.get("kd")),
        )

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

    def _refit_fleet(self) -> None:
        """Fit each fleet device's rate from the intervals where it ran alone. A device without enough data keeps its stored rate."""
        devices = self._fleet()
        if not devices:
            return
        self._device_rates.update(calibrate_rates(fleet_samples_from_entries(self._entries), devices))

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
        self._kept_used = set()
        self._refit_cooling()
        self._refit_modes()

    def _keep(self, name: str, value: Any) -> Any:
        """Record a new value with the time of the newest reading. With no new value, return the kept one.

        The readings cover 30 days, so a device that stays off for longer leaves nothing to fit. Its last model serves until it runs again.
        """
        if value is not None:
            if self._entries:
                self._kept[name] = (value, self._entries[-1].t)
            return value
        if name not in self._kept:
            return None
        self._kept_used.add(name)
        return self._kept[name][0]

    def _kept_ages(self, now: float) -> dict[str, float] | None:
        """Age in days of each model in use that comes from the kept values. None when every model is from the readings."""
        if not self._kept_used:
            return None
        return {name: round(max(0.0, now - self._kept[name][1]) / 86400.0, 1) for name in sorted(self._kept_used)}

    def _refit_modes(self) -> None:
        """Fit the AC's heating per kW, the drying rate and the corrections for sun, outdoor temperature and humidity."""
        self._ac_heat_fit = self._dry_fit = self._corrections = None
        self._ac_heat_kw = self._dry_kw = None
        base = self._tpi or self._fit
        if base is not None:
            heat = mode_samples_from_entries(self._entries, "heat")
            try:
                self._ac_heat_fit = learn_ac_heat(heat, base.k, base.g)
                self._ac_heat_kw = statistics.fmean(s.power_kw for s in heat) if heat else None
            except NotEnoughData:
                pass
        self._ac_heat_fit = self._keep("ac_heat", self._ac_heat_fit)
        self._ac_heat_kw = self._keep("ac_heat_kw", self._ac_heat_kw)
        dry = humidity_rates_from_entries(self._entries, "dry")
        try:
            self._dry_fit = learn_dry(dry)
            self._dry_kw = statistics.fmean(kw for kw, _ in dry) if dry else None
        except NotEnoughData:
            pass
        self._dry_fit = self._keep("dry", self._dry_fit)
        self._dry_kw = self._keep("dry_kw", self._dry_kw)
        active = self._active_fit()
        if self._kept_used & {"fit", "tpi"}:
            # A kept room model takes the corrections that were learned with it.
            self._corrections = self._keep("corrections", None)
        elif active is not None:
            self._corrections = learn_corrections(samples_from_entries(self._entries), active)
            if self._corrections is None:
                self._kept.pop("corrections", None)
            else:
                self._keep("corrections", self._corrections)

    def _recent_samples(self, samples: list[Sample]) -> tuple[list[Sample], int]:
        """The samples of the last RECENT_FIT_DAYS when they alone give a fit, else every sample. Returns the days used.

        A room changes with the season, so readings from this fortnight describe it better than older ones.
        """
        if not self._entries:
            return samples, KEEP_DAYS
        newest = max(e.t for e in self._entries)
        if newest - min(e.t for e in self._entries) <= RECENT_FIT_DAYS * 86400.0:
            return samples, KEEP_DAYS
        recent = samples_from_entries([e for e in self._entries if e.t >= newest - RECENT_FIT_DAYS * 86400.0])
        if not has_enough_cycles(recent):
            return samples, KEEP_DAYS
        try:
            fit(recent)
        except NotEnoughData:
            return samples, KEEP_DAYS
        return recent, RECENT_FIT_DAYS

    def _refit_cooling(self) -> None:
        every = samples_from_entries(self._entries)
        samples, self._fit_window_days = self._recent_samples(every)
        self._mode_counts = mode_interval_counts(self._entries)
        self._refit_room_model()
        self._refit_fleet()
        try:
            self._tpi = learn_room_rates(samples)
        except NotEnoughData:
            self._tpi = None
        self._tpi = self._keep("tpi", self._tpi)
        self._heat_rate = None
        if self._tpi is not None:
            try:
                self._heat_rate = learn_heat_rate(heat_pairs_from_entries(self._entries), self._tpi.k, self._tpi.g)
            except NotEnoughData:
                self._heat_rate = None
        self._heat_rate = self._keep("heat_rate", self._heat_rate)
        self._backtest_runs = []
        self._fit = None
        self._fit_reason = "need at least 3 hours of AC on and off time over 3 runs"
        if has_enough_cycles(samples):
            try:
                self._fit = fit(samples)
                self._fit_reason = "fitted"
            except NotEnoughData as err:
                self._fit_reason = str(err)
        if self._fit is None:
            self._fit = self._keep("fit", None)
            if self._fit is not None:
                self._fit_reason = "kept from the last AC runs"
            return
        self._keep("fit", self._fit)
        target = self.comfort_c
        self._backtest_runs = backtest(every, target_c=target, min_train=60, margin_c=1.0)
        self._forecast_error_min = median_abs_error(self._backtest_runs)

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

    def _learning_suggestion(self) -> str | None:
        """Compare the fitted heat gain with the current model gain (the first fit, kept per run)."""
        if self._fit is None:
            return None
        if self._baseline_k is None:
            self._baseline_k = self._fit.k
            return None
        return learning_suggestion(self._baseline_k, self._fit.k)
