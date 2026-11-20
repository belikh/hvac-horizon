"""Samples the room every SAMPLE_INTERVAL_S, refits the model and publishes the forecast."""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from custom_components.hvac_horizon.forecast import forecast
from custom_components.hvac_horizon.model import NotEnoughData, fit
from custom_components.hvac_horizon.collector import Entry, price_slots, running_power_kw, samples_from_entries

from .const import (
    CONF_CLIMATE,
    CONF_OUTDOOR,
    CONF_POWER,
    CONF_PRICE,
    CONF_ROOM_SENSORS,
    DOMAIN,
    FIT_EVERY_N_SAMPLES,
    MAX_ENTRIES,
    SAMPLE_INTERVAL_S,
)

_LOGGER = logging.getLogger(__name__)
STORE_VERSION = 1


def _float(state: Any) -> float | None:
    if state is None or state.state in ("unknown", "unavailable", ""):
        return None
    try:
        return float(state.state)
    except ValueError:
        return None


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
        self._fit = None
        self._fit_reason = "not enough data"
        self._samples_since_fit = 0

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self._entries = [Entry(**e) for e in data.get("entries", [])]
        self._refit()

    async def async_shutdown_store(self) -> None:
        await self._store.async_save({"entries": [e.__dict__ for e in self._entries]})

    def _refit(self) -> None:
        samples = samples_from_entries(self._entries)
        try:
            self._fit = fit(samples)
        except NotEnoughData as err:
            self._fit = None
            self._fit_reason = str(err)

    def _read_inputs(self) -> Entry | None:
        opts = self.entry.data
        room_states = [self.hass.states.get(e) for e in opts[CONF_ROOM_SENSORS]]
        room_values = [v for v in (_float(s) for s in room_states) if v is not None]
        outdoor = _float(self.hass.states.get(opts[CONF_OUTDOOR]))
        power_w = _float(self.hass.states.get(opts[CONF_POWER]))
        if not room_values or outdoor is None or power_w is None:
            return None
        return Entry(
            t=dt_util.utcnow().timestamp(),
            room_c=statistics.median(room_values),
            outdoor_c=outdoor,
            power_w=power_w,
        )

    def _price_forecast(self, now: float) -> list:
        price_id = self.entry.data.get(CONF_PRICE)
        if not price_id:
            return []
        state = self.hass.states.get(price_id)
        if state is None:
            return []
        pairs = []
        for item in state.attributes.get("forecast", []) or []:
            try:
                start = datetime.fromisoformat(item["time"]).timestamp()
                pairs.append((start, float(item["value"])))
            except (KeyError, TypeError, ValueError):
                continue
        return price_slots(pairs, now)

    async def _async_update_data(self) -> dict[str, Any]:
        now = dt_util.utcnow().timestamp()
        entry = self._read_inputs()
        if entry is not None:
            self._entries.append(entry)
            self._entries = self._entries[-MAX_ENTRIES:]
            self._samples_since_fit += 1
            if self._samples_since_fit >= FIT_EVERY_N_SAMPLES or self._fit is None:
                self._refit()
                self._samples_since_fit = 0
            self._store.async_delay_save(lambda: {"entries": [e.__dict__ for e in self._entries]}, 60)

        return self._forecast(now)

    def _forecast(self, now: float) -> dict[str, Any]:
        result: dict[str, Any] = {
            "samples": len(self._entries),
            "status": "learning",
            "minutes": None,
            "energy_kwh": None,
            "cost": None,
            "k": None,
            "c": None,
        }
        if self._fit is None:
            result["reason"] = self._fit_reason
            return result

        result["k"] = round(self._fit.k, 4)
        result["c"] = round(self._fit.c, 4)

        climate = self.hass.states.get(self.entry.data[CONF_CLIMATE])
        room_values = [v for v in (_float(self.hass.states.get(e)) for e in self.entry.data[CONF_ROOM_SENSORS]) if v is not None]
        outdoor = _float(self.hass.states.get(self.entry.data[CONF_OUTDOOR]))
        target = climate.attributes.get("temperature") if climate else None
        if not room_values or outdoor is None or target is None:
            result["status"] = "no input"
            return result
        room = statistics.median(room_values)
        if room <= float(target):
            result["status"] = "at setpoint"
            result["minutes"] = 0.0
            return result

        power_kw = (_float(self.hass.states.get(self.entry.data[CONF_POWER])) or 0.0) / 1000.0
        if power_kw < 0.2:
            power_kw = running_power_kw(self._entries, now) or 0.0
        if power_kw <= 0:
            result["status"] = "no running power yet"
            return result

        f = forecast(self._fit, room, float(target), outdoor, power_kw, self._price_forecast(now))
        result.update(
            status="reachable" if f.reachable else "unreachable",
            minutes=None if f.minutes is None else round(f.minutes, 1),
            energy_kwh=None if f.energy_kwh is None else round(f.energy_kwh, 3),
            cost=None if f.cost is None else round(f.cost, 3),
            reason=f.reason,
        )
        return result
