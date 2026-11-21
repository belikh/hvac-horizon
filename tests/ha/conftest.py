"""Set up the climate integration before each test.

The entry's climate platform loads the climate integration, which registers the real climate services. Tests
that stand in for the AC register their own climate services first. Loading the integration here, before a
test registers its fakes, keeps the fakes in place.
"""

import time

import pytest
from homeassistant.core import StateMachine
from homeassistant.setup import async_setup_component


@pytest.fixture(autouse=True)
async def _climate_integration_loaded(hass):
    assert await async_setup_component(hass, "climate", {})


@pytest.fixture(autouse=True)
def _devices_set_by_tests_are_old(hass, monkeypatch):
    """Give a climate or switch state set by a test a last_changed one hour in the past.

    The coordinator holds a device inside its minimum on and off time from the entity's last_changed.
    A test that sets the AC and then expects a command at once models a device that has been in that state
    for a while. A test that needs a recent change passes `timestamp` to async_set.
    """
    original = StateMachine.async_set

    def async_set(self, entity_id, new_state, attributes=None, force_update=False, context=None, state_info=None, timestamp=None):
        if timestamp is None and entity_id.split(".", 1)[0] in ("climate", "switch"):
            timestamp = time.time() - 3600
        return original(self, entity_id, new_state, attributes, force_update, context, state_info, timestamp)

    monkeypatch.setattr(StateMachine, "async_set", async_set)
