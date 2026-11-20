"""Config flow: pick the AC, its power meter, the room and outdoor sensors, and the price sensor."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import selector

from .const import CONF_CLIMATE, CONF_OUTDOOR, CONF_POWER, CONF_PRICE, CONF_ROOM_SENSORS, DOMAIN


class HorizonConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_CLIMATE])
            self._abort_if_unique_id_configured()
            climate = self.hass.states.get(user_input[CONF_CLIMATE])
            title = climate.attributes.get("friendly_name", user_input[CONF_CLIMATE]) if climate else user_input[CONF_CLIMATE]
            return self.async_create_entry(title=title, data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_CLIMATE): selector.EntitySelector(selector.EntitySelectorConfig(domain="climate")),
                vol.Required(CONF_POWER): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="power")
                ),
                vol.Required(CONF_ROOM_SENSORS): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="temperature", multiple=True)
                ),
                vol.Required(CONF_OUTDOOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
                ),
                vol.Optional(CONF_PRICE): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)
