"""Async client for the IneTrack developer API.

API description: https://api.inetrack.com (command browser)
JSON schema:     https://api.inetrack.com/apischema

Every call is an HTTP GET to ``/api`` with the command name and its parameters
in the query string, authenticated with the ``apikey`` parameter::

    /api?command=GetLastReports&deviceIds=1,2&positionType=POSITION&apikey=...

Nested parameters are flattened with dots (``searchParameters.deviceId=1``) and
array parameters are joined with commas. Every response is a JSON object with
``success``, ``message`` and ``errorId`` plus command-specific fields.

Notes from testing against the live API (November 2026):

* ``GetLastReports``, ``GetExtendedDataList``, ``GetLastOneStatusData`` and
  ``GetLastOneDeviceEvents`` work with an API key.
* The object list commands (``ListUserDevices``, ``GetTrackersByIds``,
  ``ListStatusDataRules``, ...) and ``GetConnectionStates`` answer HTTP 500 for
  every request, so tracker IDs have to be entered by the user.
* An unknown key gives HTTP 403 "Forbidden" on the working commands (and also
  when the tracker ID does not belong to the account).

This module deliberately has no Home Assistant imports, so it can be used (and
tested) on its own.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import logging
import re
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.inetrack.com"
REQUEST_TIMEOUT = 30

POSITION_TYPE_POSITION = "POSITION"
POSITION_TYPE_POI = "POI"
POSITION_TYPE_REVERSE_GEOCODE = "REVERSE_GEOCODE"


class InetrackError(Exception):
    """Base class for IneTrack errors."""


class InetrackConnectionError(InetrackError):
    """The API could not be reached."""


class InetrackAuthError(InetrackError):
    """The API key (or the tracker ID) was rejected."""


class InetrackApiError(InetrackError):
    """The API answered, but reported a failure."""

    def __init__(
        self,
        command: str,
        message: str,
        error_id: int | None = None,
        status: int | None = None,
    ) -> None:
        """Initialize the error."""
        super().__init__(f"{command} failed: {message} (HTTP {status}, errorId {error_id})")
        self.command = command
        self.message = message
        self.error_id = error_id
        self.status = status


def _encode_params(params: Mapping[str, Any], prefix: str = "") -> dict[str, str]:
    """Flatten command parameters into query string values."""
    encoded: dict[str, str] = {}
    for key, value in params.items():
        if value is None:
            continue
        name = f"{prefix}{key}"
        if isinstance(value, Mapping):
            encoded.update(_encode_params(value, f"{name}."))
        elif isinstance(value, bool):
            encoded[name] = "true" if value else "false"
        elif isinstance(value, (list, tuple, set, frozenset)):
            encoded[name] = ",".join(str(item) for item in value)
        else:
            encoded[name] = str(value)
    return encoded


def parse_tracker_ids(text: str) -> list[int]:
    """Read tracker IDs from text: plain numbers or app.inetrack.com URLs.

    ``https://app.inetrack.com/#home/devices?...route?trackerId=100001`` gives 100001.
    """
    ids: list[int] = []
    for part in re.split(r"[\s,;]+", text.strip()):
        if not part:
            continue
        found = re.findall(r"trackerId=(\d+)", part)
        if not found and part.isdigit():
            found = [part]
        for item in found:
            if int(item) not in ids:
                ids.append(int(item))
    return ids


def ms_to_datetime(value: Any) -> datetime | None:
    """Convert an IneTrack epoch timestamp to an aware datetime.

    Most timestamps are epoch milliseconds, but ``storageTimestamp`` is in
    nanoseconds, so the unit is inferred from the magnitude.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        return None
    if stamp <= 0:
        return None
    if stamp > 1e17:  # nanoseconds
        stamp /= 1e6
    elif stamp > 1e14:  # microseconds
        stamp /= 1e3
    elif stamp < 1e11:  # seconds
        stamp *= 1e3
    try:
        return datetime.fromtimestamp(stamp / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _poi_name(data: Mapping[str, Any]) -> str | None:
    """Name of the first POI (a plain or an "assisted" object with stringValues)."""
    for poi in data.get("pois") or []:
        if not isinstance(poi, Mapping):
            continue
        strings = poi.get("stringValues")
        name = poi.get("name") or (strings.get("name") if isinstance(strings, Mapping) else None)
        if name:
            return str(name)
    return None


def _address_text(address: Any) -> str | None:
    """Readable text of a ``GeocodedAddress`` object."""
    if not isinstance(address, Mapping):
        return None
    if address.get("displayAddress"):
        return str(address["displayAddress"])
    street = " ".join(str(part) for part in (address.get("road"), address.get("houseNum")) if part)
    parts = [address.get("postcode"), address.get("city"), street]
    return ", ".join(str(part) for part in parts if part) or None


@dataclass(slots=True)
class Tracker:
    """A tracker (device) of the account."""

    id: int
    name: str
    model: str | None = None
    serial: str | None = None
    expiration: datetime | None = None

    @classmethod
    def from_api(cls, item: Mapping[str, Any]) -> Tracker:
        """Build from a ``deviceDescriptor`` object."""
        device_id = int(item["id"])
        device_type = item.get("devicetype")
        return cls(
            id=device_id,
            name=str(item.get("displayName") or f"Tracker {device_id}"),
            model=(device_type.get("name") or None) if isinstance(device_type, Mapping) else None,
            serial=str(item["nativeID"]) if item.get("nativeID") else None,
            expiration=ms_to_datetime(item.get("expiration")),
        )


@dataclass(slots=True)
class Position:
    """The last reported position of a tracker."""

    latitude: float
    longitude: float
    altitude: float | None = None
    heading: float | None = None
    speed: float | None = None
    accuracy: float | None = None
    timestamp: datetime | None = None
    poi: str | None = None
    address: str | None = None

    @property
    def place(self) -> str | None:
        """POI name if the position is inside one, otherwise the address."""
        return self.poi or self.address

    @classmethod
    def from_report_wrapper(cls, wrapper: Mapping[str, Any]) -> Position | None:
        """Build from a ``ReportWrapper`` object, if it has a fix."""
        report = wrapper.get("reportOrNull") or wrapper.get("report")
        if not isinstance(report, Mapping):
            return None
        latitude = _as_float(report.get("lat"))
        longitude = _as_float(report.get("lng"))
        if latitude is None or longitude is None:
            return None
        poi_or_address = wrapper.get("poiOrAddress")
        if not isinstance(poi_or_address, Mapping):
            poi_or_address = {}
        return cls(
            latitude=latitude,
            longitude=longitude,
            altitude=_as_float(report.get("alt")),
            heading=_as_float(report.get("heading")),
            speed=_as_float(report.get("speed")),
            accuracy=_as_float(report.get("accuracy")),
            timestamp=ms_to_datetime(report.get("creationTimestamp")),
            poi=_poi_name(poi_or_address),
            address=_address_text(poi_or_address.get("address") or wrapper.get("addressOrNull")),
        )


@dataclass(slots=True)
class Measurement:
    """The latest value of a named data series of a tracker."""

    name: str
    value: float | str | None
    timestamp: datetime | None


@dataclass(slots=True)
class TrackerEvent:
    """An event reported for a tracker (e.g. ``ACC_On``, ``connected``)."""

    tracker_id: int
    name: str
    timestamp: datetime | None
    message: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_wrapper(cls, wrapper: Mapping[str, Any]) -> TrackerEvent | None:
        """Build from a ``DeviceEventWrapper`` object."""
        event = wrapper.get("deviceEvent")
        if not isinstance(event, Mapping):
            event = wrapper
        tracker_id = wrapper.get("trackerId") or event.get("trackerId") or event.get("deviceId")
        name = wrapper.get("name") or event.get("name")
        if tracker_id is None or not name:
            return None
        return cls(
            tracker_id=int(tracker_id),
            name=str(name),
            timestamp=ms_to_datetime(
                event.get("creationTimestamp") or wrapper.get("creationTimestamp")
            ),
            message=event.get("message") or None,
            raw=dict(event),
        )


def coerce_value(value: Any) -> float | str | None:
    """Return numbers as float and keep anything else as text."""
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        return text


class InetrackApiClient:
    """Talk to the IneTrack API with an API key."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    async def request(self, command: str, **params: Any) -> dict[str, Any]:
        """Run an API command and return the decoded response object."""
        query = {"command": command, **_encode_params(params), "apikey": self._api_key}
        url = f"{self._base_url}/api"
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.get(url, params=query) as response:
                    status = response.status
                    text = await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise InetrackConnectionError(
                f"Error talking to the IneTrack API ({command}): {type(err).__name__}"
            ) from err

        _LOGGER.debug("%s -> HTTP %s: %.300s", command, status, text)

        if status in (401, 403):
            raise InetrackAuthError(f"{command}: rejected by the API (HTTP {status})")
        if status in (502, 503, 504):
            raise InetrackConnectionError(f"{command}: IneTrack API unavailable (HTTP {status})")

        try:
            data = json.loads(text)
        except ValueError as err:
            raise InetrackApiError(command, "response is not JSON", status=status) from err
        if not isinstance(data, dict):
            raise InetrackApiError(command, "unexpected response type", status=status)

        if status >= 400 or data.get("success") is False:
            message = str(data.get("message") or "unknown error")
            if message.strip().lower() in ("forbidden", "unauthorized", "not authorized"):
                raise InetrackAuthError(f"{command}: {message}")
            raise InetrackApiError(command, message, data.get("errorId"), status)
        return data

    async def list_devices(self) -> list[dict[str, Any]]:
        """Return the trackers of the account (currently fails server-side, see above)."""
        data = await self.request("ListUserDevices", firstResult=0, numMaxResult=1000)
        return [item for item in data.get("list") or [] if isinstance(item, dict)]

    async def get_last_reports(
        self, tracker_ids: Iterable[int], position_type: str = POSITION_TYPE_POSITION
    ) -> list[dict[str, Any]]:
        """Return the last position report of each tracker (``ReportWrapper``)."""
        data = await self.request(
            "GetLastReports", deviceIds=list(tracker_ids), positionType=position_type
        )
        return [item for item in data.get("repList") or [] if isinstance(item, dict)]

    async def get_extended_data(
        self,
        tracker_id: int,
        start_ms: int,
        end_ms: int,
        names: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return raw tracker values (voltage, odometer, I/O, ...) in a time window."""
        data = await self.request(
            "GetExtendedDataList",
            deviceId=tracker_id,
            minCreationTimestamp=start_ms,
            maxCreationTimestamp=end_ms,
            names=list(names) if names else None,
        )
        return [item for item in data.get("extDataList") or [] if isinstance(item, dict)]

    async def get_last_status_data(self, tracker_ids: Iterable[int]) -> list[dict[str, Any]]:
        """Return the newest value of every status data series of the trackers."""
        data = await self.request("GetLastOneStatusData", trackerIds=list(tracker_ids))
        return [item for item in data.get("statusDatas") or [] if isinstance(item, dict)]

    async def get_last_events(
        self, tracker_ids: Iterable[int], names: Iterable[str] | None = None
    ) -> list[TrackerEvent]:
        """Return the newest events of the trackers."""
        data = await self.request(
            "GetLastOneDeviceEvents",
            trackerIds=list(tracker_ids),
            names=list(names) if names else None,
        )
        events = (TrackerEvent.from_wrapper(item) for item in data.get("deviceEvents") or [])
        return [event for event in events if event is not None]
