"""Sensors for IneTrack trackers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    DEGREE,
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfLength,
    UnitOfSpeed,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import slugify

from .api import Measurement
from .coordinator import InetrackConfigEntry, InetrackCoordinator, InetrackDeviceData
from .entity import InetrackEntity, async_add_entities_when_seen

PARALLEL_UPDATES = 0

type DataSource = Literal["extended", "status"]


@dataclass(frozen=True, kw_only=True)
class InetrackPositionSensorEntityDescription(SensorEntityDescription):
    """A sensor computed from the last position report."""

    value_fn: Callable[[InetrackDeviceData], Any]
    exists_fn: Callable[[InetrackCoordinator], bool] = lambda _: True


@dataclass(frozen=True, kw_only=True)
class InetrackDataSensorEntityDescription(SensorEntityDescription):
    """A sensor for a known extended data / status data value."""

    source: DataSource
    data_names: tuple[str, ...]


def _position_value(attribute: str) -> Callable[[InetrackDeviceData], Any]:
    def _value(data: InetrackDeviceData) -> Any:
        return getattr(data.position, attribute) if data.position else None

    return _value


POSITION_SENSORS: tuple[InetrackPositionSensorEntityDescription, ...] = (
    InetrackPositionSensorEntityDescription(
        key="speed",
        translation_key="speed",
        device_class=SensorDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_position_value("speed"),
    ),
    InetrackPositionSensorEntityDescription(
        key="heading",
        translation_key="heading",
        native_unit_of_measurement=DEGREE,
        suggested_display_precision=0,
        value_fn=_position_value("heading"),
    ),
    InetrackPositionSensorEntityDescription(
        key="altitude",
        translation_key="altitude",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_registry_enabled_default=False,
        value_fn=_position_value("altitude"),
    ),
    InetrackPositionSensorEntityDescription(
        key="gps_accuracy",
        translation_key="gps_accuracy",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_position_value("accuracy"),
    ),
    InetrackPositionSensorEntityDescription(
        key="place",
        translation_key="place",
        value_fn=_position_value("place"),
    ),
    InetrackPositionSensorEntityDescription(
        key="address",
        translation_key="address",
        entity_registry_enabled_default=False,
        value_fn=_position_value("address"),
        exists_fn=lambda coordinator: coordinator.address_lookup,
    ),
    InetrackPositionSensorEntityDescription(
        key="last_report",
        translation_key="last_report",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_position_value("timestamp"),
    ),
)

# Raw value names as sent by the trackers (Teltonika devices: millivolts, meters).
DATA_SENSORS: tuple[InetrackDataSensorEntityDescription, ...] = (
    InetrackDataSensorEntityDescription(
        key="external_voltage",
        translation_key="external_voltage",
        source="extended",
        data_names=("externalVoltage", "powerSupplyVoltage"),
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
        suggested_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    InetrackDataSensorEntityDescription(
        key="tracker_battery_voltage",
        translation_key="tracker_battery_voltage",
        source="extended",
        data_names=("batteryVoltage",),
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
        suggested_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    InetrackDataSensorEntityDescription(
        key="odometer",
        translation_key="odometer",
        source="extended",
        data_names=("totalOdometer", "virtualOdometer"),
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        suggested_unit_of_measurement=UnitOfLength.KILOMETERS,
        suggested_display_precision=0,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    InetrackDataSensorEntityDescription(
        key="trip_distance",
        translation_key="trip_distance",
        source="extended",
        data_names=("tripOdometer",),
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        suggested_unit_of_measurement=UnitOfLength.KILOMETERS,
        suggested_display_precision=1,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    InetrackDataSensorEntityDescription(
        key="satellites",
        translation_key="satellites",
        source="extended",
        data_names=("satNum",),
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    InetrackDataSensorEntityDescription(
        key="gsm_signal",
        translation_key="gsm_signal",
        source="status",
        data_names=("gsmSignalStrength",),
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

# Raw values already shown by another entity.
_DUPLICATE_NAMES = {"ignition", "speed", "gpsSpeed"}
_KNOWN_NAMES: dict[DataSource, set[str]] = {
    "extended": {name for d in DATA_SENSORS if d.source == "extended" for name in d.data_names}
    | _DUPLICATE_NAMES,
    "status": {name for d in DATA_SENSORS if d.source == "status" for name in d.data_names},
}


def _values(data: InetrackDeviceData, source: DataSource) -> dict[str, Measurement]:
    return data.extended_data if source == "extended" else data.status_data


async def async_setup_entry(
    hass: HomeAssistant,
    entry: InetrackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up IneTrack sensors."""

    def build(
        coordinator: InetrackCoordinator, tracker_id: int, data: InetrackDeviceData, known: set[str]
    ) -> list[Entity]:
        entities: list[Entity] = []

        def add(key: str, factory: Callable[[], Entity]) -> None:
            if f"{tracker_id}_{key}" not in known:
                known.add(f"{tracker_id}_{key}")
                entities.append(factory())

        for position_description in POSITION_SENSORS:
            if position_description.exists_fn(coordinator):
                add(
                    position_description.key,
                    lambda d=position_description: InetrackPositionSensor(
                        coordinator, tracker_id, d
                    ),
                )
        for data_description in DATA_SENSORS:
            if any(
                name in _values(data, data_description.source)
                for name in data_description.data_names
            ):
                add(
                    data_description.key,
                    lambda d=data_description: InetrackDataSensor(coordinator, tracker_id, d),
                )
        for source in ("extended", "status"):
            for name in _values(data, source):
                if name not in _KNOWN_NAMES[source]:
                    add(
                        f"{source}_{slugify(name)}",
                        lambda s=source, n=name: InetrackRawSensor(coordinator, tracker_id, s, n),
                    )
        return entities

    async_add_entities_when_seen(entry, async_add_entities, build)


class InetrackPositionSensor(InetrackEntity, SensorEntity):
    """A value of the last position report."""

    entity_description: InetrackPositionSensorEntityDescription

    def __init__(
        self,
        coordinator: InetrackCoordinator,
        tracker_id: int,
        description: InetrackPositionSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, tracker_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the value."""
        data = self.device_data
        return self.entity_description.value_fn(data) if data else None


class InetrackDataSensor(InetrackEntity, SensorEntity):
    """A known extended data / status data value, with unit and device class."""

    entity_description: InetrackDataSensorEntityDescription

    def __init__(
        self,
        coordinator: InetrackCoordinator,
        tracker_id: int,
        description: InetrackDataSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, tracker_id, description.key)
        self.entity_description = description

    @property
    def _measurement(self) -> Measurement | None:
        data = self.device_data
        if data is None:
            return None
        values = _values(data, self.entity_description.source)
        found = [values[name] for name in self.entity_description.data_names if name in values]
        return found[0] if found else None

    @property
    def native_value(self) -> float | None:
        """Return the value."""
        measurement = self._measurement
        if measurement is None or not isinstance(measurement.value, float):
            return None
        return measurement.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return when the value was measured."""
        measurement = self._measurement
        if measurement is None or measurement.timestamp is None:
            return {}
        return {"measured_at": measurement.timestamp.isoformat()}


class InetrackRawSensor(InetrackEntity, SensorEntity):
    """Any other value the tracker sends, under its raw name (disabled by default)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator: InetrackCoordinator, tracker_id: int, source: DataSource, name: str
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, tracker_id, f"{source}_{slugify(name)}")
        self._source: DataSource = source
        self._data_name = name
        self._attr_name = name if source == "extended" else f"{name} (status)"

    @property
    def _measurement(self) -> Measurement | None:
        data = self.device_data
        return _values(data, self._source).get(self._data_name) if data else None

    @property
    def native_value(self) -> float | str | None:
        """Return the latest value."""
        measurement = self._measurement
        if measurement is None or measurement.value is None:
            return None
        if isinstance(measurement.value, float):
            return int(measurement.value) if measurement.value.is_integer() else measurement.value
        return str(measurement.value)[:255]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return when the value was measured."""
        measurement = self._measurement
        if measurement is None or measurement.timestamp is None:
            return {}
        return {"measured_at": measurement.timestamp.isoformat()}
