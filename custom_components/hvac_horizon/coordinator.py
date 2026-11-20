"""Samples the room every SAMPLE_INTERVAL_S, refits the model and publishes the forecast."""

from __future__ import annotations

import logging
import statistics
from dataclasses import asdict, fields, replace
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from custom_components.hvac_horizon.backfill import backtest, median_abs_error
from custom_components.hvac_horizon.collector import Entry, has_enough_cycles, price_slots, running_power_kw, samples_from_entries
from custom_components.hvac_horizon.forecast import forecast
from custom_components.hvac_horizon.decide import Mode, Preset, RoomState, Settings, decide
from custom_components.hvac_horizon.features import (
    ScheduleBlock,
    classify_day,
    daily_briefing,
    heating_failure,
    hot_streak,
    learning_suggestion,
    band_action,
    comfort_band,
    compliance_score,
    drying_needed,
    fan_speed_from_gap,
    stale_price_fallback,
    ventilation_rate_from_co2,
    mould_risk,
    surface_humidity,
    presence_mode,
    sun_heat_gain,
    vacation_target,
    night_plan,
    safety_output,
    scheduled_preset,
    sensor_stalled,
)
from custom_components.hvac_horizon.features import air_quality_ventilation, humidity_action, minimum_time_ok, price_shifted_target
from custom_components.hvac_horizon.model import Fit, NotEnoughData, fit
from custom_components.hvac_horizon.control import precool_start_in_min
from custom_components.hvac_horizon.autotune import RelayTuner

from .const import (
    CONF_AWAY_C,
    CONF_CLIMATE,
    CONF_AWAY_DELAY_MIN,
    CONF_CO2,
    CONF_IRRADIANCE,
    CONF_WINDOW_AREA_M2,
    DEFAULT_AWAY_DELAY_MIN,
    CONF_VACATION,
    CONF_VACATION_C,
    DEFAULT_VACATION_C,
    CONF_DEHUMIDIFIER,
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
    CONF_MIN_ON_MIN,
    CONF_CONTROL,
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
        self._autotune_result = None
        self._last_good_price_at: float | None = None
        self._total_min: float = 0.0
        self._heater_start_temp: float | None = None
        self._backtest_runs: list = []
        self._forecast_error_min: float | None = None

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

    def _heatwave(self) -> bool:
        """True when the outdoor readings have reached 30 C on at least three cycles in a row.

        Uses the recent outdoor readings as the forecast until a weather entity is wired in.
        """
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

    def _night_plan(self) -> str | None:
        """Overnight plan from the room and outdoor readings. Uses the current outdoor reading as the low until a forecast is wired in."""
        rooms = self._rooms()
        outdoor = _float(self.hass.states.get(self.entry.data[CONF_OUTDOOR]))
        if not rooms or outdoor is None:
            return None
        comfort = float(self.entry.data.get(CONF_COMFORT_C, DEFAULT_COMFORT_C))
        return night_plan(outdoor, statistics.median(rooms), comfort)

    def _day_type(self) -> str | None:
        """Day class from the outdoor sensor's reading. Uses the current reading until a forecast is wired in."""
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
        result["day_type"] = self._day_type()
        result["night_plan"] = self._night_plan()
        result["learning_suggestion"] = self._learning_suggestion()
        vacation_on = self._optional(CONF_VACATION) is not None and self._optional(CONF_VACATION).state == "on"
        result["vacation"] = vacation_on
        result["occupancy"] = self._occupancy_mode(now)
        result["solar_gain_kw"] = self._solar_gain()
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
        result["safety_output_pct"] = safety_output(bool(result["sensors_stalled"]), 100.0, fallback_pct=0.0)
        if self.entry.data.get(CONF_CONTROL):
            await self._async_control(now, result)
        return result

    async def _async_control(self, now: float, result: dict[str, Any]) -> None:
        """Send one climate action per cycle. Runs only when the control option is on."""
        climate_id = self.entry.data[CONF_CLIMATE]
        rooms = self._rooms()
        if not rooms:
            return
        window = self._optional(CONF_WINDOW)
        if window is not None and window.state == "on":
            self._window_open_since = self._window_open_since or now
        else:
            self._window_open_since = None
        window_open_min = 0.0 if self._window_open_since is None else (now - self._window_open_since) / 60.0
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
        if climate is None or climate.state in ("unavailable", "unknown"):
            return
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
                return
            if climate.state != "off":
                await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate_id, "hvac_mode": "off"}, blocking=True)
            return
        if climate.state != "cool":
            await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate_id, "hvac_mode": "cool"}, blocking=True)
        current = climate.attributes.get("temperature")
        if current is None or float(current) != float(action.setpoint_c):
            await self.hass.services.async_call(
                "climate", "set_temperature", {"entity_id": climate_id, "temperature": action.setpoint_c}, blocking=True
            )

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
