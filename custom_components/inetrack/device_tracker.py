"""Device tracker (map position) for IneTrack trackers."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Position
from .coordinator import InetrackConfigEntry, InetrackCoordinator, InetrackDeviceData
from .entity import InetrackEntity, async_add_entities_when_seen

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: InetrackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one device tracker per IneTrack tracker."""

    def build(
        coordinator: InetrackCoordinator, tracker_id: int, data: InetrackDeviceData, known: set[str]
    ) -> list[Entity]:
        key = f"{tracker_id}_location"
        if key in known:
            return []
        known.add(key)
        return [InetrackDeviceTracker(coordinator, tracker_id)]

    async_add_entities_when_seen(entry, async_add_entities, build)


class InetrackDeviceTracker(InetrackEntity, TrackerEntity):
    """The GPS position of a vehicle."""

    _attr_name = None
    _attr_translation_key = "location"

    def __init__(self, coordinator: InetrackCoordinator, tracker_id: int) -> None:
        """Initialize the tracker."""
        super().__init__(coordinator, tracker_id, "location")

    @property
    def _position(self) -> Position | None:
        data = self.device_data
        return data.position if data else None

    @property
    def source_type(self) -> SourceType:
        """Return the source of the location."""
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        """Return the latitude."""
        return self._position.latitude if self._position else None

    @property
    def longitude(self) -> float | None:
        """Return the longitude."""
        return self._position.longitude if self._position else None

    @property
    def location_accuracy(self) -> float:
        """Return the GPS accuracy in meters."""
        position = self._position
        if position is None or position.accuracy is None:
            return 0
        return position.accuracy

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra details of the last report."""
        position = self._position
        if position is None:
            return {}
        attributes: dict[str, Any] = {
            "speed": position.speed,
            "heading": position.heading,
            "altitude": position.altitude,
            "place": position.place,
            "last_report": position.timestamp.isoformat() if position.timestamp else None,
        }
        return {key: value for key, value in attributes.items() if value is not None}
