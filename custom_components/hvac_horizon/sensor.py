"""Sensors that publish the forecast from the coordinator."""

from __future__ import annotations

import json

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HorizonCoordinator


# Published keys shown as plain sensors. Each key is read from coordinator.data. A list or dict key shows its
# content: the state is the JSON text, cut to 255 characters, and the attribute "value" holds the full value.
STATUS_KEYS: dict[str, str] = {
    "day_type": "Day type",
    "night_plan": "Night plan",
    "precool_status": "Pre-cool status",
    "daily_briefing": "Daily briefing",
    "window_advice": "Window advice",
    "ventilation": "Ventilation",
    "ventilation_rate": "Ventilation rate",
    "humidity": "Humidity action",
    "drying_needed": "Drying needed",
    "mould_risk": "Mould risk",
    "compliance_pct": "Comfort compliance",
    "heatwave": "Heatwave",
    "fan_speed": "Fan speed",
    "price_used_per_kwh": "Price used per kWh",
    "occupancy": "Occupancy",
    "solar_gain_kw": "Solar gain",
    "band_action": "Band action",
    "target_c": "Target temperature",
    "preset": "Preset",
    "heating_allowed": "Heating allowed",
    "samples_heat": "AC heat intervals",
    "samples_dry": "AC dry intervals",
    "heater_feedback": "Heater feedback",
    "heater_pwm_on": "Heater PWM on",
    "valve_opening_pct": "Valve opening",
    "cover_action": "Cover action",
    "second_ac": "Second AC",
    "learning_suggestion": "Learning suggestion",
    "external_change": "External change",
    "sensors_stalled": "Stalled sensors",
    "commands_unconfirmed": "Unconfirmed commands",
    "free_cooling": "Free cooling",
    "forecast_mode": "Forecast mode",
    "forecast_target": "Forecast target",
    "fit_corrections": "Fit corrections",
    "fit_window_days": "Fit window",
    "heat_model": "Heat model",
    "dry_model": "Dry model",
    "watchdog_trips": "Watchdog trips",
    "rooms_max_age_s": "Room reading age",
    "safety_output_pct": "Safety output",
    "economizer": "Economizer",
    "motion_preset": "Motion preset",
    "shed": "Loads shed",
    "restored": "Load switched back on",
    "mpc_plan": "MPC plan steps",
    "mpc_status": "MPC plan effect",
    "mpc_heat_steps": "MPC heat plan steps",
    "solar_scale": "Solar scale",
    "vacation": "Vacation mode",
    "heat_source": "Heat source",
    "boiler_demand": "Boiler demand",
    "whole_house_fan": "Whole-house fan",
    "heating_failure": "Heating failure",
    "house_cooling": "House cooling",
    "heater_pid_output": "Heater PID output",
    "heater_output_pct": "Heater output",
    "tpi_output_pct": "TPI output",
    "preheat": "Preheat",
    "heat_up_min": "Heat-up time",
    "cool_on_above_c": "Cool above",
    "heat_on_below_c": "Heat below",
    "backtest_runs": "Backtest runs",
    "trv_offset_k": "TRV offset",
    "room_fit": "Two-device room fit",
    "forecast_days": "Forecast day counts",
    "dispatch": "Room dispatch",
    "pending": "Pending action",
    "ac_setpoint_sent_c": "Setpoint sent",
    "ac_sensor_offset_k": "Sensor offset",
    "minutes_low": "Minutes to setpoint, low",
    "minutes_high": "Minutes to setpoint, high",
    "outdoor_forecast_used": "Outdoor forecast used",
    "outdoor_forecast_next_c": "Outdoor forecast ahead",
    "outdoor_source": "Outdoor source",
    "solar_factor": "Solar cloud factor",
    "outdoor_bias_now_k": "Outdoor forecast bias",
    "outdoor_bias_pairs": "Outdoor bias pairs",
}

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
        "sensors_stalled", "commands_unconfirmed", "watchdog_trips", "heating_failure", "window_advice", "mould_risk", "dispatch", "precool_status", "mpc_status",
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
        "economizer", "free_cooling", "fit_corrections", "fit_window_days", "heat_model", "dry_model", "forecast_target", "motion_preset", "price_used_per_kwh", "cover_action", "preheat",
    }
)
STATUS_UNITS = {
    "target_c": "°C",
    "cool_on_above_c": "°C",
    "heat_on_below_c": "°C",
    "compliance_pct": "%",
    "safety_output_pct": "%",
    "valve_opening_pct": "%",
    "heater_pid_output": "%",
    "heater_output_pct": "%",
    "tpi_output_pct": "%",
    "heat_up_min": "min",
    "fit_window_days": "d",
    "minutes_low": "min",
    "minutes_high": "min",
    "rooms_max_age_s": "s",
    "solar_gain_kw": "kW",
    "trv_offset_k": "K",
    "ac_setpoint_sent_c": "°C",
    "ac_sensor_offset_k": "K",
    "outdoor_bias_now_k": "K",
    "price_used_per_kwh": None,
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: HorizonCoordinator = entry.runtime_data
    async_add_entities(
        [
            HorizonSensor(coordinator, entry, "minutes_to_setpoint", "Minutes to setpoint", "min", SensorStateClass.MEASUREMENT),
            HorizonSensor(coordinator, entry, "energy_kwh", "Energy to setpoint", "kWh", SensorStateClass.MEASUREMENT),
            HorizonSensor(coordinator, entry, "cost", "Cost to setpoint", None, SensorStateClass.MEASUREMENT),
            HorizonSensor(coordinator, entry, "status", "Status", None, None),
            HorizonSensor(coordinator, entry, "samples", "Samples collected", None, SensorStateClass.MEASUREMENT),
            HorizonSensor(coordinator, entry, "forecast_error_min", "Forecast error", "min", SensorStateClass.MEASUREMENT),
        ]
        + [HorizonStatusSensor(coordinator, entry, key, name) for key, name in STATUS_KEYS.items()]
    )


def _set_category(entity: SensorEntity, key: str) -> None:
    """Mark every sensor outside PRIMARY_KEYS diagnostic, and the internal ones disabled by default."""
    if key in PRIMARY_KEYS:
        return
    entity._attr_entity_category = EntityCategory.DIAGNOSTIC
    entity._attr_entity_registry_enabled_default = key not in DIAGNOSTIC_DISABLED_KEYS


class HorizonSensor(CoordinatorEntity[HorizonCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, key, name, unit, state_class) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = state_class
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": f"HVAC Horizon {entry.title}"}
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

    def __init__(self, coordinator, entry, key, name) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_native_unit_of_measurement = STATUS_UNITS.get(key)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": f"HVAC Horizon {entry.title}"}
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
