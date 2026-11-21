"""Forecasting and planning inputs: weather refresh, heatwave and solar estimates, price forecast, the forecast itself, pre-cool and pre-heat timing."""

from __future__ import annotations

import math
import statistics
from typing import Any
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CLIMATE,
    CONF_CONTROL,
    CONF_IRRADIANCE,
    CONF_OUTDOOR,
    CONF_POWER,
    CONF_PRICE,
    CONF_PRICE_CHEAP,
    CONF_PRICE_EXPENSIVE,
    CONF_SCHEDULE,
    CONF_WEATHER,
    CONF_WINDOW_AREA_M2,
    CONF_WINDOW_AZIMUTH,
    DEFAULT_PRICE_CHEAP,
    DEFAULT_PRICE_EXPENSIVE,
    DEFAULT_WINDOW_AZIMUTH,
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
from custom_components.hvac_horizon.backfill import accuracy_pairs
from custom_components.hvac_horizon.solar_model import clear_sky_irradiance, solar_elevation_azimuth, window_irradiance
from custom_components.hvac_horizon.forecast import forecast, forecast_range
from custom_components.hvac_horizon.control import precool_start_in_min
from custom_components.hvac_horizon.coordinator_common import _LOGGER, _parse_deadline, _round_or_none, _float


class ForecastMixin:
    # Instance attributes set in HorizonCoordinator.__init__ and used here.
    _auto_heat_gain: float
    _auto_tpi_gain: float
    _backtest_runs: list
    _entries: list[Entry]
    _fit: Fit | None
    _fit_reason: str
    _forecast_error_min: float | None
    _forecast_highs: list[float]
    _heat_rate: float | None
    _mode_counts: dict[str, int]
    _outdoor_history: list[float]
    _precool_status: str | None
    _precool_warned: str | None
    _price_warned: bool
    _room_fit: dict[str, Any] | None
    _room_model: RoomFit | None
    _tpi: TpiFit | None
    entry: ConfigEntry
    hass: HomeAssistant

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
        outdoor = _float(self.hass.states.get(self.entry.data[CONF_OUTDOOR]))
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
        )
        if minutes <= 0:
            self._precool_status = "no deadline: next_event is in the past"
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

    def _forecast(self, now: float, target_c: float | None = None) -> dict[str, Any]:
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
            "room_fit": self._room_fit,
            "room_rate_coefficients": None if self._tpi is None else {"k_per_h": round(self._tpi.k, 4), "g_k_per_h": round(self._tpi.g, 4), "c_k_per_h_per_kw": round(self._tpi.c, 4), "samples_off": self._tpi.samples_off, "samples_on": self._tpi.samples_on},
            "samples_heat": self._mode_counts.get("heat", 0),
            "samples_dry": self._mode_counts.get("dry", 0),
            "auto_tpi_gains": {"cool": round(self._auto_tpi_gain, 4), "heat": round(self._auto_heat_gain, 4)},
        }
        if self._fit is not None:
            result.update(
                fit_residual_k_per_h=_round_or_none(self._fit.residual_k_per_h, 5),
                k_stderr=_round_or_none(self._fit.k_stderr, 5),
                c_stderr=_round_or_none(self._fit.c_stderr, 5),
                g_stderr=_round_or_none(self._fit.g_stderr, 5),
                fit_outliers_dropped=self._fit.outliers_dropped,
                fit_confidence=fit_confidence(self._fit),
            )
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

        active, power_kw = self._forecast_model(active, power_kw, now)
        f = forecast(active, room, target, outdoor, power_kw, self._price_forecast(now))
        low, high = forecast_range(active, room, target, outdoor, power_kw, self._price_forecast(now)) if f.reachable else (None, None)
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
