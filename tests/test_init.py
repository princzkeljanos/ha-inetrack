"""Tests for setting up IneTrack and its entities."""

from __future__ import annotations

from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.inetrack.api import (
    InetrackApiError,
    InetrackAuthError,
    InetrackConnectionError,
)
from custom_components.inetrack.const import CONF_TRACKERS
from custom_components.inetrack.diagnostics import async_get_config_entry_diagnostics

from .conftest import TRACKER_A, TRACKER_B, find_device, report, reports


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float = 61) -> None:
    freezer.tick(seconds)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _float(hass: HomeAssistant, entity_id: str) -> float:
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return float(state.state)


async def test_position_entities(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """The device tracker and position sensors come from GetLastReports."""
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    tracker = hass.states.get("device_tracker.mazda3_new")
    assert tracker.attributes["latitude"] == 47.4979
    assert tracker.attributes["longitude"] == 19.0402
    assert tracker.attributes["gps_accuracy"] == 7
    assert tracker.attributes["source_type"] == "gps"
    assert tracker.attributes["place"] == "Autószerelő műhely"

    assert hass.states.get("sensor.mazda3_new_place").state == "Autószerelő műhely"
    assert hass.states.get("sensor.mazda6_place").state == "Budapest, Fő utca 1"
    assert _float(hass, "sensor.mazda6_speed") == 42
    assert _float(hass, "sensor.mazda6_heading") == 335.5
    assert hass.states.get("sensor.mazda6_last_report").state != "unknown"
    assert hass.states.get("binary_sensor.mazda6_moving").state == STATE_ON
    assert hass.states.get("binary_sensor.mazda3_new_moving").state == STATE_OFF

    registry = er.async_get(hass)
    for disabled in (
        "sensor.mazda6_altitude",
        "sensor.mazda6_gps_accuracy",
        "sensor.mazda6_address",
    ):
        assert hass.states.get(disabled) is None
        assert registry.async_get(disabled).disabled_by is er.RegistryEntryDisabler.INTEGRATION

    device = find_device(hass, config_entry, TRACKER_B)
    assert device.name == "Mazda6"
    assert device.manufacturer == "IneTrack"
    mock_client.get_last_reports.assert_called_with([TRACKER_A, TRACKER_B], "REVERSE_GEOCODE")


async def test_data_sensors(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """Known raw values become sensors with proper units; the rest are hidden."""
    await _setup(hass, config_entry)

    voltage = hass.states.get("sensor.mazda3_new_car_battery_voltage")
    assert float(voltage.state) == pytest.approx(12.059)
    assert voltage.attributes["unit_of_measurement"] == "V"
    assert voltage.attributes["device_class"] == "voltage"
    assert "measured_at" in voltage.attributes
    assert _float(hass, "sensor.mazda6_car_battery_voltage") == pytest.approx(12.506)
    assert _float(hass, "sensor.mazda3_new_tracker_battery_voltage") == pytest.approx(4.045)

    odometer = hass.states.get("sensor.mazda3_new_odometer")
    assert float(odometer.state) == pytest.approx(75607.609)
    assert odometer.attributes["unit_of_measurement"] == "km"
    assert odometer.attributes["state_class"] == "total_increasing"
    assert _float(hass, "sensor.mazda6_odometer") == pytest.approx(67445.538)
    assert _float(hass, "sensor.mazda6_trip_distance") == pytest.approx(0.98)
    assert _float(hass, "sensor.mazda3_new_satellites") == 8
    assert _float(hass, "sensor.mazda3_new_gsm_signal") == pytest.approx(35.48)
    assert _float(hass, "sensor.mazda6_gsm_signal") == 100

    registry = er.async_get(hass)
    raw = registry.async_get("sensor.mazda3_new_digin2")
    assert raw.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert raw.entity_category is EntityCategory.DIAGNOSTIC
    assert registry.async_get("sensor.mazda6_sleepmode") is not None
    # Private or duplicate values never become entities.
    unique_ids = {entry.unique_id for entry in registry.entities.values()}
    for name in ("simiccidnumberpart1", "iokeyvalue", "ibuttonid", "ignition", "gpsspeed"):
        assert not any(uid.endswith(f"extended_{name}") for uid in unique_ids), name


async def test_event_binary_sensors(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """Ignition and connectivity follow the newest of their events."""
    await _setup(hass, config_entry)
    assert hass.states.get("binary_sensor.mazda3_new_ignition").state == STATE_OFF
    assert hass.states.get("binary_sensor.mazda6_ignition").state == STATE_ON
    assert hass.states.get("binary_sensor.mazda3_new_connected").state == STATE_ON
    connected = hass.states.get("binary_sensor.mazda6_connected")
    assert connected.state == STATE_OFF
    assert "reported_at" in connected.attributes


async def test_extended_data_read_incrementally(
    hass: HomeAssistant,
    mock_client: MagicMock,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Extended data is read only after a new report, from just before the last one."""
    fixed = reports()
    mock_client.get_last_reports.side_effect = None
    mock_client.get_last_reports.return_value = fixed
    await _setup(hass, config_entry)

    first = {c.args[0]: c.args for c in mock_client.get_extended_data.call_args_list}
    report_ms = fixed[0]["report"]["creationTimestamp"]
    assert first[TRACKER_A][1] == report_ms - 10 * 60 * 1000
    calls = mock_client.get_extended_data.call_count

    await _tick(hass, freezer)  # same reports: nothing new to read
    assert mock_client.get_extended_data.call_count == calls

    mock_client.get_last_reports.return_value = [report(TRACKER_A, age_s=0), fixed[1]]
    await _tick(hass, freezer)
    assert mock_client.get_extended_data.call_count == calls + 1
    tracker_id, start_ms, _end = mock_client.get_extended_data.call_args.args
    assert tracker_id == TRACKER_A
    assert start_ms == report_ms - 5 * 60 * 1000


async def test_rejected_tracker(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """A tracker ID the key cannot see is skipped, the others keep working."""

    async def last_reports(ids: list[int], *_: object) -> list[dict]:
        if TRACKER_B in ids:
            raise InetrackAuthError("Forbidden")
        return [r for r in reports() if r["trackerId"] in ids]

    mock_client.get_last_reports.side_effect = last_reports
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("device_tracker.mazda3_new") is not None
    assert hass.states.get("device_tracker.mazda6") is None
    assert config_entry.runtime_data.rejected == {TRACKER_B}


async def test_auth_failure_starts_reauth(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """When every tracker is refused, the key is asked for again."""
    mock_client.get_last_reports.side_effect = InetrackAuthError("Forbidden")
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_setup_retry(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """A network problem during setup is retried later."""
    mock_client.get_last_reports.side_effect = InetrackConnectionError("timeout")
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_address_lookup_fallback(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """When reverse geocoding fails server-side, POI names are still shown."""

    async def last_reports(ids: list[int], position_type: str) -> list[dict]:
        if position_type == "REVERSE_GEOCODE":
            raise InetrackApiError("GetLastReports", "Internal server error", status=500)
        return [r for r in reports() if r["trackerId"] in ids]

    mock_client.get_last_reports.side_effect = last_reports
    await _setup(hass, config_entry)
    assert hass.states.get("sensor.mazda3_new_place").state == "Autószerelő műhely"
    mock_client.get_last_reports.assert_called_with([TRACKER_A, TRACKER_B], "POI")


async def test_short_outage_keeps_state(
    hass: HomeAssistant,
    mock_client: MagicMock,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """One or two failed polls keep the last state; the third makes it unavailable."""
    await _setup(hass, config_entry)
    mock_client.get_last_reports.side_effect = InetrackConnectionError("down")
    await _tick(hass, freezer)
    await _tick(hass, freezer)
    assert hass.states.get("device_tracker.mazda6").state != STATE_UNAVAILABLE
    await _tick(hass, freezer)
    assert hass.states.get("device_tracker.mazda6").state == STATE_UNAVAILABLE

    mock_client.get_last_reports.side_effect = lambda ids, *_: reports()
    await _tick(hass, freezer)
    assert hass.states.get("device_tracker.mazda6").state != STATE_UNAVAILABLE


async def test_failing_optional_command_is_paused(
    hass: HomeAssistant,
    mock_client: MagicMock,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A command that keeps failing is paused instead of failing every poll."""
    mock_client.get_last_status_data.side_effect = InetrackApiError("x", "error", status=500)
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    await _tick(hass, freezer)
    await _tick(hass, freezer)
    assert mock_client.get_last_status_data.call_count == 3
    await _tick(hass, freezer)
    assert mock_client.get_last_status_data.call_count == 3
    assert hass.states.get("sensor.mazda6_gsm_signal") is None


async def test_removed_tracker_device_is_deleted(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """Removing a tracker in the options removes its device."""
    await _setup(hass, config_entry)
    assert find_device(hass, config_entry, TRACKER_B) is not None

    hass.config_entries.async_update_entry(
        config_entry, options={CONF_TRACKERS: {str(TRACKER_A): "Mazda3-new"}}
    )
    await hass.async_block_till_done()
    assert find_device(hass, config_entry, TRACKER_B) is None
    assert hass.states.get("device_tracker.mazda3_new") is not None


async def test_unload(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """The entry unloads cleanly."""
    await _setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_diagnostics_are_redacted(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """Diagnostics never contain the key, the position or the address."""
    await _setup(hass, config_entry)
    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    text = str(diagnostics)
    assert config_entry.data["api_key"] not in text
    assert "47.4979" not in text
    assert "Fő utca" not in text
    assert "8900000001" not in text
    assert diagnostics["devices"][str(TRACKER_B)]["ignition"] is True
