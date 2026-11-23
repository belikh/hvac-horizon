"""Sensors that publish the forecast from the coordinator."""

from __future__ import annotations

import json

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfEnergy, UnitOfPower, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HorizonCoordinator
from .coordinator_common import room_device_info

# Read-only entities of a coordinator: no limit on parallel updates.
PARALLEL_UPDATES = 0


# Published keys shown as plain sensors. Each key is read from coordinator.data. A list or dict key shows its
# content: the state is the JSON text, cut to 255 characters, and the attribute "value" holds the full value.
# Every status sensor, in the order they are created. The names are in translations/en.json under entity.sensor.
# The values that are on or off are binary sensors: see BINARY_KEYS in binary_sensor.py.
STATUS_KEYS: tuple[str, ...] = (
    "day_type", "night_plan", "precool_status", "daily_briefing", "window_advice", "ventilation", "ventilation_rate", "humidity",
    "compliance_pct", "fan_speed", "price_used_per_kwh", "occupancy", "solar_gain_kw", "band_action", "target_c", "preset",
    "samples_heat", "samples_dry", "heater_feedback", "valve_opening_pct", "cover_action", "second_ac",
    "learning_suggestion", "sensors_stalled", "commands_unconfirmed", "free_cooling", "forecast_mode", "forecast_target",
    "fit_corrections", "fit_window_days", "kept_models", "heat_model", "dry_model", "watchdog_trips", "rooms_max_age_s", "safety_output_pct",
    "economizer", "motion_preset", "shed", "restored", "mpc_plan", "mpc_status", "mpc_heat_steps", "solar_scale", "heat_source",
    "heater_pid_output", "heater_output_pct", "tpi_output_pct", "preheat",
    "heat_up_min", "cool_on_above_c", "heat_on_below_c", "backtest_runs", "trv_offset_k", "room_fit", "forecast_days", "dispatch", "pending",
    "ac_setpoint_sent_c", "ac_sensor_offset_k", "minutes_low", "minutes_high", "outdoor_forecast_next_c", "outdoor_source",
    "solar_factor", "outdoor_bias_now_k", "outdoor_bias_pairs",
)

# Sensors a user reads every day. They are enabled and have no entity category.
PRIMARY_KEYS = frozenset(
    {
        "minutes_to_setpoint", "energy_kwh", "cost", "status", "forecast_error_min", "target_c", "preset",
        "cool_on_above_c", "heat_on_below_c", "minutes_low", "minutes_high", "forecast_mode",
    }
)
# Diagnostic sensors that stay enabled: a user reads or acts on them.
DIAGNOSTIC_ENABLED_KEYS = frozenset(
    {
        "sensors_stalled", "commands_unconfirmed", "watchdog_trips", "heating_failure", "ac_failure", "window_advice", "mould_risk", "dispatch", "precool_status", "mpc_status",
        "samples", "day_type", "night_plan", "daily_briefing", "ventilation", "humidity", "drying_needed",
        "compliance_pct", "heatwave", "occupancy", "heating_allowed", "vacation", "heat_source", "whole_house_fan",
        "learning_suggestion", "external_change", "pending", "outdoor_forecast_used", "outdoor_source", "ac_setpoint_sent_c", "ac_sensor_offset_k",
    }
)
# Diagnostic sensors that are internal detail. They are created disabled; enable one in the entity settings.
DIAGNOSTIC_DISABLED_KEYS = frozenset(
    {
        "heater_pwm_on", "heater_pid_output", "heater_output_pct", "tpi_output_pct", "safety_output_pct", "heater_feedback",
        "mpc_plan", "mpc_heat_steps", "shed", "restored", "room_fit", "backtest_runs", "rooms_max_age_s", "samples_heat",
        "samples_dry", "forecast_days", "outdoor_forecast_next_c", "solar_factor", "outdoor_bias_now_k", "outdoor_bias_pairs", "trv_offset_k", "valve_opening_pct", "solar_scale", "solar_gain_kw",
        "ventilation_rate", "fan_speed", "band_action", "house_cooling", "heat_up_min", "boiler_demand", "second_ac",
        "economizer", "free_cooling", "fit_corrections", "fit_window_days", "kept_models", "heat_model", "dry_model", "forecast_target", "motion_preset", "price_used_per_kwh", "cover_action", "preheat",
    }
)
_TEMPERATURE = (SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS)
_DIFFERENCE = (SensorDeviceClass.TEMPERATURE_DELTA, UnitOfTemperature.KELVIN)
_MINUTES = (SensorDeviceClass.DURATION, UnitOfTime.MINUTES)
# The device class and unit of each numeric sensor. With a device class Home Assistant shows the value in the user's
# unit system and keeps statistics. A percentage and a price have no device class that fits, so they carry a unit only.
MEASURES: dict[str, tuple[SensorDeviceClass | None, str | None]] = {
    "minutes_to_setpoint": _MINUTES, "minutes_low": _MINUTES, "minutes_high": _MINUTES, "forecast_error_min": _MINUTES, "heat_up_min": _MINUTES,
    "energy_kwh": (SensorDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR),
    "target_c": _TEMPERATURE, "cool_on_above_c": _TEMPERATURE, "heat_on_below_c": _TEMPERATURE, "ac_setpoint_sent_c": _TEMPERATURE,
    "trv_offset_k": _DIFFERENCE, "ac_sensor_offset_k": _DIFFERENCE, "outdoor_bias_now_k": _DIFFERENCE,
    "rooms_max_age_s": (SensorDeviceClass.DURATION, UnitOfTime.SECONDS),
    "fit_window_days": (SensorDeviceClass.DURATION, UnitOfTime.DAYS),
    "solar_gain_kw": (SensorDeviceClass.POWER, UnitOfPower.KILO_WATT),
    "compliance_pct": (None, PERCENTAGE), "safety_output_pct": (None, PERCENTAGE), "valve_opening_pct": (None, PERCENTAGE),
    "heater_pid_output": (None, PERCENTAGE), "heater_output_pct": (None, PERCENTAGE), "tpi_output_pct": (None, PERCENTAGE),
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: HorizonCoordinator = entry.runtime_data
    async_add_entities(
        [
            HorizonSensor(coordinator, entry, "minutes_to_setpoint", SensorStateClass.MEASUREMENT),
            # A forecast of energy is no meter reading, so it has no state class: Home Assistant allows only totals for the energy class.
            HorizonSensor(coordinator, entry, "energy_kwh", None),
            HorizonSensor(coordinator, entry, "cost", SensorStateClass.MEASUREMENT),
            HorizonSensor(coordinator, entry, "status", None),
            HorizonSensor(coordinator, entry, "samples", SensorStateClass.MEASUREMENT),
            HorizonSensor(coordinator, entry, "forecast_error_min", SensorStateClass.MEASUREMENT),
        ]
        + [HorizonStatusSensor(coordinator, entry, key) for key in STATUS_KEYS]
    )


def _set_category(entity: Entity, key: str) -> None:
    """Mark every sensor outside PRIMARY_KEYS diagnostic, and the internal ones disabled by default."""
    if key in PRIMARY_KEYS:
        return
    entity._attr_entity_category = EntityCategory.DIAGNOSTIC
    entity._attr_entity_registry_enabled_default = key not in DIAGNOSTIC_DISABLED_KEYS


class HorizonSensor(CoordinatorEntity[HorizonCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, key, state_class) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_translation_key = key
        self._attr_device_class, self._attr_native_unit_of_measurement = MEASURES.get(key, (None, None))
        self._attr_state_class = state_class
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = room_device_info(coordinator.hass, entry)
        _set_category(self, key)

    @property
    def extra_state_attributes(self):
        if self.coordinator.data is None:
            return None
        if self._key == "forecast_error_min":
            # The last 20 backtest runs. The state is the median absolute error over every run.
            # range_coverage_pct is the share of range_runs whose measured time fell inside the forecast range.
            d = self.coordinator.data
            return {"accuracy_pairs": d.get("accuracy_pairs"), "range_coverage_pct": d.get("range_coverage_pct"), "range_runs": d.get("range_runs")}
        if self._key != "status":
            return None
        d = self.coordinator.data
        return {
            "reason": d.get("reason"),
            "k_per_h": d.get("k"),
            "c_k_per_kw": d.get("c"),
            "g_k_per_h": d.get("g"),
            "fit_residual_k_per_h": d.get("fit_residual_k_per_h"),  # residual standard deviation of the fit, K/h
            "k_stderr": d.get("k_stderr"),  # standard errors of the fitted coefficients; None without a fit
            "c_stderr": d.get("c_stderr"),
            "g_stderr": d.get("g_stderr"),
            "fit_outliers_dropped": d.get("fit_outliers_dropped"),  # samples removed by the residual filter
            "fit_confidence": d.get("fit_confidence"),  # low, medium or high from the relative standard error of c
            "autotune_status": d.get("autotune_status"),
            "autotune": d.get("autotune"),  # kp, ki, kd after a relay test; None before
            "shed_skipped": d.get("shed_skipped"),  # set when a shed load entry is malformed
            "investigation": d.get("investigation"),  # rule-based answer to why the AC runs or not, and recent actions
            "investigation_ai": d.get("investigation_ai"),  # conversation agent answer, when a flow field sets one
            "investigation_ai_mode": d.get("investigation_ai_mode"),  # the AC mode that answer explains; None with no answer
            "ai_calls_today": d.get("ai_calls_today"),
            "investigation_report": d.get("investigation_report"),  # 24-hour report from generate_investigation_report; None until run
            "investigation_report_source": d.get("investigation_report_source"),  # "model" when the agent answered, else "rule"
            "investigation_report_at": d.get("investigation_report_at"),  # ISO time the report was stored
            "room_rate_coefficients": d.get("room_rate_coefficients"),  # k, g from AC-off, c from AC-on; None until fitted
            "auto_tpi_gains": d.get("auto_tpi_gains"),  # online cool gain on c, clamped to 0.5 to 1.5; 1.0 until it moves
        }

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        key = {"minutes_to_setpoint": "minutes", "energy_kwh": "energy_kwh", "cost": "cost", "status": "status", "samples": "samples", "forecast_error_min": "forecast_error_min"}[self._key]
        return self.coordinator.data.get(key)


def _plain(value):
    """A list or dict as JSON-friendly data. Sets and tuples become lists."""
    return list(value) if isinstance(value, (tuple, set)) else value


class HorizonStatusSensor(CoordinatorEntity[HorizonCoordinator], SensorEntity):
    """Shows one published key. Returns None when the key is absent."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, key) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_translation_key = key
        self._attr_device_class, self._attr_native_unit_of_measurement = MEASURES.get(key, (None, None))
        if self._attr_device_class is not None:
            self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = room_device_info(coordinator.hass, entry)
        _set_category(self, key)

    @property
    def extra_state_attributes(self):
        if self.coordinator.data is None:
            return None
        value = self.coordinator.data.get(self._key)
        if isinstance(value, (list, tuple, set, dict)):
            return {"value": _plain(value)}
        return None

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        value = self.coordinator.data.get(self._key)
        if isinstance(value, (list, tuple, set, dict)):
            return json.dumps(_plain(value), default=str)[:255]
        if isinstance(value, str):
            return value[:255]
        if isinstance(value, (bool, int, float)) or value is None:
            return value
        return str(value)[:255]
