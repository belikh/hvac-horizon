"""Accuracy panel: authenticated JSON views of the backtest pairs, and the custom panel module that reads them.

The panel is a web component that Home Assistant loads into its own frontend. It calls the views through
hass.callApi, so it never handles a token itself. The static path serves the module only: one JavaScript file
with no data in it."""

from __future__ import annotations

from pathlib import Path

from aiohttp import web
from homeassistant.components.frontend import async_register_built_in_panel, async_remove_panel
from homeassistant.components.http import HomeAssistantView, StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .const import ACCURACY_PANEL_PATH, CONF_ACCURACY_PANEL, DOMAIN

ACCURACY_URL = "/api/hvac_horizon/accuracy/{entry_id}"
PAGE_URL = "/hvac_horizon"  # the panel module is served at /hvac_horizon/accuracy-panel.js
PANEL_ELEMENT = "hvac-horizon-accuracy"  # the custom element the module defines
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
                "range_coverage_pct": data.get("range_coverage_pct"),
                "range_runs": data.get("range_runs") or 0,
            }
        )


class HorizonEntriesView(HomeAssistantView):
    """GET /api/hvac_horizon/entries: the loaded entries as [{entry_id, title}]. Needs a signed-in user."""

    url = "/api/hvac_horizon/entries"
    name = "api:hvac_horizon:entries"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        return self.json(
            [{"entry_id": e.entry_id, "title": e.title} for e in _loaded_entries(hass)]
        )


def _loaded_entries(hass: HomeAssistant, exclude_entry_id: str | None = None):
    """HVAC Horizon entries that are loaded or still setting up, in the order Home Assistant lists them."""
    return [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.state in (ConfigEntryState.LOADED, ConfigEntryState.SETUP_IN_PROGRESS) and e.entry_id != exclude_entry_id
    ]


def sync_accuracy_panel(hass: HomeAssistant, exclude_entry_id: str | None = None) -> None:
    """Register the sidebar panel for the entries with accuracy_panel on, or remove it when none has it on.

    Runs at setup and unload. Registration replaces the panel's config, so a second entry changes it to the list.
    """
    flagged = [e.entry_id for e in _loaded_entries(hass, exclude_entry_id) if e.data.get(CONF_ACCURACY_PANEL, False)]
    if not flagged:
        async_remove_panel(hass, ACCURACY_PANEL_PATH, warn_if_unknown=False)
        return
    # A "custom" panel is Home Assistant's own loader for a web component. It passes `hass` and this config to the element.
    async_register_built_in_panel(
        hass,
        "custom",
        sidebar_title="HVAC Accuracy",
        sidebar_icon="mdi:chart-line",
        frontend_url_path=ACCURACY_PANEL_PATH,
        require_admin=False,
        config={
            "_panel_custom": {
                "name": PANEL_ELEMENT,
                "embed_iframe": False,
                "trust_external": False,
                "module_url": f"{PAGE_URL}/accuracy-panel.js",
            },
            "entry_ids": flagged,
        },
        update=True,
    )


async def async_register_accuracy_page(hass: HomeAssistant) -> None:
    """Register the view and the static page once per Home Assistant instance."""
    store = hass.data.setdefault(DOMAIN, {})
    if store.get(REGISTERED_KEY):
        return
    hass.http.register_view(HorizonAccuracyView())
    hass.http.register_view(HorizonEntriesView())
    await hass.http.async_register_static_paths(
        [StaticPathConfig(PAGE_URL, str(Path(__file__).parent / "frontend"), cache_headers=False)]
    )
    store[REGISTERED_KEY] = True
