"""User commands and presets: target, off, lock, manual and timed presets, what-if answers and the relay auto-tune."""

from __future__ import annotations

import statistics
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    AUTOTUNE_MAX_SETPOINT_C,
    AUTOTUNE_MIN_RISE_K,
    AUTOTUNE_MIN_SETPOINT_C,
    CONF_CLIMATE,
    CONF_COMFORT_C,
    CONF_HEATER_SWITCH,
    CONF_DEHUMIDIFIER,
    CONF_AC_HEAT,
    CONF_MPC_HEAT_PLAN,
    CONF_LOCK_PIN,
    CONF_POWER,
    DEFAULT_COMFORT_C,
)
from custom_components.hvac_horizon.room_model.collector import RUNNING_MIN_KW
from custom_components.hvac_horizon.engine.fleet import COOL, HEAT
from custom_components.hvac_horizon.room_model.model import Fit
from custom_components.hvac_horizon.room_model.pid import Pid
from custom_components.hvac_horizon.engine.decide import Preset
from custom_components.hvac_horizon.room_model.autotune import RelayTuner
from custom_components.hvac_horizon.engine.features import (
    HUMIDITY_BAND_HIGH_PCT,
    HUMIDITY_BAND_LOW_PCT,
    HUMIDITY_BAND_MIN_WIDTH_PCT,
    HUMIDITY_TARGET_MAX_PCT,
    HUMIDITY_TARGET_MIN_PCT,
    check_pin,
    lock_allows_control,
    timed_preset_active,
    timed_preset_name,
)
from custom_components.hvac_horizon.room_model.forecast import forecast
from custom_components.hvac_horizon.coordinator_common import USER_MODES, _float


class UserMixin:
    # Instance attributes set in HorizonCoordinator.__init__ and used here.
    _autotune_result: Any
    _autotune_status: str | None
    _baseline_k: float | None
    _cool_above_c: float
    _cool_shown_c: float
    _dismissed_for_k: float | None
    _fit: Fit | None
    _fit_reason: str
    _heat_target_c: float
    _heater_gains: dict[str, float] | None
    _heater_pid: Pid | None
    _locked: bool
    _manual_preset: str | None
    _manual_scheduled: str | None
    _preset_now: str | None
    _store: Store[dict[str, Any]]
    _target_override_c: float | None
    _heat_override_c: float | None
    _humidity_target_pct: float | None
    _timed_duration_min: float
    _timed_preset: str | None
    _timed_started_min: float | None
    _tuner: RelayTuner | None
    _tuner_started_s: float
    _user_mode: str
    _user_off: bool
    entry: ConfigEntry
    hass: HomeAssistant

    def what_if(self, room_c: float, target_c: float, outdoor_c: float, power_kw: float) -> dict[str, Any]:
        """Forecast minutes, energy and cost for given conditions, using the current fit and price slots."""
        active = self._active_fit()
        if active is None:
            return {"status": "learning", "reason": self._fit_reason}
        f = forecast(active, room_c, target_c, outdoor_c, power_kw, self._price_forecast(dt_util.utcnow().timestamp()))
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
        self._store.async_delay_save(self._save_payload, 0)

    def _effective_preset(self, now_min: float, normal: str) -> str:
        if self._manual_preset is not None:
            if self._manual_scheduled is None:
                self._manual_scheduled = normal
            elif normal != self._manual_scheduled:
                self._manual_preset = None  # the schedule moved to another block
                self._manual_scheduled = None
        active = timed_preset_active(self._timed_started_min, now_min, self._timed_duration_min)
        return timed_preset_name(active, self._timed_preset, self._manual_preset or normal)

    @property
    def comfort_c(self) -> float:
        """The comfort target: the user's target from the climate entity, else the configured comfort setpoint."""
        if self._target_override_c is not None:
            return self._target_override_c
        return float(self.settings.get(CONF_COMFORT_C, DEFAULT_COMFORT_C))

    @property
    def heat_comfort_c(self) -> float:
        """The comfort heat target: the low target the user set in auto, else the comfort target. It never exceeds the comfort target."""
        if self._heat_override_c is not None:
            return min(self._heat_override_c, self.comfort_c)
        return self.comfort_c

    def effective_target_c(self, heating: bool) -> float:
        """The target the thermostat shows: the heat target when `heating`, else the cool target. Both follow the preset.

        The cool target is the one the user set. The threshold the AC cools above can differ from it by the price shift.
        """
        return self._heat_target_c if heating else self._cool_shown_c

    def device_action(self) -> str:
        """What the room's devices do now, from their own states: "cooling", "heating", "drying", "fan", "idle" or "off".

        "fan" means the first AC is in its fan mode and nothing heats or cools.

        Reads the first AC, the heater switch and the fleet devices. A climate in cool or heat counts as running only when
        its power sensor reads at least RUNNING_MIN_KW (below that it runs the fan only, which is "idle"). A device with no
        power reading counts by its state. "off" means the user holds the room off and nothing runs. A room with control
        off and nothing running is "idle".
        """
        data = self.settings
        climate = self.hass.states.get(self._ac_id)
        fan_only = False
        if climate is not None and climate.state in ("cool", "heat", "dry"):
            power = self._ac_power_w()
            if power is None or power / 1000.0 >= RUNNING_MIN_KW:
                return {"cool": "cooling", "heat": "heating", "dry": "drying"}[climate.state]
            fan_only = True
        dehumidifier = self.hass.states.get(data[CONF_DEHUMIDIFIER]) if data.get(CONF_DEHUMIDIFIER) else None
        if dehumidifier is not None and dehumidifier.state == "on" and self.dry_mode:
            return "drying"
        heater = self.hass.states.get(data[CONF_HEATER_SWITCH]) if data.get(CONF_HEATER_SWITCH) else None
        if heater is not None and heater.state == "on":
            return "heating"
        for device in self._fleet():
            if self._fleet_running(device):
                sensor = _float(self.hass.states.get(device.power_sensor)) if device.power_sensor else None
                if sensor is not None and sensor / 1000.0 < RUNNING_MIN_KW:
                    fan_only = True
                    continue
                return "cooling" if device.kind == COOL else "heating"
        if fan_only:
            return "idle"
        if climate is not None and climate.state == "fan_only":
            return "fan"
        return "off" if self._user_off else "idle"

    @property
    def preset_now(self) -> str | None:
        return self._preset_now

    @property
    def user_off(self) -> bool:
        return self._user_off

    def _ac_lists(self, mode: str) -> bool:
        """True when the first AC lists `mode`. An AC whose mode list is not known yet counts as able."""
        climate = self.hass.states.get(self._ac_id)
        modes = None if climate is None else climate.attributes.get("hvac_modes")
        return modes is None or mode in modes

    @property
    def has_heat(self) -> bool:
        """True when the entry can heat: a heater switch, the AC (ac_heat or mpc_heat_plan, and a heat mode on the AC) or a heating fleet device."""
        data = self.settings
        return (
            bool(data.get(CONF_HEATER_SWITCH))
            or (self.has_ac and self._ac_lists("heat") and (bool(data.get(CONF_AC_HEAT, False)) or bool(data.get(CONF_MPC_HEAT_PLAN, False))))
            or any(device.kind == HEAT for device in self._fleet())
        )

    @property
    def has_cool(self) -> bool:
        """True when the entry can cool: a first AC or a cooling fleet device. A room with heating only has neither."""
        return self.has_ac or any(device.kind == COOL for device in self._fleet())

    @property
    def can_dry(self) -> bool:
        """True when the entry can dehumidify: a dehumidifier switch, or a first AC that lists a dry mode.

        An AC whose mode list is not known yet counts as able. A room run by fleet climates has no dry path for the AC.
        """
        data = self.settings
        if data.get(CONF_DEHUMIDIFIER):
            return True
        if not self.has_ac or any(device.entity_id for device in self._fleet()):
            return False
        return self._ac_lists("dry")

    @property
    def can_fan(self) -> bool:
        """True when the first AC lists a fan mode. An AC whose mode list is not known yet counts as able.

        A room run by fleet climates has no fan path for the AC.
        """
        if not self.has_ac or any(device.entity_id for device in self._fleet()):
            return False
        return self._ac_lists("fan_only")

    @property
    def user_mode(self) -> str:
        """The mode in force: "cool", "heat", "auto", "dry" or "fan_only". The climate entity shows it and control obeys it.

        A stored "heat" on an entry that can no longer heat reads "auto". So does a stored "dry" on an entry that
        cannot dehumidify and a stored "fan_only" on an AC with no fan mode, so the entity and control agree.
        """
        if not self.has_cool:
            return "dry" if self._user_mode == "dry" and self.can_dry else "heat"
        if (
            (self._user_mode == "heat" and not self.has_heat)
            or (self._user_mode == "dry" and not self.can_dry)
            or (self._user_mode == "fan_only" and not self.can_fan)
        ):
            return "auto"
        return self._user_mode

    @property
    def fan_only_mode(self) -> bool:
        """True while the user chose the fan mode and the room is not held off. The AC runs its fan and nothing heats or cools."""
        return self.user_mode == "fan_only" and not self._user_off

    @property
    def humidity_target_pct(self) -> float:
        """The room dries above this relative humidity. The user sets it on the thermostat. The default is HUMIDITY_BAND_HIGH_PCT."""
        return HUMIDITY_BAND_HIGH_PCT if self._humidity_target_pct is None else self._humidity_target_pct

    @property
    def humidify_below_pct(self) -> float:
        """A humidifier runs below this: 40 %, or HUMIDITY_BAND_MIN_WIDTH_PCT under a humidity target below 60 %."""
        return min(HUMIDITY_BAND_LOW_PCT, self.humidity_target_pct - HUMIDITY_BAND_MIN_WIDTH_PCT)

    def set_humidity_target(self, humidity_pct: float) -> None:
        """Store the humidity target from the thermostat."""
        self._require_unlocked()
        if not HUMIDITY_TARGET_MIN_PCT <= humidity_pct <= HUMIDITY_TARGET_MAX_PCT:
            raise HomeAssistantError(f"the humidity target {humidity_pct} is outside {HUMIDITY_TARGET_MIN_PCT:.0f} to {HUMIDITY_TARGET_MAX_PCT:.0f} %")
        self._humidity_target_pct = float(humidity_pct)
        self._store.async_delay_save(self._save_payload, 0)

    @property
    def dry_mode(self) -> bool:
        """True while the user chose dry and the room is not held off. The AC then dries and never cools."""
        return self.user_mode == "dry" and not self._user_off

    @property
    def cooling_permitted(self) -> bool:
        """True when the mode lets the room cool: cool or auto. In heat, dry and fan mode nothing cools."""
        return self.user_mode in ("cool", "auto")

    @property
    def heating_permitted(self) -> bool:
        """True when the mode lets the room heat: heat, auto or dry. In cool mode and in fan mode nothing heats.

        Dry mode cools the room as it removes water, so the heating devices may hold the heat target while the AC dries.
        """
        return self.user_mode in ("heat", "auto", "dry")

    def _require_unlocked(self) -> None:
        if not lock_allows_control(self._locked):
            raise HomeAssistantError("HVAC Horizon entry is locked")

    def set_user_target(self, target_c: float) -> None:
        """Store the user's target and make it apply now.

        The target is the comfort target, so the room returns to the comfort preset: a timed preset ends, and a schedule
        block with another preset is set aside until the schedule moves to its next block.
        """
        self._require_unlocked()
        self._target_override_c = float(target_c)
        self._heat_override_c = None  # one target serves cooling and heating
        self._to_comfort_preset()

    def set_user_target_range(self, low_c: float, high_c: float) -> None:
        """Store separate targets: the room heats to `low_c` and cools above `high_c`. Both apply now, as in set_user_target."""
        self._require_unlocked()
        if low_c > high_c:
            raise HomeAssistantError(f"the low target {low_c} is above the high target {high_c}")
        self._target_override_c = float(high_c)
        self._heat_override_c = float(low_c)
        self._to_comfort_preset()

    async def async_set_ac_option(self, option: str, value: str) -> None:
        """Pass a fan mode or a swing mode from the room thermostat to the first AC."""
        self._require_unlocked()
        if not self.has_ac:
            raise HomeAssistantError("this room has no AC")
        await self.hass.services.async_call("climate", f"set_{option}", {"entity_id": self._ac_id, option: value}, blocking=True)

    def _to_comfort_preset(self) -> None:
        self._manual_preset = Preset.COMFORT.value
        self._manual_scheduled = None
        self._timed_preset = None
        self._timed_started_min = None
        self._timed_duration_min = 0.0
        self._store.async_delay_save(self._save_payload, 0)

    def set_user_off(self, off: bool, mode: str | None = None) -> None:
        """Hold the room off (AC and heater) or return it to automatic control. The next tick sends the change through the usual gates.

        `mode` (one of USER_MODES) records the mode the user chose and is kept with the hold.
        """
        self._require_unlocked()
        self._user_off = off
        if mode in USER_MODES:
            self._user_mode = mode
        self._store.async_delay_save(self._save_payload, 0)

    def set_manual_preset(self, preset: str) -> None:
        """Use `preset` until the schedule changes block. A timed preset still wins while it runs. Not stored across a restart."""
        self._require_unlocked()
        if preset not in {p.value for p in Preset}:
            raise HomeAssistantError(f"unknown preset {preset}")
        self._manual_preset = preset
        self._manual_scheduled = None

    def set_lock(self, locked: bool, pin: str | None) -> bool:
        """Lock or unlock control. Unlocking needs the configured PIN. Returns True when the lock changed."""
        configured = self.settings.get(CONF_LOCK_PIN)
        if not locked:
            if not check_pin(pin, configured):
                return False
        if locked and not configured:
            return False
        self._locked = locked
        self._store.async_delay_save(self._save_payload, 0)
        return True

    def start_autotune(self, setpoint_c: float) -> None:
        """Begin a relay test at `setpoint_c`. The heater follows the test only while control is on and no gate is closed.

        Raises ValueError when the setpoint is outside AUTOTUNE_MIN_SETPOINT_C to AUTOTUNE_MAX_SETPOINT_C, when no
        room reading exists, or when the setpoint is less than AUTOTUNE_MIN_RISE_K above the room.
        """
        if not AUTOTUNE_MIN_SETPOINT_C <= setpoint_c <= AUTOTUNE_MAX_SETPOINT_C:
            raise ValueError(f"auto-tune setpoint must be {AUTOTUNE_MIN_SETPOINT_C:g} to {AUTOTUNE_MAX_SETPOINT_C:g} C")
        rooms = self._rooms()
        if not rooms:
            raise ValueError("auto-tune needs a room temperature reading")
        room_c = statistics.median(rooms)
        if setpoint_c < room_c + AUTOTUNE_MIN_RISE_K:
            raise ValueError(
                f"auto-tune setpoint {setpoint_c:g} C must be at least {AUTOTUNE_MIN_RISE_K:g} K above the room ({room_c:.1f} C)"
            )
        self._tuner = RelayTuner(setpoint_c=setpoint_c)
        self._tuner_started_s = dt_util.utcnow().timestamp()
        self._autotune_result = None
        self._autotune_status = "running"

    async def async_cancel_autotune(self) -> None:
        """End a running relay test and turn the heater off. Does nothing when no test runs."""
        if self._tuner is None:
            return
        self._tuner = None
        self._autotune_status = "cancelled"
        await self.async_heater_off()

    def _relay_tick(self, now: float, room_c: float | None, may_drive: bool) -> bool:
        """One relay test step. Returns the heater state to command.

        `may_drive` is False when control is off or a gate is closed. The test then records nothing and the
        heater stays off, because the room is not under relay control.
        """
        if self._tuner is None or room_c is None or not may_drive:
            return False
        want_on = self._tuner.step(room_c, now / 3600.0)
        if self._tuner.done:
            self._finish_relay()
            return False
        return want_on

    def _finish_relay(self) -> None:
        """End the test and keep its gains. The heater stays off this step and the PWM path resumes on the next."""
        result = self._tuner.result()
        self._tuner = None
        self._autotune_result = result
        self._autotune_status = "done"
        if result is not None:
            self._heater_gains = {"kp": result.kp, "ki": result.ki, "kd": result.kd}
            if self._heater_pid is not None:
                self._heater_pid.set_gains(*self._heater_gain_values())
            self._store.async_delay_save(self._save_payload, 0)

    def respond_suggestion(self, accept: bool) -> None:
        """Accept adopts the learned heat gain as the baseline; dismiss hides the suggestion until the gain drifts."""
        if accept:
            if self._fit is not None:
                self._baseline_k = self._fit.k
            self._dismissed_for_k = None
        elif self._fit is not None:
            self._dismissed_for_k = self._fit.k
