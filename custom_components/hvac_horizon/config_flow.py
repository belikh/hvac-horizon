"""Config flow: pick the AC, its power meter, the room and outdoor sensors, and the optional price,
humidity and energy sensors. The reconfigure step edits the same fields without deleting samples."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_AWAY_DELAY_MIN,
    CONF_COIL_DRY,
    CONF_HEATER_SWITCH,
    CONF_HOUSE_SEALED,
    CONF_LOCK_PIN,
    CONF_MIN_OFF_MIN,
    CONF_MIN_ON_MIN,
    CONF_PRICE_CHEAP,
    CONF_PRICE_EXPENSIVE,
    CONF_VACATION,
    CONF_VACATION_C,
    CONF_WHOLE_HOUSE_FAN,
    CONF_BOOST_C,
    CONF_HEATER_INVERTED,
    CONF_OUTPUT_MIN,
    CONF_OUTPUT_MAX,
    CONF_COVER,
    CONF_MOTION,
    CONF_SHED_LOADS,
    CONF_SHED_MAX_W,
    CONF_WHOLE_HOUSE_POWER,
    CONF_ECO_C,
    CONF_BOILER,
    CONF_DEHUMIDIFIER,
    CONF_HUMIDIFIER,
    CONF_FAN,
    CONF_CO2,
    CONF_PM25,
    CONF_SCHEDULE,
    CONF_AWAY_C,
    CONF_COMFORT_C,
    CONF_CONTROL,
    CONF_VALVE,
    CONF_PRESENCE,
    CONF_WINDOW,
    CONF_WINDOW_AZIMUTH,
    CONF_WINDOW_AREA_M2,
    CONF_IRRADIANCE,
    CONF_WINDOW_DELAY_MIN,
    DEFAULT_AWAY_C,
    DEFAULT_COMFORT_C,
    DEFAULT_WINDOW_AZIMUTH,
    DEFAULT_WINDOW_DELAY_MIN,
    CONF_CLIMATE,
    CONF_CLIMATE_2,
    CONF_ENERGY,
    CONF_HUMIDITY,
    CONF_OUTDOOR,
    CONF_POWER,
    CONF_PRICE,
    CONF_ROOM_SENSORS,
    CONF_WEATHER,
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
            key(CONF_CLIMATE_2): selector.EntitySelector(selector.EntitySelectorConfig(domain="climate")),
            key(CONF_WEATHER): selector.EntitySelector(selector.EntitySelectorConfig(domain="weather")),
            key(CONF_PRICE): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            key(CONF_HUMIDITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="humidity")
            ),
            key(CONF_ENERGY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="energy")
            ),
            key(CONF_COVER): selector.EntitySelector(selector.EntitySelectorConfig(domain="cover")),
            key(CONF_WINDOW): selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor")),
            key(CONF_CO2): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="carbon_dioxide")
            ),
            key(CONF_FAN): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
            key(CONF_DEHUMIDIFIER): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
            key(CONF_BOILER): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
            key(CONF_HUMIDIFIER): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
            key(CONF_PM25): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="pm25")
            ),
            key(CONF_SCHEDULE): selector.EntitySelector(selector.EntitySelectorConfig(domain=["schedule", "sensor", "input_datetime"])),
            key(CONF_MOTION): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor", device_class="motion")
            ),
            key(CONF_PRESENCE): selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor")),
            key(CONF_VALVE): selector.EntitySelector(selector.EntitySelectorConfig(domain="number")),
            key(CONF_WHOLE_HOUSE_POWER): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="power")
            ),
            key(CONF_VACATION): selector.EntitySelector(selector.EntitySelectorConfig(domain=["input_boolean", "switch"])),
            vol.Optional(CONF_VACATION_C): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=30, step=0.5, unit_of_measurement="°C")
            ),
            key(CONF_HEATER_SWITCH): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
            key(CONF_WHOLE_HOUSE_FAN): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
            vol.Optional(CONF_LOCK_PIN): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
            vol.Optional(CONF_COIL_DRY, default=defaults.get(CONF_COIL_DRY, False)): selector.BooleanSelector(),
            vol.Optional(CONF_HOUSE_SEALED, default=defaults.get(CONF_HOUSE_SEALED, False)): selector.BooleanSelector(),
            vol.Optional(CONF_PRICE_CHEAP): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=10, step=0.01, mode="box")),
            vol.Optional(CONF_PRICE_EXPENSIVE): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=10, step=0.01, mode="box")),
            vol.Optional(CONF_MIN_ON_MIN): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=120, step=1, unit_of_measurement="min")
            ),
            vol.Optional(CONF_MIN_OFF_MIN): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=120, step=1, unit_of_measurement="min")
            ),
            vol.Optional(CONF_AWAY_DELAY_MIN): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=240, step=1, unit_of_measurement="min")
            ),
            vol.Optional(CONF_SHED_MAX_W): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=50000, step=50, unit_of_measurement="W")
            ),
            vol.Optional(CONF_SHED_LOADS): selector.TextSelector(selector.TextSelectorConfig(multiple=True)),
            vol.Optional(CONF_COMFORT_C, default=defaults.get(CONF_COMFORT_C, DEFAULT_COMFORT_C)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=16, max=30, step=0.5, unit_of_measurement="°C")
            ),
            vol.Optional(CONF_ECO_C, default=defaults.get(CONF_ECO_C, DEFAULT_COMFORT_C + 1.0)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=16, max=30, step=0.5, unit_of_measurement="°C")
            ),
            vol.Optional(CONF_BOOST_C, default=defaults.get(CONF_BOOST_C, DEFAULT_COMFORT_C - 1.0)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=16, max=30, step=0.5, unit_of_measurement="°C")
            ),
            vol.Optional(CONF_AWAY_C, default=defaults.get(CONF_AWAY_C, DEFAULT_AWAY_C)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=16, max=32, step=0.5, unit_of_measurement="°C")
            ),
            vol.Optional(CONF_WINDOW_DELAY_MIN, default=defaults.get(CONF_WINDOW_DELAY_MIN, DEFAULT_WINDOW_DELAY_MIN)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=120, step=1, unit_of_measurement="min")
            ),
            vol.Optional(CONF_WINDOW_AZIMUTH, default=defaults.get(CONF_WINDOW_AZIMUTH, DEFAULT_WINDOW_AZIMUTH)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=360, step=1, unit_of_measurement="°")
            ),
            vol.Optional(CONF_WINDOW_AREA_M2): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=50, step=0.1, unit_of_measurement="m²")
            ),
            vol.Optional(CONF_IRRADIANCE): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="irradiance")
            ),
            vol.Optional(CONF_CONTROL, default=defaults.get(CONF_CONTROL, False)): selector.BooleanSelector(),
            vol.Optional(CONF_HEATER_INVERTED, default=False): selector.BooleanSelector(),
            vol.Optional(CONF_OUTPUT_MIN): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=100, step=1, unit_of_measurement="%")
            ),
            vol.Optional(CONF_OUTPUT_MAX): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=100, step=1, unit_of_measurement="%")
            ),
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
