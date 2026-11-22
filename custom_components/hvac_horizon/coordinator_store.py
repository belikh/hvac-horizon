"""Persistence: reading and writing the store payload, the commanded-state record, recorder backfill, power-interval tracking and the learned-data reset."""

from __future__ import annotations

from typing import Any
from dataclasses import asdict
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from custom_components.hvac_horizon.room_model.outdoor import OutdoorBias
from .const import CONF_OUTDOOR, CONF_POWER, CONF_ROOM_SENSORS, DOMAIN, MAX_ENTRIES, SAMPLE_INTERVAL_S
from custom_components.hvac_horizon.room_model.collector import Entry, entries_from_history
from custom_components.hvac_horizon.room_model.model import Fit
from custom_components.hvac_horizon.room_model import kept
from custom_components.hvac_horizon.engine.features import HUMIDITY_TARGET_MAX_PCT, HUMIDITY_TARGET_MIN_PCT, house_mode_from_store
from custom_components.hvac_horizon.coordinator_common import USER_MODES, _LOGGER, _entries_from_store, _float, _stored_dict, _stored_number


class StoreMixin:
    # Instance attributes set in HorizonCoordinator.__init__ and used here.
    _actuator_opened_by_us: bool
    _auto_heat_gain: float
    _auto_tpi_gain: float
    _backtest_runs: list
    _coil_dry_sent: bool
    _device_rates: dict[str, dict[str, float]]
    _entries: list[Entry]
    _fit: Fit | None
    _fit_reason: str
    _forecast_error_min: float | None
    _heater_commanded_on: bool | None
    _heater_gains: dict[str, float] | None
    _heater_switch_driven: str | None
    _inhibit_until_min: float | None
    _kept: kept.Kept
    _kept_used: set[str]
    _last_power_w: float
    _last_t: float
    _last_written_mode: str | None
    _last_written_setpoint: float | None
    _loaded_at: float
    _locked: bool
    _report: dict[str, Any]
    _samples_since_fit: int
    _saved_commanded: dict[str, Any]
    _second_ac_commanded_on: bool
    _shed_at: dict[str, float]
    _shed_context: dict[str, str]
    _shed_loads: set[str]
    _store_loaded: bool
    _store: Store[dict[str, Any]]
    _switches_on: set[str]
    _t_at_sample: float
    _target_override_c: float | None
    _heat_override_c: float | None
    _humidity_target_pct: float | None
    _timed_duration_min: float
    _timed_preset: str | None
    _timed_started_min: float | None
    _user_mode: str
    _user_off: bool
    _valve_commanded_pct: float
    _valve_driven: str | None
    _ws_at_sample: float
    _ws_total: float
    entry: ConfigEntry
    hass: HomeAssistant

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
        room_ids = list(self.settings[CONF_ROOM_SENSORS])
        outdoor_id = self.settings[CONF_OUTDOOR]
        power_id = self.settings.get(CONF_POWER)
        if not power_id:
            return 0  # the history of the AC's power is what the readings are built from
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
        self._last_power_w = self._ac_power_w() or 0.0
        self._last_t = now
        self._ws_total = 0.0
        self._ws_at_sample = 0.0
        self._t_at_sample = now
        entity = self.settings.get(CONF_POWER)
        if not entity:
            return  # with no power sensor each reading takes the power estimate of its own tick

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
        # A damaged field takes its default, so one bad value does not keep the entry from loading.
        data = _stored_dict(await self._store.async_load())
        self._entries = _entries_from_store(data.get("entries"))
        stored = data.get("investigation_report")
        if isinstance(stored, dict):
            # The last on-demand report and its time survive a restart. Keys missing from the store stay None.
            self._report = {key: stored.get(key) for key in self._report}
        gains = {key: _stored_number(_stored_dict(data.get("heater_gains")).get(key)) for key in ("kp", "ki", "kd")}
        self._heater_gains = gains if all(v is not None for v in gains.values()) else None
        override = _stored_number(data.get("target_override_c"))
        self._target_override_c = override if override is not None and 0.0 <= override <= 50.0 else None
        low = _stored_number(data.get("heat_override_c"))
        self._heat_override_c = low if low is not None and 0.0 <= low <= 50.0 else None
        humidity = _stored_number(data.get("humidity_target_pct"))
        self._humidity_target_pct = humidity if humidity is not None and HUMIDITY_TARGET_MIN_PCT <= humidity <= HUMIDITY_TARGET_MAX_PCT else None
        self._user_off = bool(data.get("user_off", False))
        self._user_mode = data.get("user_mode") if data.get("user_mode") in USER_MODES else "auto"
        auto_gains = _stored_dict(data.get("auto_tpi_gains"))
        self._auto_tpi_gain = _stored_number(auto_gains.get("cool")) or 1.0
        self._auto_heat_gain = _stored_number(auto_gains.get("heat")) or 1.0
        self._locked = bool(data.get("locked", False))
        house_mode = data.get("house_mode")
        if house_mode in ("home", "away"):
            self.hass.data.setdefault(DOMAIN, {})["house_mode"] = house_mode
        now_min = dt_util.utcnow().timestamp() / 60.0
        inhibit = _stored_number(data.get("inhibit_until_min"))
        self._inhibit_until_min = inhibit if inhibit is not None and inhibit > now_min else None
        timed = data.get("timed_preset")
        self._timed_preset = None
        self._timed_started_min = None
        self._timed_duration_min = 0.0
        if isinstance(timed, dict) and timed.get("name") is not None:
            started, duration = _stored_number(timed.get("started_min")), _stored_number(timed.get("duration_min"))
            if started is not None and duration is not None and started + duration > now_min:
                self._timed_preset = str(timed["name"])
                self._timed_started_min = started
                self._timed_duration_min = duration
        shed_loads = data.get("shed_loads")
        self._shed_loads = {e for e in shed_loads if isinstance(e, str)} if isinstance(shed_loads, list) else set()
        self._load_commanded(data.get("commanded"))
        self._loaded_at = dt_util.utcnow().timestamp()
        self._shed_at = {str(k): float(v) for k, v in _stored_dict(data.get("shed_at")).items() if str(k) in self._shed_loads and _stored_number(v) is not None}
        self._shed_context = {}
        self._actuator_opened_by_us = bool(data.get("actuator_opened_by_us", False))
        self._device_rates = {
            str(k): {"rate": float(v["rate"]), "samples": _stored_number(v.get("samples")) or 0.0}
            for k, v in _stored_dict(data.get("device_rates")).items()
            if isinstance(v, dict) and _stored_number(v.get("rate"))
        }
        self._outdoor_bias = OutdoorBias.from_dict(data.get("outdoor_bias"))
        self._kept = kept.from_store(data.get("kept_models"))
        self._refit()
        self._store_loaded = True

    def save_soon(self) -> None:
        """Queue a store write now. Used by the set_house_mode service."""
        self._store.async_delay_save(self._save_payload, 0)

    def reset_learning(self) -> None:
        """Forget the stored samples and the fit. The next cycles start learning again."""
        self._entries = []
        self._fit = None
        self._tpi = None
        self._heat_rate = None
        self._ac_heat_fit = self._dry_fit = self._corrections = None
        self._ac_heat_kw = self._dry_kw = None
        self._kept = {}
        self._kept_used = set()
        self._auto_tpi_gain = 1.0
        self._auto_heat_gain = 1.0
        self._fit_reason = "learning reset"
        self._backtest_runs = []
        self._forecast_error_min = None
        self._samples_since_fit = 0
        self._outdoor_bias = OutdoorBias()
        self._series_cache = None
        self._store.async_delay_save(self._save_payload, 0)

    def export_samples(self) -> list[dict[str, Any]]:
        """All stored readings, for the export service."""
        return [asdict(e) for e in self._entries]

    def _commanded_state(self) -> dict[str, Any]:
        """What this entry last wrote to its devices. The store keeps it, so a restart still knows what it left on."""
        return {
            "setpoint": self._last_written_setpoint,
            "mode": self._last_written_mode,
            "heater_on": self._heater_commanded_on,
            "heater_switch": self._heater_switch_driven,
            "switches_on": sorted(self._switches_on),
            "second_ac_on": self._second_ac_commanded_on,
            "coil_dry": self._coil_dry_sent,
            "valve": self._valve_driven,
            "valve_pct": self._valve_commanded_pct,
        }

    def _load_commanded(self, stored: Any) -> None:
        """Restore what was last commanded. A payload from before this key, or a malformed one, leaves the defaults."""
        stored = stored if isinstance(stored, dict) else {}
        setpoint = stored.get("setpoint")
        self._last_written_setpoint = _stored_number(setpoint)
        mode = stored.get("mode")
        self._last_written_mode = mode if isinstance(mode, str) else None
        heater_on = stored.get("heater_on")
        self._heater_commanded_on = bool(heater_on) if heater_on is not None else None
        switch = stored.get("heater_switch")
        self._heater_switch_driven = switch if isinstance(switch, str) and switch else None
        switches = stored.get("switches_on")
        self._switches_on = {e for e in switches if isinstance(e, str)} if isinstance(switches, list) else set()
        self._second_ac_commanded_on = bool(stored.get("second_ac_on", False))
        self._coil_dry_sent = bool(stored.get("coil_dry", False))
        valve = stored.get("valve")
        self._valve_driven = valve if isinstance(valve, str) and valve else None
        pct = stored.get("valve_pct")
        self._valve_commanded_pct = float(pct) if isinstance(pct, (int, float)) else 0.0
        self._saved_commanded = self._commanded_state()

    def _save_commanded_if_changed(self) -> None:
        """Queue a store write when a command changed what the entry last wrote, so a crash loses at most one tick."""
        current = self._commanded_state()
        if current != self._saved_commanded:
            self._saved_commanded = current
            self._store.async_delay_save(self._save_payload, 0)

    def _save_payload(self) -> dict[str, Any]:
        return {
            "entries": [asdict(e) for e in self._entries],
            "heater_gains": self._heater_gains,
            "auto_tpi_gains": {"cool": self._auto_tpi_gain, "heat": self._auto_heat_gain},
            "investigation_report": self._report,
            "target_override_c": self._target_override_c,
            "heat_override_c": self._heat_override_c,
            "humidity_target_pct": self._humidity_target_pct,
            "user_off": self._user_off,
            "user_mode": self._user_mode,
            "locked": self._locked,  # the PIN stays in the config entry and is never stored here
            "house_mode": house_mode_from_store(self.hass.data.get(DOMAIN, {})),
            "inhibit_until_min": self._inhibit_until_min,
            "timed_preset": None
            if self._timed_preset is None or self._timed_started_min is None
            else {"name": self._timed_preset, "started_min": self._timed_started_min, "duration_min": self._timed_duration_min},
            "shed_loads": sorted(self._shed_loads),
            "commanded": self._commanded_state(),
            "shed_at": {e: t for e, t in sorted(self._shed_at.items()) if e in self._shed_loads},
            "actuator_opened_by_us": self._actuator_opened_by_us,
            "device_rates": self._device_rates,
            "outdoor_bias": self._outdoor_bias.to_dict(),
            "kept_models": kept.to_store(self._kept),
        }

    async def async_shutdown_store(self) -> None:
        await self._store.async_save(self._save_payload())
