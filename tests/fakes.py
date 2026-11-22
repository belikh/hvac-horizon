"""Fake room devices that are real Home Assistant entities.

A test that writes a device's state with `hass.states.async_set` skips everything Home Assistant does for a real
device. These fakes are `ClimateEntity` and `SwitchEntity` objects added to the real `climate` and `switch`
integrations, so every command the integration sends goes through Home Assistant's own services:

- A mode the AC does not list is refused with ServiceValidationError, as on a real AC.
- A setpoint outside `min_temp` to `max_temp` is refused the same way.
- The state carries the attributes a real AC has: `hvac_modes`, `min_temp`, `max_temp`, `target_temp_step`,
  `current_temperature`, `hvac_action`, `fan_modes`, `swing_modes` and `supported_features`.
- `last_changed` is the time of the command.

Each device records the commands it received in `calls`. A device can fail in the ways real ones do:

    available = False       the entity reads `unavailable`, and a command to it does nothing
    ignore_commands = True  a command is accepted and the device does not change (a lost radio packet)
    fail_commands = True    a command raises HomeAssistantError (the device's integration lost its connection)

A FakeAc holds its own state, or wraps a `unit`: a simulated AC from tests/sim/house.py. With a unit, the mode and
the setpoint are the unit's, and `hvac_action` and `current_temperature` come from its compressor and its own sensor.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.climate import DATA_COMPONENT as CLIMATE_COMPONENT
from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.components.switch import DATA_COMPONENT as SWITCH_COMPONENT
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component

ALL_MODES = ("off", "cool", "heat", "dry", "fan_only")
Listener = Callable[[str, str, Any], None]  # entity id, service, value

_ACTION = {"cool": HVACAction.COOLING, "heat": HVACAction.HEATING, "dry": HVACAction.DRYING}


class _Faults:
    available_now = True
    ignore_commands = False
    fail_commands = False

    def _accept(self, service: str, value: Any) -> bool:
        """Record a command. True when the device acts on it."""
        if self.fail_commands:
            raise HomeAssistantError(f"{self.entity_id}: no response from the device")
        self.calls.append((service, value))
        if self.listener is not None:
            self.listener(self.entity_id, service, value)
        return not self.ignore_commands


class FakeAc(_Faults, ClimateEntity):
    """A split AC. `modes` are the modes the unit has, so a cool-only unit refuses heat."""

    _attr_should_poll = False
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        entity_id: str,
        *,
        name: str = "Fake AC",
        modes: tuple[str, ...] = ALL_MODES,
        mode: str = "off",
        setpoint_c: float = 24.0,
        min_c: float = 16.0,
        max_c: float = 30.0,
        step_c: float = 1.0,
        fan_modes: tuple[str, ...] = (),
        swing_modes: tuple[str, ...] = (),
        reports_action: bool = True,
        reports_temperature: bool = True,
        unit: Any = None,
        listener: Listener | None = None,
    ) -> None:
        self.entity_id = entity_id
        self._attr_name = name
        self._attr_hvac_modes = [HVACMode(m) for m in modes]
        self._attr_min_temp = min_c
        self._attr_max_temp = max_c
        self._attr_target_temperature_step = step_c
        self._attr_fan_modes = list(fan_modes) or None
        self._attr_fan_mode = fan_modes[0] if fan_modes else None
        self._attr_swing_modes = list(swing_modes) or None
        self._attr_swing_mode = swing_modes[0] if swing_modes else None
        features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        if fan_modes:
            features |= ClimateEntityFeature.FAN_MODE
        if swing_modes:
            features |= ClimateEntityFeature.SWING_MODE
        self._attr_supported_features = features
        self.unit = unit
        self._mode = mode
        self._setpoint_c = setpoint_c
        self.reports_action = reports_action
        self.reports_temperature = reports_temperature
        self.room_c: float | None = None  # what the unit's own sensor reads, for an AC with no simulated unit
        self.running: bool | None = None  # compressor state for an AC with no unit; None means it runs whenever the mode needs it
        self.calls: list[tuple[str, Any]] = []
        self.listener = listener

    @property
    def available(self) -> bool:
        return self.available_now

    @property
    def mode(self) -> str:
        return self.unit.mode if self.unit is not None else self._mode

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode(self.mode)

    @property
    def native_target_temperature(self) -> float:
        return self.unit.setpoint_c if self.unit is not None else self._setpoint_c

    @property
    def native_current_temperature(self) -> float | None:
        if not self.reports_temperature:
            return None
        if self.unit is not None:
            return self.unit.sensed_c
        return self.room_c

    @property
    def hvac_action(self) -> HVACAction | None:
        if not self.reports_action:
            return None
        mode = self.mode
        if mode == "off":
            return HVACAction.OFF
        if mode == "fan_only":
            return HVACAction.FAN
        running = self.unit.compressor if self.unit is not None else (True if self.running is None else self.running)
        return _ACTION[mode] if running else HVACAction.IDLE

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if not self._accept("set_hvac_mode", str(hvac_mode)):
            return
        if self.unit is not None:
            self.unit.mode = str(hvac_mode)
        else:
            self._mode = str(hvac_mode)
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if not self._accept("set_temperature", kwargs[ATTR_TEMPERATURE]):
            return
        if self.unit is not None:
            self.unit.setpoint_c = float(kwargs[ATTR_TEMPERATURE])
        else:
            self._setpoint_c = float(kwargs[ATTR_TEMPERATURE])
        self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        if self._accept("set_fan_mode", fan_mode):
            self._attr_fan_mode = fan_mode
            self.async_write_ha_state()

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        if self._accept("set_swing_mode", swing_mode):
            self._attr_swing_mode = swing_mode
            self.async_write_ha_state()

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    def press_remote(self, *, mode: str | None = None, setpoint_c: float | None = None) -> None:
        """A person changes the AC with its remote control. No service call is recorded."""
        if mode is not None:
            if self.unit is not None:
                self.unit.mode = mode
            else:
                self._mode = mode
        if setpoint_c is not None:
            if self.unit is not None:
                self.unit.setpoint_c = setpoint_c
            else:
                self._setpoint_c = setpoint_c
        self.async_write_ha_state()


class FakeSwitch(_Faults, SwitchEntity):
    """A smart plug or relay. `on_change` is called with the new state when the device acts on a command."""

    _attr_should_poll = False

    def __init__(self, entity_id: str, *, name: str = "Fake switch", on: bool = False, on_change: Callable[[bool], None] | None = None, listener: Listener | None = None) -> None:
        self.entity_id = entity_id
        self._attr_name = name
        self._on = on
        self.on_change = on_change
        self.calls: list[tuple[str, Any]] = []
        self.listener = listener

    @property
    def available(self) -> bool:
        return self.available_now

    @property
    def is_on(self) -> bool:
        return self._on

    def _apply(self, service: str, on: bool) -> None:
        if not self._accept(service, None):
            return
        self._on = on
        if self.on_change is not None:
            self.on_change(on)
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._apply("turn_on", True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._apply("turn_off", False)

    def flip_by_hand(self, on: bool) -> None:
        """A person presses the button on the plug. No service call is recorded."""
        self._on = on
        if self.on_change is not None:
            self.on_change(on)
        self.async_write_ha_state()


async def add_devices(hass: HomeAssistant, *devices: FakeAc | FakeSwitch) -> None:
    """Add the devices to the real climate and switch integrations and wait for their first state."""
    for domain, key, kind in (("climate", CLIMATE_COMPONENT, FakeAc), ("switch", SWITCH_COMPONENT, FakeSwitch)):
        wanted = [d for d in devices if isinstance(d, kind)]
        if not wanted:
            continue
        assert await async_setup_component(hass, domain, {})
        component = hass.data[key]
        for device in wanted:  # a second house in the same test replaces the devices of the first
            if (old := component.get_entity(device.entity_id)) is not None:
                await old.async_remove()
        await component.async_add_entities(wanted)
    await hass.async_block_till_done()
    for device in devices:
        assert hass.states.get(device.entity_id) is not None, device.entity_id
