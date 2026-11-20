"""Accuracy page: an authenticated JSON view of the backtest pairs, and the static page that reads it."""

from __future__ import annotations

from pathlib import Path

from aiohttp import web
from homeassistant.components.frontend import async_register_built_in_panel, async_remove_panel
from homeassistant.components.http import HomeAssistantView, StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .const import ACCURACY_PANEL_PATH, CONF_ACCURACY_PANEL, DOMAIN

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


def _accuracy_panel_url(entry_ids: list[str]) -> str:
    """The page with the first entry id when one entry has the panel, or the entry list when several do."""
    page = f"{PAGE_URL}/accuracy.html"
    if len(entry_ids) == 1:
        return f"{page}?entry_id={entry_ids[0]}"
    return page


def sync_accuracy_panel(hass: HomeAssistant, exclude_entry_id: str | None = None) -> None:
    """Register the sidebar panel for the entries with accuracy_panel on, or remove it when none has it on.

    Runs at setup and unload. Registration replaces the panel's URL, so a second entry changes it to the list.
    """
    flagged = [e.entry_id for e in _loaded_entries(hass, exclude_entry_id) if e.data.get(CONF_ACCURACY_PANEL, False)]
    if not flagged:
        async_remove_panel(hass, ACCURACY_PANEL_PATH, warn_if_unknown=False)
        return
    async_register_built_in_panel(
        hass,
        "iframe",
        sidebar_title="HVAC Accuracy",
        sidebar_icon="mdi:chart-line",
        frontend_url_path=ACCURACY_PANEL_PATH,
        require_admin=False,
        config={"url": _accuracy_panel_url(flagged)},
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
