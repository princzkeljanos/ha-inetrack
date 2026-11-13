"""Tests for the IneTrack API client (HTTP level and parsing)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.inetrack.api import (
    InetrackApiClient,
    InetrackApiError,
    InetrackAuthError,
    InetrackConnectionError,
    Position,
    TrackerEvent,
    coerce_value,
    ms_to_datetime,
    parse_tracker_ids,
)

URL = "https://api.inetrack.com/api"


async def test_request_encoding(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Lists are comma separated, None is left out, the key is sent."""
    aioclient_mock.get(URL, json={"success": True, "repList": [{"trackerId": 1}]})
    client = InetrackApiClient(async_get_clientsession(hass), "secret")
    assert await client.get_last_reports([1, 2], "POI") == [{"trackerId": 1}]
    _, url, _, _ = aioclient_mock.mock_calls[-1]
    assert url.query["command"] == "GetLastReports"
    assert url.query["deviceIds"] == "1,2"
    assert url.query["positionType"] == "POI"
    assert url.query["apikey"] == "secret"

    aioclient_mock.clear_requests()
    aioclient_mock.get(URL, json={"success": True, "extDataList": []})
    await client.get_extended_data(5, 100, 200)
    _, url, _, _ = aioclient_mock.mock_calls[-1]
    assert url.query["deviceId"] == "5"
    assert url.query["minCreationTimestamp"] == "100"
    assert "names" not in url.query


async def test_events(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Event wrappers are parsed."""
    aioclient_mock.get(
        URL,
        json={
            "success": True,
            "deviceEvents": [
                {
                    "deviceEvent": {
                        "deviceId": 7,
                        "creationTimestamp": 1794552235000,
                        "name": "ACC_Off",
                        "message": "0",
                    },
                    "name": "ACC_Off",
                    "trackerId": 7,
                },
                {"deviceEvent": None},
            ],
        },
    )
    client = InetrackApiClient(async_get_clientsession(hass), "secret")
    events = await client.get_last_events([7], ["ACC_On", "ACC_Off"])
    assert [(e.tracker_id, e.name, e.message) for e in events] == [(7, "ACC_Off", "0")]
    _, url, _, _ = aioclient_mock.mock_calls[-1]
    assert url.query["names"] == "ACC_On,ACC_Off"


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (403, {"success": False, "message": "Forbidden"}, InetrackAuthError),
        (401, None, InetrackAuthError),
        (500, {"success": False, "message": "Internal server error"}, InetrackApiError),
        (200, {"success": False, "message": "Something", "errorId": 3}, InetrackApiError),
        (200, {"success": False, "message": "Forbidden"}, InetrackAuthError),
        (503, None, InetrackConnectionError),
    ],
)
async def test_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    body: dict | None,
    error: type[Exception],
) -> None:
    """HTTP and API errors map to the right exception."""
    aioclient_mock.get(URL, status=status, json=body)
    client = InetrackApiClient(async_get_clientsession(hass), "secret")
    with pytest.raises(error):
        await client.get_last_reports([1])


async def test_timeout(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A timeout is a connection error."""
    aioclient_mock.get(URL, exc=TimeoutError)
    client = InetrackApiClient(async_get_clientsession(hass), "secret")
    with pytest.raises(InetrackConnectionError):
        await client.get_last_reports([1])


def test_parse_tracker_ids() -> None:
    """IDs come from plain numbers or app links, without duplicates."""
    text = (
        "https://app.inetrack.com/#home/devices?deviceGroups=12345&chart=true"
        "&details=true/route?trackerId=100001&realtime=y\n100002, 100001;  12"
    )
    assert parse_tracker_ids(text) == [100001, 100002, 12]
    assert parse_tracker_ids("deviceGroups=12345") == []
    assert parse_tracker_ids("") == []


def test_timestamps() -> None:
    """Milliseconds, nanoseconds and seconds are all understood."""
    expected = "2016-05-18T11:07:23"
    assert ms_to_datetime(1463569643128).isoformat().startswith(expected)
    assert ms_to_datetime(1463569643128000000).isoformat().startswith(expected)
    assert ms_to_datetime(1463569643).isoformat().startswith(expected)
    assert ms_to_datetime(None) is None
    assert ms_to_datetime(0) is None
    assert ms_to_datetime("x") is None


def test_position_parsing() -> None:
    """POI names (plain or assisted objects) win over the address."""
    report = {"lat": 47.8, "lng": 17.8, "speed": 0, "creationTimestamp": 1794556423000}
    assisted = {"pois": [{"stringValues": {"name": "Home"}, "id": 1}], "address": None}
    position = Position.from_report_wrapper({"report": report, "poiOrAddress": assisted})
    assert (position.poi, position.address, position.place) == ("Home", None, "Home")

    geocoded = Position.from_report_wrapper(
        {
            "reportOrNull": report,
            "poiOrAddress": {"pois": [], "address": {"city": "Tôň", "road": "Fő", "houseNum": "1"}},
        }
    )
    assert geocoded.place == "Tôň, Fő 1"
    assert geocoded.timestamp.year == 2026
    assert Position.from_report_wrapper({"trackerId": 1, "reportOrNull": None}) is None


def test_event_and_values() -> None:
    """Small parsing helpers."""
    event = TrackerEvent.from_wrapper({"name": "connected", "trackerId": 3})
    assert (event.tracker_id, event.name, event.timestamp) == (3, "connected", None)
    assert TrackerEvent.from_wrapper({"trackerId": 3}) is None
    assert coerce_value("12059") == 12059.0
    assert coerce_value(35.48) == 35.48
    assert coerce_value("03.28.07") == "03.28.07"
    assert coerce_value(None) is None
