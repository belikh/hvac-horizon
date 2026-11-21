"""One climate entity per room. It sets the target, the preset and the on or off hold on the coordinator.

The entity never drives a device. The coordinator applies each change on its next tick through the control,
lock, inhibit and minimum-time gates.

Target temperature and preset: the temperature shown is the effective target the coordinator planned on its last
tick. It is the cool threshold while the mode is cool or off, and the heat target while the mode is heat. Both
follow the preset, so eco shows its own cooling setpoint. Setting a temperature stores the user's comfort target,
the base the presets work from. It leaves the preset unchanged. Under eco the shown value stays at the eco
setpoint until the preset changes.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_AC_HEAT, CONF_CLIMATE, CONF_CONTROL, CONF_HEATER_SWITCH, DOMAIN
from .coordinator import HorizonCoordinator

PRESETS = ["comfort", "eco", "boost", "away"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([HorizonClimate(entry.runtime_data, entry)])


class HorizonClimate(CoordinatorEntity[HorizonCoordinator], ClimateEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_min_temp = 16.0
    _attr_max_temp = 32.0
    _attr_preset_modes = PRESETS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, coordinator: HorizonCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_room"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": f"HVAC Horizon {entry.title}"}
        # Heat is offered when the room has a heater switch. An AC that heats by itself reports heat too, so it is listed as well.
        self._has_heat = bool(entry.data.get(CONF_HEATER_SWITCH)) or bool(entry.data.get(CONF_AC_HEAT, False))
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.COOL] + ([HVACMode.HEAT] if self._has_heat else [])

    @property
    def current_temperature(self) -> float | None:
        return self.coordinator.median_room_c()

    @property
    def target_temperature(self) -> float:
        return self.coordinator.effective_target_c(self.hvac_mode == HVACMode.HEAT)

    @property
    def preset_mode(self) -> str | None:
        return self.coordinator.preset_now

    @property
    def hvac_mode(self) -> HVACMode:
        if not self._entry.data.get(CONF_CONTROL) or self.coordinator.user_off:
            return HVACMode.OFF
        state = self.hass.states.get(self._entry.data[CONF_CLIMATE])
        if state is not None and state.state == "cool":
            return HVACMode.COOL
        if self._has_heat and ((state is not None and state.state == "heat") or (self.coordinator.data or {}).get("heater_pwm_on")):
            return HVACMode.HEAT
        return HVACMode.OFF

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        self.coordinator.set_user_target(temperature)
        await self.coordinator.async_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        # Off holds the room off. Cool and heat return it to automatic control, which starts the AC only when the room needs it.
        self.coordinator.set_user_off(hvac_mode == HVACMode.OFF)
        await self.coordinator.async_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        self.coordinator.set_manual_preset(preset_mode)
        await self.coordinator.async_refresh()
