"""Config flow: pick the AC, its power meter, the room and outdoor sensors, and the optional price,
humidity and energy sensors. The reconfigure step edits the same fields without deleting samples."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_SCHEDULE,
    CONF_AWAY_C,
    CONF_COMFORT_C,
    CONF_CONTROL,
    CONF_PRESENCE,
    CONF_WINDOW,
    CONF_WINDOW_DELAY_MIN,
    DEFAULT_AWAY_C,
    DEFAULT_COMFORT_C,
    DEFAULT_WINDOW_DELAY_MIN,
    CONF_CLIMATE,
    CONF_ENERGY,
    CONF_HUMIDITY,
    CONF_OUTDOOR,
    CONF_POWER,
    CONF_PRICE,
    CONF_ROOM_SENSORS,
    DOMAIN,
)


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    def key(name: str, required: bool = False):
        marker = vol.Required if required else vol.Optional
        if name in defaults and defaults[name]:
            return marker(name, description={"suggested_value": defaults[name]})
        return marker(name)

    return vol.Schema(
        {
            key(CONF_CLIMATE, True): selector.EntitySelector(selector.EntitySelectorConfig(domain="climate")),
            key(CONF_POWER, True): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="power")
            ),
            key(CONF_ROOM_SENSORS, True): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="temperature", multiple=True)
            ),
            key(CONF_OUTDOOR, True): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
            ),
            key(CONF_PRICE): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            key(CONF_HUMIDITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="humidity")
            ),
            key(CONF_ENERGY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="energy")
            ),
            key(CONF_WINDOW): selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor")),
            key(CONF_SCHEDULE): selector.EntitySelector(selector.EntitySelectorConfig(domain=["schedule", "sensor", "input_datetime"])),
            key(CONF_PRESENCE): selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor")),
            vol.Optional(CONF_COMFORT_C, default=defaults.get(CONF_COMFORT_C, DEFAULT_COMFORT_C)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=16, max=30, step=0.5, unit_of_measurement="°C")
            ),
            vol.Optional(CONF_AWAY_C, default=defaults.get(CONF_AWAY_C, DEFAULT_AWAY_C)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=16, max=32, step=0.5, unit_of_measurement="°C")
            ),
            vol.Optional(CONF_WINDOW_DELAY_MIN, default=defaults.get(CONF_WINDOW_DELAY_MIN, DEFAULT_WINDOW_DELAY_MIN)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=120, step=1, unit_of_measurement="min")
            ),
            vol.Optional(CONF_CONTROL, default=defaults.get(CONF_CONTROL, False)): selector.BooleanSelector(),
        }
    )


class HorizonConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_CLIMATE])
            self._abort_if_unique_id_configured()
            climate = self.hass.states.get(user_input[CONF_CLIMATE])
            title = climate.attributes.get("friendly_name", user_input[CONF_CLIMATE]) if climate else user_input[CONF_CLIMATE]
            return self.async_create_entry(title=title, data=user_input)
        return self.async_show_form(step_id="user", data_schema=_schema({}))

    async def async_step_reconfigure(self, user_input: dict | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            return self.async_update_reload_and_abort(entry, data_updates=user_input)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_schema(dict(entry.data)),
        )
