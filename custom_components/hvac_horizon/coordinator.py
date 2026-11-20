"""Samples the room every SAMPLE_INTERVAL_S, refits the model and publishes the forecast."""

from __future__ import annotations

import logging
import statistics
from dataclasses import asdict, fields
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from custom_components.hvac_horizon.collector import Entry, price_slots, running_power_kw, samples_from_entries
from custom_components.hvac_horizon.forecast import forecast
from custom_components.hvac_horizon.model import Fit, NotEnoughData, fit

from .const import (
    CONF_CLIMATE,
    CONF_ENERGY,
    CONF_HUMIDITY,
    CONF_OUTDOOR,
    CONF_POWER,
    CONF_PRICE,
    CONF_ROOM_SENSORS,
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

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self._entries = [_entry_from_dict(e) for e in data.get("entries", [])]
        self._refit()

    def _save_payload(self) -> dict[str, Any]:
        return {"entries": [asdict(e) for e in self._entries]}

    async def async_shutdown_store(self) -> None:
        await self._store.async_save(self._save_payload())

    def _refit(self) -> None:
        try:
            self._fit = fit(samples_from_entries(self._entries))
            self._fit_reason = "fitted"
        except NotEnoughData as err:
            self._fit = None
            self._fit_reason = str(err)

    def _rooms(self) -> list[float]:
        states = (self.hass.states.get(e) for e in self.entry.data[CONF_ROOM_SENSORS])
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
        if reading is not None:
            self._entries.append(reading)
            self._entries = self._entries[-MAX_ENTRIES:]
            self._samples_since_fit += 1
            if self._samples_since_fit >= FIT_EVERY_N_SAMPLES or self._fit is None:
                self._refit()
                self._samples_since_fit = 0
            self._store.async_delay_save(self._save_payload, 60)
        return self._forecast(now)

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
        }
        if self._fit is None:
            return result

        result.update(k=round(self._fit.k, 4), c=round(self._fit.c, 4), g=round(self._fit.g, 4))
        climate = self.hass.states.get(self.entry.data[CONF_CLIMATE])
        rooms = self._rooms()
        outdoor = _float(self.hass.states.get(self.entry.data[CONF_OUTDOOR]))
        target = climate.attributes.get("temperature") if climate else None
        if not rooms or outdoor is None or target is None:
            result.update(status="no input")
            return result

        room = statistics.median(rooms)
        if room <= float(target):
            result.update(status="at setpoint", minutes=0.0, energy_kwh=0.0, cost=0.0)
            return result

        power_kw = (_float(self.hass.states.get(self.entry.data[CONF_POWER])) or 0.0) / 1000.0
        if power_kw < 0.2:
            power_kw = running_power_kw(self._entries, now) or 0.0
        if power_kw <= 0:
            result.update(status="no running power yet")
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
