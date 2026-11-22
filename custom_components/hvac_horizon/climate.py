"""One climate entity per room. It sets the target, the preset and the on or off hold on the coordinator.

The entity never drives a device. The coordinator applies each change on its next tick through the control,
lock, inhibit and minimum-time gates.

Target temperature and preset: the temperature shown is the target the coordinator planned on its last tick. It is
the cool target while the mode is cool or off, and the heat target while the mode is heat. Both follow the preset,
so eco shows its own cooling setpoint. Setting a temperature stores the user's comfort target and returns the room
to the comfort preset, so the value applies at once. In auto, a room that can heat shows two targets: it heats to the
low one and cools above the high one.

Fan mode, swing mode and humidity: the fan and swing modes are the first AC's own, and a change goes to that AC. The
current humidity is the room humidity sensor's reading.

hvac_mode is the user's intent: off while the hold is set or control is off, otherwise the mode the user last chose
(auto by default). Cool lets only cooling devices run, heat lets only heating devices run, auto lets both run. hvac_action is what the devices do: cooling or heating while the compressor runs (power at or above
RUNNING_MIN_KW), idle while the AC runs the fan only. With control off the mode reads off and the `control` attribute
reads "observe only".
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ATTR_TARGET_TEMP_HIGH, ATTR_TARGET_TEMP_LOW, ClimateEntity, HVACAction, ClimateEntityFeature, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_CLIMATE, CONF_CONTROL, CONF_DEHUMIDIFIER, CONF_FLEET, CONF_HEATER_SWITCH, CONF_POWER, CONF_ROOM_HUMIDITY, DOMAIN
from .coordinator import HorizonCoordinator

PRESETS = ["comfort", "eco", "boost", "away"]

# Home Assistant 2026.11 deprecates `_attr_temperature_unit`, `current_temperature` and `target_temperature` in favour of the
# native_* names. 2026.10.4 has no native_* names. The entity defines the pair its base class supports, so neither version warns.
_NATIVE_TEMPERATURE_API = hasattr(ClimateEntity, "native_temperature_unit")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([HorizonClimate(entry.runtime_data, entry)])


class HorizonClimate(CoordinatorEntity[HorizonCoordinator], ClimateEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_target_temperature_step = 0.5
    _attr_min_temp = 16.0
    _attr_max_temp = 32.0
    _attr_preset_modes = PRESETS
    _BASE_FEATURES = (
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
        # Heat is offered when the entry can heat: a heater switch, the AC or a heating fleet device.
        self._has_heat = coordinator.has_heat
        # Cool only cools, heat only heats, auto does both. A room with nothing to heat with has no heat mode.

    def _ac_attribute(self, name: str) -> Any:
        ac = self.hass.states.get(self._entry.data[CONF_CLIMATE]) if self.hass else None
        return ac.attributes.get(name) if ac else None

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """A room that can heat has a low and a high target in auto. Fan and swing modes are the first AC's, when it has them."""
        features = self._BASE_FEATURES
        if self._has_heat:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        if self._ac_attribute("fan_modes"):
            features |= ClimateEntityFeature.FAN_MODE
        if self._ac_attribute("swing_modes"):
            features |= ClimateEntityFeature.SWING_MODE
        return features

    @property
    def fan_modes(self) -> list[str] | None:
        return self._ac_attribute("fan_modes")

    @property
    def fan_mode(self) -> str | None:
        return self._ac_attribute("fan_mode")

    @property
    def swing_modes(self) -> list[str] | None:
        return self._ac_attribute("swing_modes")

    @property
    def swing_mode(self) -> str | None:
        return self._ac_attribute("swing_mode")

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self.coordinator.async_set_ac_option("fan_mode", fan_mode)

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        await self.coordinator.async_set_ac_option("swing_mode", swing_mode)

    @property
    def current_humidity(self) -> float | None:
        """The room humidity sensor's reading, when one is set."""
        sensor = self._entry.data.get(CONF_ROOM_HUMIDITY)
        state = self.hass.states.get(sensor) if sensor and self.hass else None
        try:
            return float(state.state) if state else None
        except ValueError:
            return None

    @property
    def _ranged(self) -> bool:
        """True in auto when the room can heat: the card shows the heat target and the cool target as low and high."""
        return self._has_heat and self.hvac_mode == HVACMode.AUTO

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Off, cool, auto, heat when the entry can heat, and dry when it can dehumidify."""
        modes = [HVACMode.OFF, HVACMode.COOL]
        if self._has_heat:
            modes.append(HVACMode.HEAT)
        modes.append(HVACMode.AUTO)
        if self.coordinator.can_dry:
            modes.append(HVACMode.DRY)
        return modes

    def _device_entity_ids(self) -> list[str]:
        """The entities whose state decides hvac_action: the AC, its power sensor, the heater switch and the fleet devices."""
        data = self._entry.data
        ids = [data.get(CONF_CLIMATE), data.get(CONF_POWER), data.get(CONF_HEATER_SWITCH), data.get(CONF_DEHUMIDIFIER)]
        for device in data.get(CONF_FLEET) or []:
            if isinstance(device, dict):
                ids += [device.get("entity_id"), device.get("power_sensor")]
        return [entity_id for entity_id in ids if isinstance(entity_id, str) and entity_id]

    async def async_added_to_hass(self) -> None:
        """Follow the devices between coordinator ticks, so hvac_action shows what they do now."""
        await super().async_added_to_hass()
        self.async_on_remove(async_track_state_change_event(self.hass, self._device_entity_ids(), self._device_changed))

    @callback
    def _device_changed(self, _event: Event) -> None:
        self.async_write_ha_state()

    def _room_temperature(self) -> float | None:
        return self.coordinator.median_room_c()

    def _target(self) -> float | None:
        return None if self._ranged else self.coordinator.effective_target_c(self.hvac_mode == HVACMode.HEAT)

    def _target_low(self) -> float | None:
        return self.coordinator.effective_target_c(True) if self._ranged else None

    def _target_high(self) -> float | None:
        return self.coordinator.effective_target_c(False) if self._ranged else None

    if _NATIVE_TEMPERATURE_API:
        _attr_native_temperature_unit = UnitOfTemperature.CELSIUS

        @property
        def native_current_temperature(self) -> float | None:
            return self._room_temperature()

        @property
        def native_target_temperature(self) -> float | None:
            return self._target()

        @property
        def native_target_temperature_low(self) -> float | None:
            return self._target_low()

        @property
        def native_target_temperature_high(self) -> float | None:
            return self._target_high()

    else:
        _attr_temperature_unit = UnitOfTemperature.CELSIUS

        @property
        def current_temperature(self) -> float | None:
            return self._room_temperature()

        @property
        def target_temperature(self) -> float | None:
            return self._target()

        @property
        def target_temperature_low(self) -> float | None:
            return self._target_low()

        @property
        def target_temperature_high(self) -> float | None:
            return self._target_high()

    @property
    def hvac_action(self) -> HVACAction:
        return {
            "cooling": HVACAction.COOLING,
            "heating": HVACAction.HEATING,
            "drying": HVACAction.DRYING,
            "idle": HVACAction.IDLE,
        }.get(self.coordinator.device_action(), HVACAction.OFF)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        # `pending` names a start that the restart delay holds, for example "start in 2 min: restart delay". None when nothing waits.
        attributes: dict[str, Any] = {"pending": (self.coordinator.data or {}).get("pending")}
        if not self._entry.data.get(CONF_CONTROL):
            attributes["control"] = "observe only"
        return attributes

    @property
    def preset_mode(self) -> str | None:
        return self.coordinator.preset_now

    @property
    def hvac_mode(self) -> HVACMode:
        """The user's intent: off while the user hold is set or control is off, else the mode the user last chose (auto by default)."""
        if not self._entry.data.get(CONF_CONTROL) or self.coordinator.user_off:
            return HVACMode.OFF
        return HVACMode(self.coordinator.user_mode)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        low, high = kwargs.get(ATTR_TARGET_TEMP_LOW), kwargs.get(ATTR_TARGET_TEMP_HIGH)
        if low is not None and high is not None:
            self.coordinator.set_user_target_range(low, high)
        elif (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            self.coordinator.set_user_target(temperature)
        else:
            return
        await self.coordinator.async_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        # Off holds the room off. Cool lets only cooling devices run, heat only heating devices, auto both.
        # A device starts only when the room needs it. The chosen mode is stored with the hold.
        self.coordinator.set_user_off(hvac_mode == HVACMode.OFF, None if hvac_mode == HVACMode.OFF else str(hvac_mode))
        await self.coordinator.async_refresh()

    async def async_turn_on(self) -> None:
        """End the off hold and return to the mode chosen before it. Home Assistant's default would pick heat or cool itself."""
        self.coordinator.set_user_off(False)
        await self.coordinator.async_refresh()

    async def async_turn_off(self) -> None:
        self.coordinator.set_user_off(True)
        await self.coordinator.async_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        self.coordinator.set_manual_preset(preset_mode)
        await self.coordinator.async_refresh()
