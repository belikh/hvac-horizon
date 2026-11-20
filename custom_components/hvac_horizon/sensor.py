"""Sensors that publish the forecast from the coordinator."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HorizonCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: HorizonCoordinator = entry.runtime_data
    async_add_entities(
        [
            HorizonSensor(coordinator, entry, "minutes_to_setpoint", "Minutes to setpoint", "min", SensorStateClass.MEASUREMENT),
            HorizonSensor(coordinator, entry, "energy_kwh", "Energy to setpoint", "kWh", SensorStateClass.MEASUREMENT),
            HorizonSensor(coordinator, entry, "cost", "Cost to setpoint", None, SensorStateClass.MEASUREMENT),
            HorizonSensor(coordinator, entry, "status", "Status", None, None),
            HorizonSensor(coordinator, entry, "samples", "Samples collected", None, SensorStateClass.MEASUREMENT),
        ]
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
        if self._key != "status" or self.coordinator.data is None:
            return None
        d = self.coordinator.data
        return {"reason": d.get("reason"), "k_per_h": d.get("k"), "c_k_per_kw": d.get("c"), "g_k_per_h": d.get("g")}

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        key = {"minutes_to_setpoint": "minutes", "energy_kwh": "energy_kwh", "cost": "cost", "status": "status", "samples": "samples"}[self._key]
        return self.coordinator.data.get(key)
