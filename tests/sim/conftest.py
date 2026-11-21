"""Load the climate integration before a test registers its fake climate services.

The entry's climate platform loads the climate integration, which registers the real services. Loading it first
keeps the fakes the harness registers afterwards in place.
"""

import pytest
from homeassistant.setup import async_setup_component


@pytest.fixture(autouse=True)
async def _climate_integration_loaded(hass, enable_custom_integrations):
    assert await async_setup_component(hass, "climate", {})
