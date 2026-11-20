"""Samples the room every SAMPLE_INTERVAL_S, refits the model and publishes the forecast."""

from __future__ import annotations

import logging
import statistics
from dataclasses import asdict, fields, replace
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.components import persistent_notification
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from custom_components.hvac_horizon.backfill import backtest, median_abs_error
from custom_components.hvac_horizon.collector import (
    Entry,
    entries_from_history,
    has_enough_cycles,
    price_slots,
    running_power_kw,
    samples_from_entries,
)
from custom_components.hvac_horizon.forecast import forecast
from custom_components.hvac_horizon.decide import Mode, Preset, RoomState, Settings, decide
from custom_components.hvac_horizon.features import (
    ScheduleBlock,
    classify_day,
    forecast_highs,
    natural_ventilation_window,
    daily_briefing,
    check_pin,
    heating_failure,
    pwm_on,
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
    surface_humidity,
    presence_mode,
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
from custom_components.hvac_horizon.features import air_quality_ventilation, cover_action, humidity_action, keep_alive_due, minimum_time_ok, price_shifted_target, valve_opening_pct
from custom_components.hvac_horizon.model import Fit, NotEnoughData, fit
from custom_components.hvac_horizon.control import precool_start_in_min
from custom_components.hvac_horizon.autotune import RelayTuner

from .const import (
    PWM_WINDOW_MIN,
    CONF_AWAY_C,
    CONF_CLIMATE,
    CONF_AWAY_DELAY_MIN,
    CONF_CO2,
    CONF_LOCK_PIN,
    CONF_HOUSE_SEALED,
    CONF_WHOLE_HOUSE_FAN,
    CONF_IRRADIANCE,
    CONF_WINDOW_AREA_M2,
    DEFAULT_AWAY_DELAY_MIN,
    CONF_VACATION,
    CONF_VACATION_C,
    DEFAULT_VACATION_C,
    CONF_DEHUMIDIFIER,
    CONF_HUMIDIFIER,
    CONF_FAN,
    CONF_HEATER_SWITCH,
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
    COVER_SOLAR_THRESHOLD_KW,
    CONF_VALVE,
    CONF_ENERGY,
    CONF_SCHEDULE,
    CONF_HUMIDITY,
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


def _entry_from_dict(raw: dict[str, Any]) -> Entry:
    """Load a stored record. Unknown keys are dropped, missing optional keys take their defaults."""
    clean = {k: v for k, v in raw.items() if k in _ENTRY_FIELDS}
    if "rooms_c" in clean:
        clean["rooms_c"] = tuple(clean["rooms_c"])
    return Entry(**clean)


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
        self._heater_on_since: float | None = None
        self._baseline_k: float | None = None
        self._last_home_at: float | None = None
        self._last_cycle_t: float | None = None
        self._in_band_min: float = 0.0
        self._outdoor_history: list[float] = []
        self._last_good_price: float | None = None
        self._tuner: RelayTuner | None = None
        self._stall_reported = False
        self._locked = False
        self._forecast_highs: list[float] = []
        self._weather_checked_min: float | None = None
        self._pwm_output_pct: float = 0.0
        self._last_sent_min: float | None = None
        self._heater_commanded_on: bool | None = None
        self._dismissed_for_k: float | None = None
        self._timed_preset: str | None = None
        self._timed_started_min: float | None = None
        self._timed_duration_min: float = 0.0
        self._last_written_mode: str | None = None
        self._window_paused = False
        self._lock_notified = False
        self._inhibit_until_min: float | None = None
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
        self._refit()

    def what_if(self, room_c: float, target_c: float, outdoor_c: float, power_kw: float) -> dict[str, Any]:
        """Forecast minutes, energy and cost for given conditions, using the current fit and price slots."""
        if self._fit is None:
            return {"status": "learning", "reason": self._fit_reason}
        f = forecast(self._fit, room_c, target_c, outdoor_c, power_kw, self._price_forecast(dt_util.utcnow().timestamp()))
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
        """Begin a relay test at `setpoint_c`. The switch is driven only by the cycle while this is active."""
        self._tuner = RelayTuner(setpoint_c=setpoint_c)
        self._autotune_result = None

    def _autotune_step(self, room_c: float, now: float) -> None:
        """One relay step. Drives the heater switch only when a tuner is active and a heater switch is configured."""
        if self._tuner is None:
            return
        want_on = self._tuner.step(room_c, now / 3600.0)
        if self._tuner.done:
            self._autotune_result = self._tuner.result()
            self._tuner = None
        switch = self.entry.data.get(CONF_HEATER_SWITCH)
        if switch and self._tuner is not None:
            self._heater_commanded_on = bool(want_on)
            self.hass.async_create_task(
                self.hass.services.async_call("switch", "turn_on" if want_on else "turn_off", {"entity_id": switch}, blocking=True)
            )

    def _save_payload(self) -> dict[str, Any]:
        return {"entries": [asdict(e) for e in self._entries]}

    async def async_shutdown_store(self) -> None:
        await self._store.async_save(self._save_payload())

    def _refit(self) -> None:
        samples = samples_from_entries(self._entries)
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

    def _heating_check(self, now: float) -> bool:
        """True when the heater has run for its window without the room rising. Uses the heater switch when set."""
        switch_id = self.entry.data.get(CONF_HEATER_SWITCH)
        if not switch_id:
            return False
        state = self.hass.states.get(switch_id)
        rooms = self._rooms()
        if state is None or not rooms:
            return False
        if state.state != "on":
            self._heater_on_since = None
            self._heater_start_temp = None
            return False
        if self._heater_on_since is None:
            self._heater_on_since = now
            self._heater_start_temp = statistics.median(rooms)
        on_min = (now - self._heater_on_since) / 60.0
        rise = statistics.median(rooms) - (self._heater_start_temp or 0.0)
        return heating_failure(on_min, rise)

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
        room_rh = _float(self._optional(CONF_HUMIDITY))
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

    def _occupancy_mode(self, now: float) -> str:
        """Home or away from the optional presence sources. Tracks the last time any source was home."""
        sources = [self._optional(CONF_PRESENCE)] if self.entry.data.get(CONF_PRESENCE) else []
        home = [s.state in ("on", "home") for s in sources if s is not None]
        if any(home):
            self._last_home_at = now
        since = 0.0 if self._last_home_at is None else (now - self._last_home_at) / 60.0
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
        self._pwm_output_pct = max(0.0, min(100.0, pct))

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

    def _external_change(self) -> bool:
        """True when the climate setpoint differs from the last one this integration wrote (someone changed it by hand)."""
        climate = self.hass.states.get(self.entry.data[CONF_CLIMATE])
        if climate is None or climate.attributes.get("temperature") is None:
            return False
        current = float(climate.attributes["temperature"])
        if self._last_written_setpoint is None:
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
        """Room and outdoor sensors that have not updated recently. Published so the user can see them."""
        names = list(self.entry.data[CONF_ROOM_SENSORS]) + [self.entry.data[CONF_OUTDOOR]]
        stalled = []
        for name in names:
            state = self.hass.states.get(name)
            last = state.last_updated.timestamp() if state is not None else None
            if sensor_stalled(last, now):
                stalled.append(name)
        return stalled

    def _rooms(self) -> list[float]:
        """Room readings for the median: the room sensors, plus the AC's own temperature when it reports one.

        The AC reports whole degrees, so it has a coarser reading than the room sensors.
        """
        states = [self.hass.states.get(e) for e in self.entry.data[CONF_ROOM_SENSORS]]
        values = [v for v in (_float(s) for s in states) if v is not None]
        climate = self.hass.states.get(self.entry.data[CONF_CLIMATE])
        own = climate.attributes.get("current_temperature") if climate else None
        if own is not None:
            try:
                values.append(float(own))
            except (TypeError, ValueError):
                pass
        return values

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
        return Entry(
            t=now,
            room_c=statistics.median(rooms),
            outdoor_c=outdoor,
            power_w=power_w,
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
        rooms_tune = self._rooms()
        if rooms_tune:
            self._autotune_step(statistics.median(rooms_tune), now)
        result["autotune"] = None if self._autotune_result is None else {"kp": round(self._autotune_result.kp, 3), "ki": round(self._autotune_result.ki, 3), "kd": round(self._autotune_result.kd, 3)}
        result["sensors_stalled"] = self._stalled_sensors(now)
        self._report_stalls(result["sensors_stalled"])
        result["external_change"] = self._external_change()
        result["day_type"] = self._day_type()
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
        result["window_advice"] = self._window_advice()
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
        result["drying_needed"] = drying_needed(_float(self._optional(CONF_HUMIDITY)), cool_gap_c=room_gap)
        price_now = _float(self._optional(CONF_PRICE))
        if price_now is not None:
            self._last_good_price = price_now
            self._last_good_price_at = now
        age_min = 0.0 if self._last_good_price_at is None else (now - self._last_good_price_at) / 60.0
        result["price_used_per_kwh"] = stale_price_fallback(price_now, self._last_good_price, age_min)
        result["ventilation_rate"] = round(ventilation_rate_from_co2(_float(self._optional(CONF_CO2))), 3)
        result["heating_failure"] = self._heating_check(now)
        # Publishes the PWM pattern only. This path does not drive the heater switch;
        # only the autotune path does.
        heater_on = pwm_on(self._pwm_output_pct, PWM_WINDOW_MIN, now / 60.0)
        result["heater_pwm_on"] = heater_on
        valve_rooms = self._rooms()
        valve_opening = None
        if valve_rooms:
            comfort_target = float(self.entry.data.get(CONF_COMFORT_C, DEFAULT_COMFORT_C))
            valve_opening = valve_opening_pct(statistics.median(valve_rooms), comfort_target)
        result["valve_opening_pct"] = valve_opening
        cover_id = self.entry.data.get(CONF_COVER)
        solar = self._solar_gain()
        cover_state = self.hass.states.get(cover_id) if cover_id else None
        if solar is not None and cover_state is not None and cover_state.state not in ("unavailable", "unknown"):
            action = cover_action(solar, COVER_SOLAR_THRESHOLD_KW, cover_state.state == "open")
            result["cover_action"] = action
            await self._drive_cover(action)
        heater_switch = self.entry.data.get(CONF_HEATER_SWITCH)
        heater_state = self.hass.states.get(heater_switch) if heater_switch else None
        result["heater_feedback"] = switch_feedback(
            self._heater_commanded_on if heater_switch else None,
            heater_state.state if heater_state is not None else None,
        )
        result["safety_output_pct"] = safety_output(bool(result["sensors_stalled"]), 100.0, fallback_pct=0.0)
        self._notify_transition("lock", "_lock_notified", self._locked, "HVAC Horizon control is locked.", "HVAC Horizon control is unlocked.")
        if self.entry.data.get(CONF_CONTROL):
            await self._async_control(now, result)
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
        valve = self.entry.data.get(CONF_VALVE)
        if not self.entry.data.get(CONF_CONTROL) or not valve:
            return
        if not lock_allows_control(self._locked):
            return
        if inhibited(dt_util.utcnow().timestamp() / 60.0, self._inhibit_until_min):
            return
        state = self.hass.states.get(valve)
        if state is None or state.state in ("unavailable", "unknown"):
            return
        current = _float(state)
        if current is not None and abs(current - opening) <= 1.0:
            return
        await self.hass.services.async_call("number", "set_value", {"entity_id": valve, "value": opening}, blocking=True)

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

    async def _async_control(self, now: float, result: dict[str, Any]) -> None:
        """Send one climate action per cycle. Runs only when the control option is on."""
        climate_id = self.entry.data[CONF_CLIMATE]
        if not lock_allows_control(self._locked):
            result["action"] = "locked"
            return
        now_min = now / 60.0
        if self._external_change():
            self._inhibit_until_min = now_min + INHIBIT_MIN
        if inhibited(now_min, self._inhibit_until_min):
            result["action"] = "inhibited after a manual change"
            return
        # Keep-alive: resend the last setpoint when the device has not been written to for KEEP_ALIVE_MIN.
        keep_alive_climate = self.hass.states.get(climate_id)
        if (
            keep_alive_due(self._last_sent_min, now_min, KEEP_ALIVE_MIN)
            and self._last_written_setpoint is not None
            and keep_alive_climate is not None
            and keep_alive_climate.state not in ("unavailable", "unknown")
        ):
            await self.hass.services.async_call(
                "climate", "set_temperature", {"entity_id": climate_id, "temperature": self._last_written_setpoint}, blocking=True
            )
            self._last_sent_min = now_min
        rooms = self._rooms()
        if not rooms:
            return
        window = self._optional(CONF_WINDOW)
        if window is not None and window.state == "on":
            self._window_open_since = self._window_open_since or now
        else:
            self._window_open_since = None
        window_open_min = 0.0 if self._window_open_since is None else (now - self._window_open_since) / 60.0
        window_delay_min = float(self.entry.data.get(CONF_WINDOW_DELAY_MIN, DEFAULT_WINDOW_DELAY_MIN))
        self._notify_transition(
            "window", "_window_paused", window_open_min >= window_delay_min,
            "HVAC Horizon paused control: a window is open.", "HVAC Horizon resumed control: the window is closed.",
        )
        presence = self._optional(CONF_PRESENCE)
        # A binary presence sensor reports "on" for home. A person or tracker reports "home".
        someone_home = presence is None or presence.state in ("on", "home")
        precool = self._precool_start(now, rooms)
        outdoor = _float(self.hass.states.get(self.entry.data[CONF_OUTDOOR]))
        state = RoomState(
            room_c=statistics.median(rooms),
            window_open_min=window_open_min,
            someone_home=someone_home,
            precool_start_in_min=precool,
            outdoor_c=outdoor,
        )
        comfort = float(self.entry.data.get(CONF_COMFORT_C, DEFAULT_COMFORT_C))
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
        result["target_c"] = round(target_c, 2)
        blocks = [
            ScheduleBlock(float(b["start_h"]), float(b["end_h"]), str(b["preset"]))
            for b in (self.entry.data.get(CONF_SCHEDULE_BLOCKS) or [])
        ]
        preset_name = scheduled_preset(dt_util.now().hour + dt_util.now().minute / 60.0, blocks)
        preset_name = self._effective_preset(now_min, preset_name)
        result["preset"] = preset_name
        settings = Settings(
            comfort_c=target_c,
            preset=Preset(preset_name) if preset_name in {p.value for p in Preset} else Preset.COMFORT,
            away_c=float(self.entry.data.get(CONF_AWAY_C, DEFAULT_AWAY_C)),
            window_delay_min=float(self.entry.data.get(CONF_WINDOW_DELAY_MIN, DEFAULT_WINDOW_DELAY_MIN)),
        )
        action = decide(state, settings)
        result["action"] = f"{action.mode.value}: {action.reason}"
        climate = self.hass.states.get(climate_id)
        result["daily_briefing"] = daily_briefing(
            result.get("day_type") or "unknown",
            float(self.entry.data.get(CONF_COMFORT_C, DEFAULT_COMFORT_C)),
            result.get("action"),
            None,
        )
        ventilation = air_quality_ventilation(_float(self._optional(CONF_CO2)), _float(self._optional(CONF_PM25)))
        humidity = humidity_action(_float(self._optional(CONF_HUMIDITY)))
        result["ventilation"] = ventilation.reason
        result["humidity"] = humidity.reason
        await self._drive_switch(self.entry.data.get(CONF_FAN), ventilation.run)
        await self._drive_switch(self.entry.data.get(CONF_DEHUMIDIFIER), humidity.action == "dehumidify")
        await self._drive_switch(self.entry.data.get(CONF_HUMIDIFIER), humidity.action == "humidify")
        outdoor_now = _float(self.hass.states.get(self.entry.data[CONF_OUTDOOR]))
        rooms_wh = self._rooms()
        fan_ok = whole_house_fan_allowed(
            bool(self.entry.data.get(CONF_HOUSE_SEALED, False)),
            climate.state == "cool",
            outdoor_now,
            statistics.median(rooms_wh) if rooms_wh else 0.0,
        )
        result["whole_house_fan"] = fan_ok
        await self._drive_switch(self.entry.data.get(CONF_WHOLE_HOUSE_FAN), fan_ok)
        if climate is None or climate.state in ("unavailable", "unknown"):
            return
        self._check_mode(climate.state)
        # Minimum on and off times: do not change state until the current state has lasted long enough.
        wanted_on = action.mode is Mode.COOL
        is_on = climate.state == "cool"
        if wanted_on != is_on:
            since_min = (now - self._state_changed_at) / 60.0 if self._state_changed_at is not None else 1e9
            if not minimum_time_ok(is_on, since_min, self.entry.data.get(CONF_MIN_ON_MIN, 5.0), self.entry.data.get(CONF_MIN_OFF_MIN, 3.0)):
                result["action"] = f"{action.mode.value}: waiting for minimum time"
                return
            self._state_changed_at = now
        # Write only what differs from the device's current state, so a manual change is not overwritten every cycle.
        if action.mode is Mode.OFF:
            # No cooling needed. If the room is too humid and the AC has a dry mode, dry the room with it.
            if humidity.action == "dehumidify" and not self.entry.data.get(CONF_DEHUMIDIFIER) and climate.state != "dry":
                await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate_id, "hvac_mode": "dry"}, blocking=True)
                self._last_sent_min = now / 60.0
                self._last_written_mode = "dry"
                return
            if climate.state != "off":
                await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate_id, "hvac_mode": "off"}, blocking=True)
                self._last_sent_min = now / 60.0
                self._last_written_mode = "off"
            return
        if climate.state != "cool":
            await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate_id, "hvac_mode": "cool"}, blocking=True)
            self._last_sent_min = now / 60.0
            self._last_written_mode = "cool"
        current = climate.attributes.get("temperature")
        if current is None or float(current) != float(action.setpoint_c):
            await self.hass.services.async_call(
                "climate", "set_temperature", {"entity_id": climate_id, "temperature": action.setpoint_c}, blocking=True
            )
            self._last_written_setpoint = float(action.setpoint_c)
            self._last_sent_min = now / 60.0

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
        result: dict[str, Any] = {
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
        }
        if self._fit is None:
            return result

        result.update(k=round(self._fit.k, 4), c=round(self._fit.c, 4), g=round(self._fit.g, 4))
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

        f = forecast(self._fit, room, target, outdoor, power_kw, self._price_forecast(now))
        result.update(
            status="reachable" if f.reachable else "unreachable",
            minutes=None if f.minutes is None else round(f.minutes, 1),
            energy_kwh=None if f.energy_kwh is None else round(f.energy_kwh, 3),
            cost=None if f.cost is None else round(f.cost, 3),
            reason=f.reason,
        )
        return result
