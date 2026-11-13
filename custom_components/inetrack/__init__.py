"""The IneTrack GPS tracker integration."""

from __future__ import annotations

from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import InetrackApiClient
from .const import DOMAIN
from .coordinator import InetrackConfigEntry, InetrackCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: InetrackConfigEntry) -> bool:
    """Set up IneTrack from a config entry."""
    client = InetrackApiClient(async_get_clientsession(hass), entry.data[CONF_API_KEY])
    coordinator = InetrackCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Drop devices of trackers that were removed in the options.
    device_registry = dr.async_get(hass)
    configured = {str(tracker_id) for tracker_id in coordinator.trackers}
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if not any(
            domain == DOMAIN and identifier in configured
            for domain, identifier in device.identifiers
        ):
            device_registry.async_remove_device(device.id)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: InetrackConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: InetrackConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: InetrackConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing a device that is no longer in the IneTrack account."""
    current = {str(tracker_id) for tracker_id in entry.runtime_data.trackers}
    return not any(
        domain == DOMAIN and identifier in current
        for domain, identifier in device_entry.identifiers
    )
