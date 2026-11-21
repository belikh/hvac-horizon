"""Set up the climate integration before each test.

The entry's climate platform loads the climate integration, which registers the real climate services. Tests
that stand in for the AC register their own climate services first. Loading the integration here, before a
test registers its fakes, keeps the fakes in place.
"""

import pytest
from homeassistant.setup import async_setup_component


@pytest.fixture(autouse=True)
async def _climate_integration_loaded(hass):
    assert await async_setup_component(hass, "climate", {})
