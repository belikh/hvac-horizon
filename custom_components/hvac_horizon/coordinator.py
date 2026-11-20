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
from custom_components.hvac_horizon.decide import Mode, RoomState, Settings, decide
from custom_components.hvac_horizon.model import Fit, NotEnoughData, fit
from custom_components.hvac_horizon.control import plan_precool

from .const import (
    CONF_AWAY_C,
    CONF_CLIMATE,
    CONF_COMFORT_C,
    CONF_CONTROL,
    CONF_ENERGY,
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
        someone_home = presence is None or presence.state == "home"
        precool = None
        if result.get("minutes") is not None and result.get("status") == "reachable":
            precool = 0.0
        state = RoomState(
            room_c=statistics.median(rooms),
            window_open_min=window_open_min,
            someone_home=someone_home,
            precool_start_in_min=precool,
        )
        settings = Settings(
            comfort_c=float(self.entry.data.get(CONF_COMFORT_C, DEFAULT_COMFORT_C)),
            away_c=float(self.entry.data.get(CONF_AWAY_C, DEFAULT_AWAY_C)),
            window_delay_min=float(self.entry.data.get(CONF_WINDOW_DELAY_MIN, DEFAULT_WINDOW_DELAY_MIN)),
        )
        action = decide(state, settings)
        result["action"] = f"{action.mode.value}: {action.reason}"
        if action.mode is Mode.OFF:
            await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate_id, "hvac_mode": "off"}, blocking=True)
        else:
            await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": climate_id, "hvac_mode": "cool"}, blocking=True)
            await self.hass.services.async_call(
                "climate", "set_temperature", {"entity_id": climate_id, "temperature": action.setpoint_c}, blocking=True
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
