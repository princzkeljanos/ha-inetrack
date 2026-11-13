"""Base entity for the IneTrack integration."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, WEB_APP_URL
from .coordinator import InetrackConfigEntry, InetrackCoordinator, InetrackDeviceData


class InetrackEntity(CoordinatorEntity[InetrackCoordinator]):
    """An entity that belongs to one IneTrack tracker."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: InetrackCoordinator, tracker_id: int, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.tracker_id = tracker_id
        self._attr_unique_id = f"{tracker_id}_{key}"
        tracker = coordinator.data[tracker_id].tracker
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(tracker_id))},
            name=tracker.name,
            manufacturer=MANUFACTURER,
            model=tracker.model,
            serial_number=tracker.serial,
            configuration_url=WEB_APP_URL,
        )

    @property
    def device_data(self) -> InetrackDeviceData | None:
        """Return the latest data of this tracker."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self.tracker_id)

    @property
    def available(self) -> bool:
        """Return if the tracker is still part of the account."""
        return super().available and self.device_data is not None


type EntityBuilder = Callable[
    [InetrackCoordinator, int, InetrackDeviceData, set[str]], list[Entity]
]


@callback
def async_add_entities_when_seen(
    entry: InetrackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    build: EntityBuilder,
) -> None:
    """Add entities now, and later for trackers or values that show up after setup.

    ``build`` gets the set of keys already added and must add the keys of the
    entities it returns to it.
    """
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        if not coordinator.data:
            return
        new: list[Entity] = []
        for tracker_id, data in coordinator.data.items():
            new.extend(build(coordinator, tracker_id, data, known))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))
