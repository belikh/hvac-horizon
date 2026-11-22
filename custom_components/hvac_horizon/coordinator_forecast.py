"""Forecasting and planning inputs: weather refresh, heatwave and solar estimates, price forecast, the forecast itself, pre-cool and pre-heat timing."""

from __future__ import annotations

import math
from collections.abc import Callable
import statistics
from typing import Any
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CLIMATE,
    CONF_CONTROL,
    CONF_HEATER_POWER_W,
    CONF_HEATER_SWITCH,
    CONF_HEATWAVE_WARNING,
    CONF_IRRADIANCE,
    CONF_OUTDOOR,
    CONF_POWER,
    CONF_PRICE,
    CONF_PRICE_CHEAP,
    CONF_PRICE_EXPENSIVE,
    CONF_ROOM_HUMIDITY,
    CONF_SCHEDULE,
    CONF_WEATHER,
    CONF_WINDOW_AREA_M2,
    CONF_WINDOW_AZIMUTH,
    DEFAULT_PRICE_CHEAP,
    DEFAULT_PRICE_EXPENSIVE,
    DEFAULT_WINDOW_AZIMUTH,
    FORECAST_FRESH_S,
)
from custom_components.hvac_horizon.collector import Entry, price_slots, running_power_kw
from custom_components.hvac_horizon.model import Fit, fit_confidence
from custom_components.hvac_horizon.decide import Preset
from custom_components.hvac_horizon.devices import RoomFit, forecast_fit
from custom_components.hvac_horizon.features import (
    ScheduleBlock,
    forecast_highs,
    hot_streak,
    minutes_until_preset,
    sun_heat_gain,
)
from custom_components.hvac_horizon.tpi import TpiFit, heat_up_minutes
from custom_components.hvac_horizon.backfill import accuracy_pairs, range_coverage
from custom_components.hvac_horizon.solar_model import clear_sky_irradiance, condition_factor, solar_elevation_azimuth, window_irradiance
from custom_components.hvac_horizon.outdoor import HourlyPoint, OutdoorBias, OutdoorSeries, build_series, condition_now, night_low, parse_hourly, temperature_now
from custom_components.hvac_horizon.forecast import Forecast, forecast, forecast_range
from custom_components.hvac_horizon.corrections import Corrections, apply
from custom_components.hvac_horizon.mode_models import RateFit, dry_forecast, heat_forecast
from custom_components.hvac_horizon.control import precool_start_in_min
from custom_components.hvac_horizon.coordinator_common import PRECOOL_LATE_MAX_MIN, _LOGGER, _parse_deadline, _round_or_none, _float, _stored_number as _float_or_none
from custom_components.hvac_horizon.features import HUMIDITY_BAND_HIGH_PCT, HUMIDITY_DEADBAND_PCT
from custom_components.hvac_horizon.mode_models import MIN_MODE_SAMPLES

SOLAR_SHARE_MIN_KW = 0.05  # below this clear-sky gain the share of sun the room gets is not measured
DRY_TARGET_PCT = HUMIDITY_BAND_HIGH_PCT - HUMIDITY_DEADBAND_PCT  # dry mode ends here


class ForecastMixin:
    # Instance attributes set in HorizonCoordinator.__init__ and used here.
    _auto_heat_gain: float
    _auto_tpi_gain: float
    _backtest_runs: list
    _entries: list[Entry]
    _fit: Fit | None
    _fit_reason: str
    _forecast_error_min: float | None
    _forecast_daily: list[dict]
    _forecast_highs: list[float]
    _hourly: list[HourlyPoint]
    _hourly_at: float | None
    _outdoor_bias: OutdoorBias
    _series_cache: tuple[tuple, OutdoorSeries | None] | None
    _store: Any
    _weather_warned: set[str]
    _heat_rate: float | None
    _mode_counts: dict[str, int]
    _outdoor_history: list[float]
    _precool_status: str | None
    _precool_warned: str | None
    _price_warned: bool
    _room_fit: dict[str, Any] | None
    _room_model: RoomFit | None
    _tpi: TpiFit | None
    _ac_heat_fit: RateFit | None
    _ac_heat_kw: float | None
    _corrections: Corrections | None
    _dry_fit: RateFit | None
    _dry_kw: float | None
    _fit_window_days: int
    _heat_target_c: float
    _user_off: bool
    entry: ConfigEntry
    hass: HomeAssistant

    async def _refresh_weather(self) -> None:
        """Fetch the daily and the hourly forecast. A failed fetch keeps the last good result of its type."""
        weather_id = self.entry.data.get(CONF_WEATHER)
        if not weather_id:
            return
        daily = await self._fetch_forecast(weather_id, "daily")
        if daily is not None:
            self._forecast_daily = daily
            self._forecast_highs = forecast_highs(daily)
        hourly = await self._fetch_forecast(weather_id, "hourly")
        if hourly is not None:
            self._hourly = parse_hourly(hourly, dt_util.get_default_time_zone())
            self._hourly_at = dt_util.utcnow().timestamp()
        self._learn_outdoor_bias()

    def _local_hour(self, epoch: float) -> int:
        return datetime.fromtimestamp(epoch, dt_util.get_default_time_zone()).hour

    def _bias_at(self, epoch: float) -> float:
        """The learned sensor-minus-forecast bias for the hour of day of `epoch`."""
        return self._outdoor_bias.for_hour(self._local_hour(epoch))

    def _fresh_outdoor_sensor(self, now: float) -> float | None:
        """The outdoor sensor reading, or None when it is unavailable or stalled."""
        outdoor_id = self.entry.data[CONF_OUTDOOR]
        reading = _float(self.hass.states.get(outdoor_id))
        return None if reading is None or outdoor_id in self._stalled_sensors(now) else reading

    def _learn_outdoor_bias(self) -> None:
        """Record one pair of the outdoor sensor and the forecast for the current time.

        Nothing is recorded without a fresh sensor reading and a fresh hourly forecast that spans now.
        """
        now = dt_util.utcnow().timestamp()
        sensor = self._fresh_outdoor_sensor(now)
        forecast_c = temperature_now(self._hourly, now) if self._hourly_fresh(now) else None  # raw, without the bias
        if sensor is None or forecast_c is None:
            return
        self._outdoor_bias = self._outdoor_bias.record(sensor, forecast_c, self._local_hour(now))
        self._store.async_delay_save(self._save_payload, 900)

    async def _fetch_forecast(self, weather_id: str, kind: str) -> list[dict] | None:
        """The forecast items of one type, or None when the call fails. The first failure in a row logs a warning."""
        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": weather_id, "type": kind},
                blocking=True,
                return_response=True,
            )
            items = response[weather_id]["forecast"]
            if not isinstance(items, list):
                raise TypeError("forecast is not a list")
        except Exception as err:  # a missing weather service must not break the cycle
            if kind not in self._weather_warned:
                self._weather_warned.add(kind)
                _LOGGER.warning("Weather %s forecast unavailable for %s (%s); keeping the last one", kind, weather_id, err)
            return None
        self._weather_warned.discard(kind)
        return items

    def _hourly_fresh(self, now: float) -> bool:
        """True when the hourly forecast was fetched within FORECAST_FRESH_S."""
        return self._hourly_at is not None and bool(self._hourly) and now - self._hourly_at <= FORECAST_FRESH_S

    def _forecast_outdoor(self, now: float) -> float | None:
        """The hourly forecast temperature for the current time plus the learned bias, or None without a fresh forecast that spans it."""
        raw = temperature_now(self._hourly, now) if self._hourly_fresh(now) else None
        return None if raw is None else raw + self._bias_at(now)

    def _outdoor_reading(self) -> tuple[float | None, str]:
        """The outdoor temperature and its source, "sensor" or "forecast".

        A stalled or unavailable outdoor sensor is replaced by the fresh hourly forecast for the current time.
        With no fresh forecast the sensor value stands, which can be None.
        """
        now = dt_util.utcnow().timestamp()
        sensor = _float(self.hass.states.get(self.entry.data[CONF_OUTDOOR]))
        if self._fresh_outdoor_sensor(now) is not None:
            return sensor, "sensor"
        fallback = self._forecast_outdoor(now)
        return (sensor, "sensor") if fallback is None else (fallback, "forecast")

    def _outdoor_now(self) -> float | None:
        """The outdoor temperature for control and planning. See `_outdoor_reading`."""
        return self._outdoor_reading()[0]

    def _outdoor_series(self) -> OutdoorSeries | None:
        """Outdoor temperature per minute from now, or None without a fresh hourly forecast that reaches now.

        The series starts at the measured reading. It is rebuilt once a minute and when a new forecast arrives.
        """
        now = dt_util.utcnow().timestamp()
        if not self._hourly_fresh(now):
            return None
        reading, source = self._outdoor_reading()
        anchor = reading if source == "sensor" else None
        key = (self._hourly_at, int(now // 60), anchor, self._outdoor_bias.total)
        if self._series_cache is not None and self._series_cache[0] == key:
            return self._series_cache[1]
        series = build_series(self._hourly, now, anchor, self._bias_at)
        self._series_cache = (key, series)
        return series

    def _heatwave(self) -> bool:
        """True when the heatwave warning sensor is on, or the daily highs reach 30 C on three days in a row.

        Without a forecast the recent outdoor readings stand in for the highs, three cycles in a row.
        """
        warning = self._optional(CONF_HEATWAVE_WARNING)
        warned = warning is not None and warning.state == "on"
        if self._forecast_highs:
            return hot_streak(self._forecast_highs) or warned
        outdoor = self._outdoor_now()
        if outdoor is None:
            return warned
        self._outdoor_history.append(outdoor)
        self._outdoor_history = self._outdoor_history[-24:]
        return hot_streak(self._outdoor_history) or warned

    def _solar_factor(self) -> float | None:
        """Clear-sky scale from the forecast condition of the current hour, from CONDITION_FACTORS.

        None when an irradiance sensor is configured, there is no window area, no fresh hourly forecast,
        or the condition is not in the table.
        """
        if self.entry.data.get(CONF_IRRADIANCE) or self.entry.data.get(CONF_WINDOW_AREA_M2) is None:
            return None
        now = dt_util.utcnow().timestamp()
        return condition_factor(condition_now(self._hourly, now)) if self._hourly_fresh(now) else None

    def _solar_gain(self) -> float | None:
        """Solar gain in kW on the window. None without a window area.

        With an irradiance sensor the gain comes from its reading. Without one it is the clear-sky model
        scaled by the forecast condition factor, and None when there is no factor.
        """
        area = self.entry.data.get(CONF_WINDOW_AREA_M2)
        if area is None:
            return None
        if self.entry.data.get(CONF_IRRADIANCE):
            irradiance = _float(self._optional(CONF_IRRADIANCE))
            return None if irradiance is None else round(sun_heat_gain(irradiance, float(area)), 3)
        factor = self._solar_factor()
        modelled = self._modelled_solar_kw()
        return None if factor is None or modelled is None else round(modelled * factor, 3)

    def _modelled_solar_kw(self, ahead_min: float = 0.0) -> float | None:
        """Clear-sky gain in kW on the window from the sun position at the house location, `ahead_min` minutes from now. None without a window area."""
        area = self.entry.data.get(CONF_WINDOW_AREA_M2)
        if area is None:
            return None
        utc = dt_util.utcnow() + timedelta(minutes=ahead_min)
        elevation, azimuth = solar_elevation_azimuth(
            self.hass.config.latitude, self.hass.config.longitude,
            utc.hour + utc.minute / 60.0, utc.timetuple().tm_yday,
        )
        window_az = float(self.entry.data.get(CONF_WINDOW_AZIMUTH, DEFAULT_WINDOW_AZIMUTH))
        irradiance = window_irradiance(clear_sky_irradiance(elevation), elevation, azimuth, window_az)
        return sun_heat_gain(irradiance, float(area))

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

    def _price_thresholds(self) -> tuple[float, float]:
        """The cheap and expensive price thresholds. An invalid stored value falls back to the defaults, with one warning."""
        data = self.entry.data

        def number(name: str, default: float) -> float:
            value = data.get(name)
            if value is None:
                return default
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 10:
                return math.nan
            return float(value)

        cheap = number(CONF_PRICE_CHEAP, DEFAULT_PRICE_CHEAP)
        expensive = number(CONF_PRICE_EXPENSIVE, DEFAULT_PRICE_EXPENSIVE)
        if math.isnan(cheap) or math.isnan(expensive) or cheap >= expensive:
            if not self._price_warned:
                self._price_warned = True
                _LOGGER.warning(
                    "Invalid price thresholds (cheap=%r, expensive=%r) for %s; using %s and %s. Fix them in the entry settings.",
                    data.get(CONF_PRICE_CHEAP), data.get(CONF_PRICE_EXPENSIVE), self.entry.title,
                    DEFAULT_PRICE_CHEAP, DEFAULT_PRICE_EXPENSIVE,
                )
            return DEFAULT_PRICE_CHEAP, DEFAULT_PRICE_EXPENSIVE
        return cheap, expensive

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
        outdoor = self._outdoor_now()
        if not rooms or outdoor is None:
            return heat_target_c, "no heat model", None
        rate = self._heat_rate * self._auto_heat_gain
        needed = heat_up_minutes(statistics.median(rooms), comfort_c, outdoor, self._tpi.k, self._tpi.g, rate, outdoor_series=self._outdoor_series())
        if needed is None:
            return heat_target_c, "cannot reach comfort", None
        if lead_min <= needed:
            return comfort_c, "active", needed
        return heat_target_c, "waiting", needed

    def _precool_start(self, now: float, rooms: list[float]) -> float | None:
        """Pre-cool start from the planner. Needs a schedule entity whose next_event attribute is an ISO time."""
        schedule = self._optional(CONF_SCHEDULE)
        active = self._active_fit()
        if schedule is None:
            self._precool_status = "no schedule entity"
            return None
        if active is None:
            self._precool_status = "no room model yet"
            return None
        raw = schedule.attributes.get("next_event")
        deadline, problem = _parse_deadline(raw)
        if problem is not None:
            if self._precool_warned != problem:
                self._precool_warned = problem
                _LOGGER.warning("Pre-cool ignored: %s", problem)
            self._precool_status = f"no deadline: {problem}"
            return None
        self._precool_warned = None
        if deadline is None:
            self._precool_status = "no deadline: schedule has no next_event"
            return None
        climate = self.hass.states.get(self.entry.data[CONF_CLIMATE])
        outdoor = self._outdoor_now()
        setpoint = self.comfort_c
        if climate is None or outdoor is None or not rooms:
            self._precool_status = "no climate, outdoor or room reading"
            return None
        power_kw = running_power_kw(self._entries, now) or 0.0
        if power_kw <= 0:
            self._precool_status = "no running power learned yet"
            return None
        minutes = (deadline - now) / 60.0
        start = precool_start_in_min(
            active,
            statistics.median(rooms),
            setpoint,
            outdoor,
            power_kw,
            minutes,
            self._price_forecast(now),
            self._outdoor_series(),
        )
        if minutes <= 0:
            self._precool_status = "no deadline: next_event is in the past"
        elif start is None and minutes <= PRECOOL_LATE_MAX_MIN and statistics.median(rooms) > setpoint:
            # The room cannot reach the setpoint by the event. Cooling starts now, so it is as close as it can be.
            start = 0.0
            self._precool_status = f"late: starts now, {minutes:.0f} min to the event"
        elif start is None:
            self._precool_status = f"no plan reaches the setpoint in {minutes:.0f} min"
        else:
            self._precool_status = f"start in {start:.0f} min"
        return start

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

    def _solar_ahead(self) -> Callable[[float], float]:
        """Solar gain in kW at minutes from now: the clear-sky model times the share of it the room gets now."""
        now_kw, clear_kw = self._solar_gain(), self._modelled_solar_kw()
        share = 1.0 if now_kw is None or not clear_kw or clear_kw < SOLAR_SHARE_MIN_KW else min(now_kw / clear_kw, 1.5)
        return lambda minute: (self._modelled_solar_kw(minute) or 0.0) * share

    def _forecast(self, now: float, target_c: float | None = None) -> dict[str, Any]:
        """The forecast for what the room is doing: cooling to the cool threshold, heating to the heat target, or drying.

        The cooling forecast is the base. The heating forecast replaces it when the mode heats and the room is below
        the heat target. The drying forecast replaces it in dry mode. forecast_mode names the one published.
        """
        result = self._cool_forecast(now, target_c)
        result["forecast_mode"] = "cool"
        corrections = self._corrections
        result["fit_corrections"] = None if corrections is None or not corrections.terms else {
            "terms": list(corrections.terms), "solar_k_per_h_per_kw": round(corrections.s, 4),
            "c_per_k_outdoor": round(corrections.c_out, 4), "c_per_pct_humidity": round(corrections.c_rh, 4),
            "improvement_pct": corrections.improvement_pct,
        }
        result["fit_window_days"] = self._fit_window_days
        result["heat_model"] = self._heat_model_summary()
        result["dry_model"] = None if self._dry_fit is None else {"pct_per_h": round(self._dry_fit.rate, 2), "samples": self._dry_fit.samples}
        other = self._dry_forecast(now) if self._mode_now() == "dry" else self._heat_forecast(now)
        if other is not None:
            result.update(other)
        return result

    def _mode_now(self) -> str | None:
        """What the room is asked to do. With control on it is the thermostat's mode, and None while it is off. In observe-only it is the AC's own mode."""
        if self.entry.data.get(CONF_CONTROL):
            return None if self._user_off else self.user_mode
        climate = self.hass.states.get(self.entry.data[CONF_CLIMATE])
        return None if climate is None else climate.state

    def _heat_source_model(self) -> tuple[float, float | None, float | None, str] | None:
        """(heat rate in K/h, its standard error, electric power in kW, source) of the device that heats, or None with no model.

        A heater switch with a learned rate comes first. The AC's heat mode is next.
        """
        if self.entry.data.get(CONF_HEATER_SWITCH) and self._heat_rate is not None:
            watts = self.entry.data.get(CONF_HEATER_POWER_W)
            return self._heat_rate * self._auto_heat_gain, None, float(watts) / 1000.0 if watts else None, "heater"
        if self._ac_heat_fit is not None and self._ac_heat_kw:
            stderr = None if self._ac_heat_fit.stderr is None else self._ac_heat_fit.stderr * self._ac_heat_kw
            return self._ac_heat_fit.rate * self._ac_heat_kw, stderr, self._ac_heat_kw, "ac"
        return None

    def _heat_model_summary(self) -> dict[str, Any] | None:
        model = self._heat_source_model()
        if model is None:
            return None
        rate, _, power_kw, source = model
        return {"source": source, "k_per_h": round(rate, 3), "power_kw": _round_or_none(power_kw, 3)}

    @staticmethod
    def _forecast_fields(found: Forecast, low: float | None, high: float | None, mode: str, target: float) -> dict[str, Any]:
        return {
            "forecast_mode": mode,
            "forecast_target": round(target, 2),
            "status": "reachable" if found.reachable else "unreachable",
            "minutes": _round_or_none(found.minutes, 1),
            "minutes_low": _round_or_none(low, 1),
            "minutes_high": _round_or_none(high, 1),
            "energy_kwh": _round_or_none(found.energy_kwh, 3),
            "cost": _round_or_none(found.cost, 3),
            "reason": found.reason,
        }

    @staticmethod
    def _forecast_learning(mode: str, target: float, reason: str) -> dict[str, Any]:
        return {"forecast_mode": mode, "forecast_target": round(target, 2), "status": "learning", "minutes": None, "minutes_low": None, "minutes_high": None, "energy_kwh": None, "cost": None, "reason": reason}

    def _heat_forecast(self, now: float) -> dict[str, Any] | None:
        """The heating forecast, or None when the room is not being heated: the mode, the entry or the temperature rules it out."""
        mode = self._mode_now()
        control = bool(self.entry.data.get(CONF_CONTROL))
        if mode != "heat" and not (control and mode == "auto"):
            return None
        if control and not self.has_heat:
            return None
        rooms, outdoor = self._rooms(), self._outdoor_now()
        if not rooms or outdoor is None:
            return None
        if control:
            target = self._heat_target_c
        else:
            climate = self.hass.states.get(self.entry.data[CONF_CLIMATE])
            setpoint = _float_or_none(climate.attributes.get("temperature")) if climate else None
            if setpoint is None:
                return None
            target = setpoint
        room = statistics.median(rooms)
        if room >= target:
            return None if mode == "auto" else {**self._forecast_fields(Forecast(True, 0.0, 0.0, 0.0, math.nan, "already at or above the heat target"), 0.0, 0.0, "heat", target), "status": "at setpoint"}
        base = self._tpi or self._fit
        model = self._heat_source_model()
        if base is None or model is None:
            return self._forecast_learning("heat", target, f"need {MIN_MODE_SAMPLES} heating intervals and a room fit")
        rate, stderr, power_kw, _ = model
        prices, series = self._price_forecast(now), self._outdoor_series()
        found = heat_forecast(base.k, base.g, rate, power_kw, room, target, outdoor, prices, outdoor_series=series)
        low = high = None
        if found.reachable and stderr is not None:
            low = heat_forecast(base.k, base.g, rate + stderr, power_kw, room, target, outdoor, prices, outdoor_series=series).minutes
            high = heat_forecast(base.k, base.g, max(rate - stderr, 1e-6), power_kw, room, target, outdoor, prices, outdoor_series=series).minutes
        return self._forecast_fields(found, low, high, "heat", target)

    def _dry_forecast(self, now: float) -> dict[str, Any] | None:
        """The drying forecast in dry mode: minutes, energy and cost until the room humidity is at the end of the dry band."""
        humidity = _float(self._optional(CONF_ROOM_HUMIDITY))
        target = DRY_TARGET_PCT
        if humidity is None:
            return self._forecast_learning("dry", target, "no room humidity sensor")
        if self._dry_fit is None:
            return self._forecast_learning("dry", target, f"need {MIN_MODE_SAMPLES} dry intervals with a room humidity reading")
        found = dry_forecast(self._dry_fit.rate, humidity, target, self._dry_kw, self._price_forecast(now))
        low = high = None
        if found.reachable and self._dry_fit.stderr is not None:
            low = dry_forecast(self._dry_fit.rate + self._dry_fit.stderr, humidity, target, self._dry_kw, []).minutes
            high = dry_forecast(max(self._dry_fit.rate - self._dry_fit.stderr, 1e-6), humidity, target, self._dry_kw, []).minutes
        fields = self._forecast_fields(found, low, high, "dry", target)
        return {**fields, "status": "at setpoint"} if humidity <= target else fields

    def _cool_forecast(self, now: float, target_c: float | None = None) -> dict[str, Any]:
        # Observe-only values: the control path overwrites target_c and preset with the full decision.
        # `target_c` is the effective cool threshold from _plan_control. It replaces the comfort target when control is on.
        comfort_default = self.comfort_c if target_c is None else target_c
        result: dict[str, Any] = {
            "target_c": round(comfort_default, 2),
            "preset": "comfort",
            "samples": len(self._entries),
            "status": "learning",
            "minutes": None,
            "minutes_low": None,
            "minutes_high": None,
            "energy_kwh": None,
            "cost": None,
            "reason": self._fit_reason,
            "fit_residual_k_per_h": None,
            "k_stderr": None,
            "c_stderr": None,
            "g_stderr": None,
            "fit_outliers_dropped": None,
            "fit_confidence": None,
            "k": None,
            "c": None,
            "g": None,
            "forecast_error_min": self._forecast_error_min,
            "backtest_runs": len([r for r in self._backtest_runs if r.predicted_min is not None]),
            "accuracy_pairs": accuracy_pairs(self._backtest_runs),
            "range_coverage_pct": range_coverage(self._backtest_runs)[0],
            "range_runs": range_coverage(self._backtest_runs)[1],
            "room_fit": self._room_fit,
            "room_rate_coefficients": None if self._tpi is None else {"k_per_h": round(self._tpi.k, 4), "g_k_per_h": round(self._tpi.g, 4), "c_k_per_h_per_kw": round(self._tpi.c, 4), "samples_off": self._tpi.samples_off, "samples_on": self._tpi.samples_on},
            "samples_heat": self._mode_counts.get("heat", 0),
            "samples_dry": self._mode_counts.get("dry", 0),
            "auto_tpi_gains": {"cool": round(self._auto_tpi_gain, 4), "heat": round(self._auto_heat_gain, 4)},
        }
        active = self._active_fit()
        if active is None:
            return result

        result.update(k=round(active.k, 4), c=round(active.c, 4), g=round(active.g, 4))
        # The quality fields describe the fit that produced k, c and g, whether it is the single fit or the room-rate fit.
        result.update(
            fit_residual_k_per_h=_round_or_none(active.residual_k_per_h, 5),
            k_stderr=_round_or_none(active.k_stderr, 5),
            c_stderr=_round_or_none(active.c_stderr, 5),
            g_stderr=_round_or_none(active.g_stderr, 5),
            fit_outliers_dropped=active.outliers_dropped,
            fit_confidence=fit_confidence(active),
        )
        climate = self.hass.states.get(self.entry.data[CONF_CLIMATE])
        rooms = self._rooms()
        outdoor = self._outdoor_now()
        # Observe-only uses the climate entity's own setpoint, as the original request asked.
        # Control mode uses the comfort setting, because the integration writes that setpoint.
        if self.entry.data.get(CONF_CONTROL):
            target = comfort_default
        else:
            setpoint = climate.attributes.get("temperature") if climate else None
            target = float(setpoint) if setpoint is not None else self.comfort_c
        if not rooms or outdoor is None or climate is None:
            result.update(status="no input")
            return result

        room = statistics.median(rooms)
        if room <= target:
            result.update(status="at setpoint", minutes=0.0, energy_kwh=0.0, cost=0.0)
            if active.c_stderr is not None:
                result.update(minutes_low=0.0, minutes_high=0.0)
            return result

        power_kw = (_float(self.hass.states.get(self.entry.data[CONF_POWER])) or 0.0) / 1000.0
        if power_kw < 0.2:
            power_kw = running_power_kw(self._entries, now) or 0.0
        if power_kw <= 0:
            result.update(status="no running power yet")
            return result

        single = active
        active, power_kw = self._forecast_model(active, power_kw, now)
        series = self._outdoor_series()
        fit_at = None
        if active is single and self._corrections is not None and self._corrections.terms:
            # The corrections were fitted on the single-device fit, so they apply to it only.
            corrections, solar, room_rh = self._corrections, self._solar_ahead(), _float(self._optional(CONF_ROOM_HUMIDITY))
            fit_at = lambda minute, outdoor_at: apply(single, corrections, outdoor_at, solar(minute), room_rh)  # noqa: E731
        f = forecast(active, room, target, outdoor, power_kw, self._price_forecast(now), outdoor_series=series, fit_at=fit_at)
        low, high = forecast_range(active, room, target, outdoor, power_kw, self._price_forecast(now), outdoor_series=series, fit_at=fit_at) if f.reachable else (None, None)
        result.update(
            status="reachable" if f.reachable else "unreachable",
            minutes=None if f.minutes is None else round(f.minutes, 1),
            minutes_low=None if low is None else round(low, 1),
            minutes_high=None if high is None else round(high, 1),
            energy_kwh=None if f.energy_kwh is None else round(f.energy_kwh, 3),
            cost=None if f.cost is None else round(f.cost, 3),
            reason=f.reason,
        )
        return result
