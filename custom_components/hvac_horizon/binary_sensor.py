"""Binary sensors: the published values that are on or off."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HorizonCoordinator
from .coordinator_common import room_device_info
from .sensor import _set_category

# Each key is a bool in coordinator.data. The device class sets the words and the icon Home Assistant shows
# (Problem and OK, Running and Not running, Hot and Normal). A key with no class reads On and Off.
BINARY_KEYS: dict[str, BinarySensorDeviceClass | None] = {
    "drying_needed": None,
    "mould_risk": BinarySensorDeviceClass.PROBLEM,
    "heatwave": BinarySensorDeviceClass.HEAT,
    "heating_allowed": None,
    "heater_pwm_on": BinarySensorDeviceClass.RUNNING,
    "external_change": None,
    "vacation": None,
    "boiler_demand": None,
    "whole_house_fan": BinarySensorDeviceClass.RUNNING,
    "heating_failure": BinarySensorDeviceClass.PROBLEM,
    "house_cooling": None,
    "outdoor_forecast_used": None,
}

# Read-only entities of a coordinator: no limit on parallel updates.
PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([HorizonBinarySensor(entry.runtime_data, entry, key) for key in BINARY_KEYS])


class HorizonBinarySensor(CoordinatorEntity[HorizonCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: HorizonCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_translation_key = key
        self._attr_device_class = BINARY_KEYS[key]
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = room_device_info(coordinator.hass, entry)
        _set_category(self, key)

    @property
    def is_on(self) -> bool | None:
        """None reads as unknown: the value is not computed yet, or its inputs are missing."""
        value = (self.coordinator.data or {}).get(self._key)
        return value if isinstance(value, bool) else None
