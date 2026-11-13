"""Polling coordinator for the IneTrack integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    POSITION_TYPE_POI,
    POSITION_TYPE_POSITION,
    POSITION_TYPE_REVERSE_GEOCODE,
    InetrackApiClient,
    InetrackApiError,
    InetrackAuthError,
    InetrackError,
    Measurement,
    Position,
    Tracker,
    TrackerEvent,
    coerce_value,
    ms_to_datetime,
)
from .const import (
    COMMAND_PAUSE,
    CONF_ADDRESS_LOOKUP,
    CONF_EXTENDED_DATA,
    CONF_SCAN_INTERVAL,
    CONF_TRACKERS,
    DEFAULT_ADDRESS_LOOKUP,
    DEFAULT_EXTENDED_DATA,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_CONNECTED,
    EVENT_IGNITION_OFF,
    EVENT_IGNITION_ON,
    EVENTS_DISCONNECTED,
    EXTENDED_DATA_LOOKBACK,
    EXTENDED_DATA_OVERLAP,
    PRIVATE_DATA_NAMES,
    STATE_EVENTS,
    TOLERATED_FAILURES,
)

_LOGGER = logging.getLogger(__name__)

type InetrackConfigEntry = ConfigEntry[InetrackCoordinator]


def _newest(events: Mapping[str, TrackerEvent], names: Iterable[str]) -> TrackerEvent | None:
    found = [events[name] for name in names if name in events and events[name].timestamp]
    return max(found, key=lambda event: event.timestamp) if found else None


@dataclass(slots=True)
class InetrackDeviceData:
    """Everything known about one tracker after a poll."""

    tracker: Tracker
    position: Position | None = None
    events: dict[str, TrackerEvent] = field(default_factory=dict)
    status_data: dict[str, Measurement] = field(default_factory=dict)
    extended_data: dict[str, Measurement] = field(default_factory=dict)

    @property
    def ignition(self) -> bool | None:
        """Ignition state from the newest ACC_On / ACC_Off event."""
        event = _newest(self.events, (EVENT_IGNITION_ON, EVENT_IGNITION_OFF))
        return None if event is None else event.name == EVENT_IGNITION_ON

    @property
    def connected(self) -> bool | None:
        """Server connection from the newest connected / disconnected event."""
        event = _newest(self.events, (EVENT_CONNECTED, *EVENTS_DISCONNECTED))
        return None if event is None else event.name == EVENT_CONNECTED

    def event_time(self, *names: str) -> Any:
        """Time of the newest of the given events."""
        event = _newest(self.events, names)
        return event.timestamp if event else None


def _merge_newest(cache: dict[str, Measurement], name: str, value: Any, stamp: Any) -> None:
    """Store a value unless a newer one is already known."""
    if not name or PRIVATE_DATA_NAMES.search(name):
        return
    when = ms_to_datetime(stamp)
    current = cache.get(name)
    if current and current.timestamp and when and when < current.timestamp:
        return
    cache[name] = Measurement(name=name, value=coerce_value(value), timestamp=when)


class InetrackCoordinator(DataUpdateCoordinator[dict[int, InetrackDeviceData]]):
    """Poll the configured trackers of one IneTrack account."""

    config_entry: InetrackConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: InetrackConfigEntry, client: InetrackApiClient
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.client = client
        self.trackers: dict[int, Tracker] = {
            int(tracker_id): Tracker(id=int(tracker_id), name=name)
            for tracker_id, name in entry.options.get(CONF_TRACKERS, {}).items()
        }
        self.address_lookup: bool = entry.options.get(CONF_ADDRESS_LOOKUP, DEFAULT_ADDRESS_LOOKUP)
        self.extended_data_enabled: bool = entry.options.get(
            CONF_EXTENDED_DATA, DEFAULT_EXTENDED_DATA
        )
        self.rejected: set[int] = set()
        self._positions: dict[int, Position] = {}
        self._events: dict[int, dict[str, TrackerEvent]] = {}
        self._status: dict[int, dict[str, Measurement]] = {}
        self._extended: dict[int, dict[str, Measurement]] = {}
        self._extended_report_ms: dict[int, int] = {}
        self._failures = 0
        self._command_failures: dict[str, int] = {}
        self._command_skip_until: dict[str, float] = {}
        # Last raw responses, for diagnostics.
        self.raw: dict[str, Any] = {}

    async def _async_update_data(self) -> dict[int, InetrackDeviceData]:
        """Fetch the latest data of all trackers."""
        try:
            data = await self._fetch()
        except InetrackAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except InetrackError as err:
            # The API has occasional one-off errors; going unavailable for those
            # would also trigger "left home" style automations.
            self._failures += 1
            if self.data is not None and self._failures < TOLERATED_FAILURES:
                _LOGGER.debug("Update failed (%s), keeping the previous data", err)
                return self.data
            raise UpdateFailed(str(err)) from err
        self._failures = 0
        return data

    async def _fetch(self) -> dict[int, InetrackDeviceData]:
        ids = list(self.trackers)
        if not ids:
            return {}

        for wrapper in await self._get_reports(ids):
            tracker_id = wrapper.get("trackerId")
            position = Position.from_report_wrapper(wrapper)
            if tracker_id is not None and position is not None:
                self._positions[int(tracker_id)] = position
        ids = [tracker_id for tracker_id in ids if tracker_id not in self.rejected]

        events = await self._optional(
            "GetLastOneDeviceEvents", lambda: self.client.get_last_events(ids, STATE_EVENTS)
        )
        for event in events or []:
            known = self._events.setdefault(event.tracker_id, {})
            current = known.get(event.name)
            if (
                not current
                or not current.timestamp
                or (event.timestamp and event.timestamp >= current.timestamp)
            ):
                known[event.name] = event

        status = await self._optional(
            "GetLastOneStatusData", lambda: self.client.get_last_status_data(ids)
        )
        if status is not None:
            self.raw["GetLastOneStatusData"] = status
        for item in status or []:
            inner = item.get("statusData") if isinstance(item.get("statusData"), dict) else {}
            tracker_id = item.get("trackerId") or inner.get("trackerId") or inner.get("deviceId")
            if tracker_id is None:
                continue
            _merge_newest(
                self._status.setdefault(int(tracker_id), {}),
                str(item.get("name") or inner.get("customName") or inner.get("name") or ""),
                item.get("value", inner.get("value")),
                item.get("creationTimestamp") or inner.get("creationTimestamp"),
            )

        if self.extended_data_enabled:
            for tracker_id in ids:
                await self._update_extended_data(tracker_id)

        return {
            tracker_id: InetrackDeviceData(
                tracker=tracker,
                position=self._positions.get(tracker_id),
                events=dict(self._events.get(tracker_id, {})),
                status_data=dict(self._status.get(tracker_id, {})),
                extended_data=dict(self._extended.get(tracker_id, {})),
            )
            for tracker_id, tracker in self.trackers.items()
            if tracker_id not in self.rejected
        }

    async def _get_reports(self, ids: list[int]) -> list[dict[str, Any]]:
        """Read the last positions, finding out which trackers the key cannot see."""
        try:
            reports = await self._get_reports_with_fallback(ids)
        except InetrackAuthError:
            # One unknown tracker ID makes the API refuse the whole request.
            if len(ids) == 1:
                raise
            reports = []
            rejected: set[int] = set()
            for tracker_id in ids:
                try:
                    reports.extend(await self._get_reports_with_fallback([tracker_id]))
                except InetrackAuthError:
                    rejected.add(tracker_id)
            if rejected == set(ids):
                raise
            if rejected != self.rejected:
                _LOGGER.warning(
                    "IneTrack refuses tracker ID(s) %s with this API key. Check the "
                    "tracker IDs in the integration options",
                    ", ".join(map(str, sorted(rejected))),
                )
            self.rejected = rejected
        else:
            self.rejected = set()
        self.raw["GetLastReports"] = reports
        return reports

    async def _get_reports_with_fallback(self, ids: list[int]) -> list[dict[str, Any]]:
        """Address lookup sometimes fails server-side; retry with POI names only."""
        if not self.address_lookup:
            return await self.client.get_last_reports(ids, POSITION_TYPE_POSITION)
        try:
            return await self.client.get_last_reports(ids, POSITION_TYPE_REVERSE_GEOCODE)
        except InetrackApiError as err:
            _LOGGER.debug("Address lookup failed, retrying without it: %s", err)
            return await self.client.get_last_reports(ids, POSITION_TYPE_POI)

    async def _update_extended_data(self, tracker_id: int) -> None:
        """Read the extended data that came with reports newer than the last read."""
        position = self._positions.get(tracker_id)
        if position is None or position.timestamp is None:
            return
        report_ms = round(position.timestamp.timestamp() * 1000)
        last_ms = self._extended_report_ms.get(tracker_id)
        if last_ms is not None and report_ms <= last_ms:
            return  # no new report since the last read
        if last_ms is None:
            start_ms = report_ms - int(EXTENDED_DATA_LOOKBACK.total_seconds() * 1000)
        else:
            start_ms = last_ms - int(EXTENDED_DATA_OVERLAP.total_seconds() * 1000)
        end_ms = int(time.time() * 1000)

        rows = await self._optional(
            "GetExtendedDataList",
            lambda: self.client.get_extended_data(tracker_id, start_ms, end_ms),
        )
        if rows is None:
            return
        cache = self._extended.setdefault(tracker_id, {})
        for row in rows:
            _merge_newest(
                cache,
                str(row.get("dataName") or row.get("name") or ""),
                row.get("dataValue"),
                row.get("creationTimestamp"),
            )
        self._extended_report_ms[tracker_id] = report_ms

    async def _optional(self, command: str, call: Callable[[], Awaitable[Any]]) -> Any:
        """Run a non-essential command; pause commands that keep failing.

        The position (GetLastReports) already proved the key works, so an error
        here, even "Forbidden", only means this command is unavailable.
        """
        if time.monotonic() < self._command_skip_until.get(command, 0):
            return None
        try:
            result = await call()
        except (InetrackAuthError, InetrackApiError) as err:
            failures = self._command_failures.get(command, 0) + 1
            self._command_failures[command] = failures
            if failures >= TOLERATED_FAILURES:
                self._command_skip_until[command] = time.monotonic() + COMMAND_PAUSE.total_seconds()
                _LOGGER.warning(
                    "IneTrack command %s keeps failing, pausing it for %s: %s",
                    command,
                    COMMAND_PAUSE,
                    err,
                )
            else:
                _LOGGER.debug("IneTrack command %s failed: %s", command, err)
            return None
        self._command_failures.pop(command, None)
        return result
