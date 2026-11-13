"""Diagnostics for the IneTrack integration (device page > Download diagnostics)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from .coordinator import InetrackConfigEntry

TO_REDACT = {
    CONF_API_KEY,
    "apikey",
    "apiKey",
    "email",
    "phoneNumber",
    "icc",
    "nativeID",
    "serial",
    "owner",
    "lat",
    "lng",
    "latitude",
    "longitude",
    "address",
    "addressOrNull",
    "displayAddress",
    "road",
    "houseNum",
    "postcode",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: InetrackConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    coordinator = entry.runtime_data
    devices = {
        str(tracker_id): {
            "tracker": asdict(data.tracker),
            "position": asdict(data.position) if data.position else None,
            "ignition": data.ignition,
            "connected": data.connected,
            "events": {
                name: {"timestamp": event.timestamp, "message": event.message}
                for name, event in data.events.items()
            },
            "status_data": {name: asdict(value) for name, value in data.status_data.items()},
            "extended_data": {name: asdict(value) for name, value in data.extended_data.items()},
        }
        for tracker_id, data in (coordinator.data or {}).items()
    }
    return async_redact_data(
        {
            "entry": {"data": dict(entry.data), "options": dict(entry.options)},
            "rejected_trackers": sorted(coordinator.rejected),
            "devices": devices,
            "raw_responses": coordinator.raw,
        },
        TO_REDACT,
    )
