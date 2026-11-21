"""Learning: refits of the room model and fleet rates, online cooling and heating gains, the gain suggestion and the signals derived from learned state."""

from __future__ import annotations

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
)
from custom_components.hvac_horizon.fleet import COOL, HEAT, calibrate_rates
from custom_components.hvac_horizon.collector import (
    Entry,
    fleet_samples_from_entries,
    has_enough_cycles,
    heat_pairs_from_entries,
    mode_interval_counts,
    room_samples_from_entries,
    samples_from_entries,
)
from custom_components.hvac_horizon.model import Fit, NotEnoughData, fit
from custom_components.hvac_horizon.devices import HEATER, RoomFit, fit_room
from custom_components.hvac_horizon.tpi import (
    TpiFit,
    adjust_cool_gain,
    adjust_heat_gain,
    learn_heat_rate,
    learn_room_rates,
)
from custom_components.hvac_horizon.backfill import backtest, median_abs_error
from custom_components.hvac_horizon.features import compliance_score, learning_suggestion, pid_gain


class LearningMixin:
    # Instance attributes set in HorizonCoordinator.__init__ and used here.
    _auto_heat_gain: float
    _auto_tpi_gain: float
    _backtest_runs: list
    _baseline_k: float | None
    _device_rates: dict[str, dict[str, float]]
    _entries: list[Entry]
    _fit: Fit | None
    _fit_reason: str
    _forecast_error_min: float | None
    _heat_rate: float | None
    _heater_gains: dict[str, float] | None
    _in_band_min: float
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
        samples = samples_from_entries(self._entries)
        self._mode_counts = mode_interval_counts(self._entries)
        self._refit_room_model()
        self._refit_fleet()
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
        target = self.comfort_c
        self._backtest_runs = backtest(samples, target_c=target, min_train=60, margin_c=1.0)
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
