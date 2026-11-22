"""Peer coordination: setpoint and window-open requests between coordinators of one house and the peer valve request."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State

from .const import CONF_PEER_ENTRY, CONF_PEER_SETPOINT_MODE, DOMAIN
from custom_components.hvac_horizon.engine.features import peer_warmer

if TYPE_CHECKING:
    from custom_components.hvac_horizon.coordinator import HorizonCoordinator


class PeersMixin:
    # Instance attributes set in HorizonCoordinator.__init__ and used here.
    _peer_setpoint_original: float | None
    entry: ConfigEntry
    hass: HomeAssistant

    def set_peer_request(self, requester_id: str, active: bool) -> None:
        """Record whether the entry `requester_id` asks this entry's valve to open. Its room is at least 2 K warmer."""
        requests = self.hass.data.setdefault(DOMAIN, {}).setdefault("peer_requests", {}).setdefault(self.entry.entry_id, set())
        if active:
            requests.add(requester_id)
        else:
            requests.discard(requester_id)

    def _peer_open_requested(self) -> bool:
        return bool(self.hass.data.get(DOMAIN, {}).get("peer_requests", {}).get(self.entry.entry_id))

    def set_peer_setpoint_request(self, requester_id: str, active: bool) -> None:
        """Record whether the entry `requester_id` asks this entry to lower its cool setpoint. Its room is at least 2 K warmer."""
        requests = self.hass.data.setdefault(DOMAIN, {}).setdefault("peer_setpoint_requests", {}).setdefault(self.entry.entry_id, set())
        if active:
            requests.add(requester_id)
        else:
            requests.discard(requester_id)

    def _peer_setpoint_requested(self) -> bool:
        return bool(self.hass.data.get(DOMAIN, {}).get("peer_setpoint_requests", {}).get(self.entry.entry_id))

    def _remember_peer_setpoint(self, climate: State) -> None:
        """Keep the AC setpoint from before the first peer lowering. A later lowering keeps the first value."""
        current = climate.attributes.get("temperature")
        if self._peer_setpoint_original is None and current is not None:
            self._peer_setpoint_original = float(current)

    def _peer_coordinator(self) -> HorizonCoordinator | None:
        """The coordinator of the entry named by peer_entry (entry_id or title), or None when it is unset or not loaded."""
        ref = str(self.entry.data.get(CONF_PEER_ENTRY) or "").strip()
        if not ref:
            return None
        peer = self.hass.config_entries.async_get_entry(ref)
        if peer is None:
            peer = next((e for e in self.hass.config_entries.async_entries(DOMAIN) if e.title == ref), None)
        if peer is None or peer.entry_id == self.entry.entry_id or not hasattr(peer, "runtime_data"):
            return None
        return peer.runtime_data

    def _request_peer_valve(self) -> None:
        """Ask the peer entry to open its valve while this room is at least 2 K warmer than the peer's room.

        The peer applies the request in its own valve command, so the two entries do not write the same valve.
        A request is withdrawn when the condition ends.
        """
        peer = self._peer_coordinator()
        if peer is None:
            return
        own, other = self.median_room_c(), peer.median_room_c()
        warmer = own is not None and other is not None and peer_warmer(own, other)
        # With peer_setpoint_mode the request lowers the peer's setpoint instead. Each request kind is withdrawn when unused.
        setpoint_mode = bool(self.entry.data.get(CONF_PEER_SETPOINT_MODE, False))
        peer.set_peer_request(self.entry.entry_id, warmer and not setpoint_mode)
        peer.set_peer_setpoint_request(self.entry.entry_id, warmer and setpoint_mode)

    def forget_peer_requests(self) -> None:
        """Withdraw this entry's valve requests on unload, so a removed room cannot keep another valve open."""
        for kind in ("peer_requests", "peer_setpoint_requests"):
            for requests in self.hass.data.get(DOMAIN, {}).get(kind, {}).values():
                requests.discard(self.entry.entry_id)
