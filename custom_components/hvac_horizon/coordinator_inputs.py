"""Input reading: room and optional sensors, stall detection, derived context (occupancy, mould risk, night plan, window advice, day type) and device-state checks."""

from __future__ import annotations

import statistics
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util
from homeassistant.helpers import issue_registry as ir
from homeassistant.components import persistent_notification

from .const import (
    CONF_AWAY_DELAY_MIN,
    CONF_CLIMATE,
    CONF_CLIMATE_2,
    CONF_ENERGY,
    CONF_HUMIDITY,
    CONF_OUTDOOR,
    CONF_POWER,
    CONF_POWER_2,
    CONF_PRESENCE,
    CONF_PRICE,
    CONF_ROOM_HUMIDITY,
    CONF_ROOM_SENSORS,
    CONF_STALL_WINDOW_S,
    DEFAULT_AWAY_DELAY_MIN,
    DOMAIN,
)
from custom_components.hvac_horizon.collector import Entry
from custom_components.hvac_horizon.outdoor import night_low
from custom_components.hvac_horizon.features import (
    SENSOR_STALL_S,
    classify_day,
    mode_disagrees,
    mould_risk,
    natural_ventilation_window,
    night_plan,
    presence_mode,
    presence_sources,
    sensor_stalled,
    surface_humidity,
)
from custom_components.hvac_horizon.coordinator_common import _float


class InputsMixin:
    # Instance attributes set in HorizonCoordinator.__init__ and used here.
    _cool_above_c: float
    _forecast_daily: list[dict]
    _forecast_highs: list[float]
    _last_home_at: float | None
    _last_room_data_s: float | None
    _last_written_mode: str | None
    _last_written_setpoint: float | None
    _pwm_pct_now: float
    _stall_reported: bool
    _window_advice_open: bool
    entry: ConfigEntry
    hass: HomeAssistant

    def _mould_risk(self) -> bool:
        """Mould risk from the room humidity and the outdoor temperature as a cold-surface proxy.

        Needs the room humidity sensor. Without it the risk is reported as not present.
        """
        room_rh = _float(self._optional(CONF_ROOM_HUMIDITY))
        rooms = self._rooms()
        outdoor = self._outdoor_now()
        if room_rh is None or not rooms or outdoor is None:
            return False
        surface = surface_humidity(room_rh, statistics.median(rooms), outdoor)
        return mould_risk(surface)

    def _occupancy_mode(self, now: float) -> str:
        """Home or away from the optional presence sources. Tracks the last time any source was home."""
        states = [self.hass.states.get(entity) for entity in presence_sources(self.entry.data.get(CONF_PRESENCE))]
        home = [s.state in ("on", "home") for s in states if s is not None]
        if any(home) or self._last_home_at is None:
            # The away clock starts at the first cycle when no source has been home yet, so an away house goes away.
            self._last_home_at = now
        since = (now - self._last_home_at) / 60.0
        return presence_mode(home, float(self.entry.data.get(CONF_AWAY_DELAY_MIN, DEFAULT_AWAY_DELAY_MIN)), since)

    def _night_plan(self) -> str | None:
        """Overnight plan from the room temperature and the coming night's low.

        The low is `templow` of the first daily forecast item dated today or later that has one. Without it
        the current outdoor reading stands in.
        """
        rooms = self._rooms()
        low = night_low(self._forecast_daily, dt_util.now().date(), dt_util.get_default_time_zone())
        if low is None:
            low = self._outdoor_now()
        if not rooms or low is None:
            return None
        comfort = self.comfort_c
        return night_plan(low, statistics.median(rooms), comfort)

    def _switch_on(self, entity_id: str | None) -> bool:
        """True when the switch entity exists and is on. The on-state that the hysteresis decisions take."""
        state = self.hass.states.get(entity_id) if entity_id else None
        return state is not None and state.state == "on"

    def _window_advice(self) -> str | None:
        """Advice to open, close or hold windows from the outdoor reading and the room median. Publishes advice only."""
        rooms = self._rooms()
        outdoor = self._outdoor_now()
        if not rooms or outdoor is None:
            return None
        return natural_ventilation_window(outdoor, statistics.median(rooms), cool_above_c=self._cool_above_c, is_open=self._window_advice_open)

    def _day_type(self) -> str | None:
        """Day class from the outdoor sensor's reading. Uses the current reading until a forecast is wired in."""
        if self._forecast_highs:
            return classify_day(self._forecast_highs[0]).value
        outdoor = self._outdoor_now()
        return None if outdoor is None else classify_day(outdoor).value

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

    def _device_setpoint(self) -> float | None:
        """The setpoint the climate entity reports, or None when it reports none."""
        climate = self.hass.states.get(self.entry.data[CONF_CLIMATE])
        if climate is None or climate.attributes.get("temperature") is None:
            return None
        return float(climate.attributes["temperature"])

    def _external_change(self) -> bool:
        """True when the climate setpoint differs from the last one this integration wrote (someone changed it by hand)."""
        current = self._device_setpoint()
        if current is None or self._last_written_setpoint is None:
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

    def _stall_blocks(self, result: dict[str, Any]) -> bool:
        """True when a stall stops the AC and the heater. A stalled outdoor sensor does unless a fresh hourly forecast stands in for it, and so does every room sensor being stale.

        One stale room sensor drops out of the median, and a fresh one keeps control running.
        """
        stalled = set(result.get("sensors_stalled") or [])
        if self.entry.data[CONF_OUTDOOR] in stalled and self._forecast_outdoor(dt_util.utcnow().timestamp()) is None:
            return True
        rooms = list(self.entry.data.get(CONF_ROOM_SENSORS) or [])
        fresh = [r for r in rooms if r not in stalled and _float(self.hass.states.get(r)) is not None]
        return not fresh and any(r in stalled for r in rooms)

    def _stalled_sensors(self, now: float) -> list[str]:
        """Room and outdoor sensors that have not reported recently. Published so the user can see them.

        Reads last_reported. Home Assistant keeps last_updated when a sensor repeats its value,
        so a steady reading would look stalled.
        """
        names = list(self.entry.data[CONF_ROOM_SENSORS]) + [self.entry.data[CONF_OUTDOOR]]
        stalled = []
        for name in names:
            state = self.hass.states.get(name)
            # A missing or unavailable sensor is not a stall. _rooms() and _read_inputs() already treat it as no reading.
            if state is None or state.state in ("unavailable", "unknown"):
                continue
            if sensor_stalled(state.last_reported.timestamp(), now, self._stall_window_s()):
                stalled.append(name)
        return stalled

    def _no_room_data_stop(self, now: float) -> bool:
        """True when no room has had a valid reading for SENSOR_STALL_S. The AC then stops."""
        return self._last_room_data_s is not None and now - self._last_room_data_s >= self._stall_window_s()

    def _stall_window_s(self) -> float:
        """The stall window in seconds, from the stall_window_s option. Defaults to SENSOR_STALL_S."""
        return float(self.entry.data.get(CONF_STALL_WINDOW_S, SENSOR_STALL_S))

    def _room_states(self) -> list[State]:
        """The room sensor states whose readings feed the median: the room sensors only.

        The AC's own temperature reading is whole degrees, so it is kept out of the median.
        """
        stalled = set(self._stalled_sensors(dt_util.utcnow().timestamp()))
        every = [self.hass.states.get(e) for e in self.entry.data[CONF_ROOM_SENSORS]]
        fresh = [s for e, s in zip(self.entry.data[CONF_ROOM_SENSORS], every) if e not in stalled]
        # Stale room sensors drop out only when a fresh one exists. With none, the stop rule in _stall_blocks decides.
        states = fresh if any(_float(s) is not None for s in fresh) else every
        return [s for s in states if _float(s) is not None]

    def _rooms(self) -> list[float]:
        """Room readings for the median."""
        return [_float(s) for s in self._room_states()]

    def _rooms_max_age_s(self, now: float) -> float | None:
        """Age in seconds of the oldest reading the median uses, from last_reported. None when no reading is used.

        The readings can be up to one stall window apart, so the median mixes times by that much.
        """
        ages = [now - s.last_reported.timestamp() for s in self._room_states()]
        return round(max(ages), 1) if ages else None

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
        second_w = _float(self._optional(CONF_POWER_2)) if self.entry.data.get(CONF_CLIMATE_2) else None
        return Entry(
            t=now,
            room_c=statistics.median(rooms),
            outdoor_c=outdoor,
            power_w=power_w,
            power_2_w=second_w,
            setpoint_c=float(setpoint) if setpoint is not None else None,
            mode=climate.state if climate else None,
            rooms_c=tuple(rooms),
            humidity_pct=_float(self._optional(CONF_HUMIDITY)),
            price=price,
            energy_kwh=_float(self._optional(CONF_ENERGY)),
            heat_pct=self._pwm_pct_now,
            device_kw=self._fleet_kw(),
        )

    def median_room_c(self) -> float | None:
        """Median of the room readings, or None when there are none."""
        rooms = self._rooms()
        return statistics.median(rooms) if rooms else None
