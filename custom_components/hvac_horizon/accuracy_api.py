"""Accuracy page: an authenticated JSON view of the backtest pairs, and the static page that reads it."""

from __future__ import annotations

from pathlib import Path

from aiohttp import web
from homeassistant.components.http import HomeAssistantView, StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

ACCURACY_URL = "/api/hvac_horizon/accuracy/{entry_id}"
PAGE_URL = "/hvac_horizon"  # the page is served at /hvac_horizon/accuracy.html
REGISTERED_KEY = "accuracy_page_registered"  # set in hass.data[DOMAIN] once the view and page are registered


class HorizonAccuracyView(HomeAssistantView):
    """GET /api/hvac_horizon/accuracy/{entry_id}: the last backtest pairs and their median absolute error, in minutes.

    Needs a signed-in user. Returns 404 for an entry that is not loaded.
    """

    url = ACCURACY_URL
    name = "api:hvac_horizon:accuracy"
    requires_auth = True

    async def get(self, request: web.Request, entry_id: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or not hasattr(entry, "runtime_data"):
            return self.json_message("unknown HVAC Horizon entry", status_code=404)
        data = entry.runtime_data.data or {}
        return self.json(
            {
                "entry_id": entry_id,
                "median_abs_error_min": data.get("forecast_error_min"),
                "accuracy_pairs": data.get("accuracy_pairs") or [],
            }
        )


async def async_register_accuracy_page(hass: HomeAssistant) -> None:
    """Register the view and the static page once per Home Assistant instance."""
    store = hass.data.setdefault(DOMAIN, {})
    if store.get(REGISTERED_KEY):
        return
    hass.http.register_view(HorizonAccuracyView())
    await hass.http.async_register_static_paths(
        [StaticPathConfig(PAGE_URL, str(Path(__file__).parent / "frontend"), cache_headers=False)]
    )
    store[REGISTERED_KEY] = True
