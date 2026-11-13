"""Binary sensors for IneTrack trackers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    EVENT_CONNECTED,
    EVENT_IGNITION_OFF,
    EVENT_IGNITION_ON,
    EVENTS_DISCONNECTED,
    MOVING_MAX_REPORT_AGE,
    MOVING_SPEED_THRESHOLD,
)
from .coordinator import InetrackConfigEntry, InetrackCoordinator, InetrackDeviceData
from .entity import InetrackEntity, async_add_entities_when_seen

PARALLEL_UPDATES = 0


def _is_moving(data: InetrackDeviceData) -> bool | None:
    position = data.position
    if position is None or position.speed is None:
        return None
    if position.timestamp and dt_util.utcnow() - position.timestamp > MOVING_MAX_REPORT_AGE:
        return False
    return position.speed > MOVING_SPEED_THRESHOLD


@dataclass(frozen=True, kw_only=True)
class InetrackBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes an IneTrack binary sensor."""

    value_fn: Callable[[InetrackDeviceData], bool | None]
    changed_fn: Callable[[InetrackDeviceData], datetime | None] = lambda _: None
    exists_fn: Callable[[InetrackDeviceData], bool] = lambda _: True


BINARY_SENSORS: tuple[InetrackBinarySensorEntityDescription, ...] = (
    InetrackBinarySensorEntityDescription(
        key="moving",
        translation_key="moving",
        device_class=BinarySensorDeviceClass.MOVING,
        value_fn=_is_moving,
    ),
    InetrackBinarySensorEntityDescription(
        key="ignition",
        translation_key="ignition",
        value_fn=lambda data: data.ignition,
        changed_fn=lambda data: data.event_time(EVENT_IGNITION_ON, EVENT_IGNITION_OFF),
        exists_fn=lambda data: data.ignition is not None,
    ),
    InetrackBinarySensorEntityDescription(
        key="connected",
        translation_key="connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.connected,
        changed_fn=lambda data: data.event_time(EVENT_CONNECTED, *EVENTS_DISCONNECTED),
        exists_fn=lambda data: data.connected is not None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: InetrackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up IneTrack binary sensors."""

    def build(
        coordinator: InetrackCoordinator, tracker_id: int, data: InetrackDeviceData, known: set[str]
    ) -> list[Entity]:
        entities: list[Entity] = []
        for description in BINARY_SENSORS:
            key = f"{tracker_id}_{description.key}"
            if key not in known and description.exists_fn(data):
                known.add(key)
                entities.append(InetrackBinarySensor(coordinator, tracker_id, description))
        return entities

    async_add_entities_when_seen(entry, async_add_entities, build)


class InetrackBinarySensor(InetrackEntity, BinarySensorEntity):
    """An IneTrack binary sensor."""

    entity_description: InetrackBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: InetrackCoordinator,
        tracker_id: int,
        description: InetrackBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, tracker_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the state."""
        data = self.device_data
        return self.entity_description.value_fn(data) if data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return when the tracker reported the change."""
        data = self.device_data
        changed = self.entity_description.changed_fn(data) if data else None
        return {"reported_at": changed.isoformat()} if changed else {}
