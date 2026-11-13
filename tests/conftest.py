"""Fixtures for IneTrack tests.

The fake API responses mirror the structure the live API returned for two
Teltonika trackers (values made up).
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntry
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.inetrack.api import InetrackApiError, TrackerEvent
from custom_components.inetrack.const import CONF_TRACKERS, DOMAIN

pytest_plugins = "pytest_homeassistant_custom_component"

TRACKER_A = 100001  # parked at a POI
TRACKER_B = 100002  # driving
API_KEY = "test-api-key-0123456789abcdef012"


def now_ms(offset_s: float = 0) -> int:
    """Epoch milliseconds, like the API uses."""
    return int((time.time() + offset_s) * 1000)


def report(tracker_id: int, *, speed: float = 0, age_s: float = 20, poi: str | None = None):
    """A ReportWrapper as returned by GetLastReports(REVERSE_GEOCODE)."""
    created = now_ms(-age_s)
    pois = []
    if poi:
        pois = [
            {
                "descriptorName": "poiDescriptor",
                "longValues": {"id": 501},
                "stringValues": {"name": poi},
                "id": 501,
            }
        ]
    body = {
        "deviceId": tracker_id,
        "creationTimestamp": created,
        "storageTimestamp": created * 1_000_000 + 3_000_000_000,
        "lat": 47.4979,
        "lng": 19.0402,
        "alt": 133,
        "heading": 335.5,
        "speed": speed,
        "accuracy": 7,
    }
    address = {"displayAddress": "Budapest, Fő utca 1", "city": "Budapest", "countryCode": "hu"}
    return {
        "trackerId": tracker_id,
        "reportOrNull": body,
        "report": body,
        "poiOrAddress": {"lat": 47.4979, "lng": 19.0402, "pois": pois, "address": address},
        "addressOrNull": address,
    }


def reports(speed_b: float = 42) -> list[dict[str, Any]]:
    """Reports of both trackers."""
    return [report(TRACKER_A, poi="Autószerelő műhely"), report(TRACKER_B, speed=speed_b)]


def events() -> list[TrackerEvent]:
    """Newest ignition / connection events (A: off + connected, B: on + disconnected)."""

    def event(tracker_id: int, name: str, age_s: float) -> TrackerEvent:
        stamp = datetime.fromtimestamp(time.time() - age_s, tz=UTC)
        return TrackerEvent(tracker_id=tracker_id, name=name, timestamp=stamp)

    return [
        event(TRACKER_A, "ACC_On", 3600),
        event(TRACKER_A, "ACC_Off", 1800),
        event(TRACKER_A, "connected", 600),
        event(TRACKER_A, "disconnected", 86400),
        event(TRACKER_B, "ACC_Off", 3600),
        event(TRACKER_B, "ACC_On", 600),
        event(TRACKER_B, "connected", 900),
        event(TRACKER_B, "trackerKicked", 60),
    ]


def status_data() -> list[dict[str, Any]]:
    """GetLastOneStatusData statusDatas."""
    return [
        {
            "statusData": {
                "deviceId": TRACKER_A,
                "customName": "gsmSignalStrength",
                "value": 35.48,
            },
            "name": "gsmSignalStrength",
            "value": 35.48,
            "trackerId": TRACKER_A,
            "creationTimestamp": now_ms(-20),
        },
        {
            "statusData": {"deviceId": TRACKER_B, "customName": "gsmSignalStrength", "value": 100},
            "name": "gsmSignalStrength",
            "value": 100,
            "trackerId": TRACKER_B,
            "creationTimestamp": now_ms(-20),
        },
    ]


EXTENDED: dict[int, dict[str, str]] = {
    TRACKER_A: {
        "externalVoltage": "12059",
        "batteryVoltage": "4045",
        "virtualOdometer": "75607609",
        "satNum": "8",
        "digIn2": "0",
        "gpsSpeed": "0",
        "ioKeyValue": "{{{{",
        "iButtonId": "0",
    },
    TRACKER_B: {
        "powerSupplyVoltage": "12506",
        "batteryVoltage": "4043",
        "totalOdometer": "67445538",
        "tripOdometer": "980",
        "ignition": "1",
        "simIccidNumberPart1": "8900000001",
        "sleepMode": "0",
    },
}


def extended_rows(tracker_id: int, age_s: float = 20, **overrides: str) -> list[dict[str, Any]]:
    """GetExtendedDataList extDataList (two points per value, newest last)."""
    values = {**EXTENDED[tracker_id], **overrides}
    rows = []
    for offset in (age_s + 60, age_s):
        for name, value in values.items():
            rows.append(
                {
                    "deviceId": tracker_id,
                    "creationTimestamp": now_ms(-offset),
                    "dataName": name,
                    "dataValue": value if offset == age_s else "1",
                    "trackerId": tracker_id,
                }
            )
    return rows


def find_device(hass: HomeAssistant, entry: MockConfigEntry, tracker_id: int) -> DeviceEntry | None:
    """The device of a tracker (works on old and new device registry APIs)."""
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id):
        if (DOMAIN, str(tracker_id)) in device.identifiers:
            return device
    return None


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A configured IneTrack entry with two trackers."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="IneTrack",
        unique_id="key-0123456789abcdef",
        data={CONF_API_KEY: API_KEY},
        options={CONF_TRACKERS: {str(TRACKER_A): "Mazda3-new", str(TRACKER_B): "Mazda6"}},
    )


@pytest.fixture
def mock_client() -> Generator[MagicMock]:
    """Patch the API client everywhere it is created."""
    client = MagicMock()
    client.list_devices = AsyncMock(
        side_effect=InetrackApiError("ListUserDevices", "Internal server error", status=500)
    )
    client.get_last_reports = AsyncMock(side_effect=lambda ids, *_: [
        r for r in reports() if r["trackerId"] in ids
    ])  # fmt: skip
    client.get_last_events = AsyncMock(return_value=events())
    client.get_last_status_data = AsyncMock(return_value=status_data())
    client.get_extended_data = AsyncMock(
        side_effect=lambda tracker_id, *_args, **_kw: extended_rows(tracker_id)
    )
    with (
        patch("custom_components.inetrack.InetrackApiClient", return_value=client),
        patch("custom_components.inetrack.config_flow.InetrackApiClient", return_value=client),
    ):
        yield client
