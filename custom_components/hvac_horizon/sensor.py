"""Sensors that publish the forecast from the coordinator."""

from __future__ import annotations

import json

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
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
    "heater_feedback": "Heater feedback",
    "heater_pwm_on": "Heater PWM on",
    "valve_opening_pct": "Valve opening",
    "cover_action": "Cover action",
    "second_ac": "Second AC",
    "learning_suggestion": "Learning suggestion",
    "external_change": "External change",
    "sensors_stalled": "Stalled sensors",
    "safety_output_pct": "Safety output",
    "economizer": "Economizer",
    "motion_preset": "Motion preset",
    "shed": "Loads shed",
    "restored": "Load switched back on",
    "mpc_plan": "MPC plan steps",
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
}
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
    "solar_gain_kw": "kW",
    "trv_offset_k": "K",
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

    @property
    def extra_state_attributes(self):
        if self.coordinator.data is None:
            return None
        if self._key == "forecast_error_min":
            # The last 20 backtest runs. The state is the median absolute error over every run.
            return {"accuracy_pairs": self.coordinator.data.get("accuracy_pairs")}
        if self._key != "status":
            return None
        d = self.coordinator.data
        return {
            "reason": d.get("reason"),
            "k_per_h": d.get("k"),
            "c_k_per_kw": d.get("c"),
            "g_k_per_h": d.get("g"),
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
