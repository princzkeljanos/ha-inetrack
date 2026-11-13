"""Tests for the IneTrack config and options flow."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.inetrack.api import (
    InetrackApiError,
    InetrackAuthError,
    InetrackConnectionError,
)
from custom_components.inetrack.const import (
    CONF_ADDRESS_LOOKUP,
    CONF_EXTENDED_DATA,
    CONF_SCAN_INTERVAL,
    CONF_TRACKERS,
    DOMAIN,
)

from .conftest import API_KEY, TRACKER_A, TRACKER_B, find_device

APP_URL = (
    "https://app.inetrack.com/#home/devices?deviceGroups=12345&chart=true"
    f"&details=true/route?trackerId={TRACKER_A}&realtime=y"
)


async def _start(hass: HomeAssistant) -> dict:
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})


async def test_user_flow_with_links(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """App links and plain IDs are accepted, then the trackers are named."""
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_API_KEY: f" {API_KEY}\x1b[201~", CONF_TRACKERS: f"{APP_URL}\n{TRACKER_B}"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "names"
    mock_client.get_last_reports.assert_called_with([TRACKER_A, TRACKER_B])

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {str(TRACKER_A): "Mazda3-new", str(TRACKER_B): "  "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_API_KEY] == API_KEY.replace("\x1b[201~", "").strip()
    assert result["options"] == {
        CONF_TRACKERS: {str(TRACKER_A): "Mazda3-new", str(TRACKER_B): f"Tracker {TRACKER_B}"}
    }


async def test_user_flow_discovers_trackers(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """If IneTrack ever lists the trackers again, the IDs can be left empty."""
    mock_client.list_devices.side_effect = None
    mock_client.list_devices.return_value = [{"id": TRACKER_A, "displayName": "Mazda3-new"}]
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: API_KEY}
    )
    assert result["step_id"] == "names"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["options"] == {CONF_TRACKERS: {str(TRACKER_A): "Mazda3-new"}}


async def test_user_flow_requires_trackers(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """Without IDs (and no tracker list from the API) the form asks for them."""
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: API_KEY, CONF_TRACKERS: "no ids here"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_TRACKERS: "trackers_required"}


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (InetrackAuthError("Forbidden"), {"base": "invalid_auth"}),
        (InetrackConnectionError("timeout"), {"base": "cannot_connect"}),
        (InetrackApiError("GetLastReports", "Internal server error", status=500), {"base": "api_error"}),
        (RuntimeError("boom"), {"base": "unknown"}),
    ],
)  # fmt: skip
async def test_user_flow_errors(
    hass: HomeAssistant, mock_client: MagicMock, error: Exception, expected: dict
) -> None:
    """Errors are shown, and the flow recovers once the problem is gone."""
    side_effect = mock_client.get_last_reports.side_effect
    mock_client.get_last_reports.side_effect = error
    result = await _start(hass)
    user_input = {CONF_API_KEY: API_KEY, CONF_TRACKERS: str(TRACKER_A)}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == expected

    mock_client.get_last_reports.side_effect = side_effect
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input)
    assert result["step_id"] == "names"


async def test_user_flow_unknown_tracker(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """The IDs the key cannot see are named in the error."""

    async def last_reports(ids: list[int], *_: object) -> list:
        if 999 in ids:
            raise InetrackAuthError("Forbidden")
        return []

    mock_client.get_last_reports.side_effect = last_reports
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: API_KEY, CONF_TRACKERS: f"{TRACKER_A}, 999"}
    )
    assert result["errors"] == {CONF_TRACKERS: "unknown_trackers"}
    assert result["description_placeholders"]["trackers"] == "999"


async def test_already_configured(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """A tracker cannot be added twice, even with another key."""
    config_entry.add_to_hass(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "another-key", CONF_TRACKERS: str(TRACKER_B)}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """A new key replaces the rejected one after it is checked."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    mock_client.get_last_reports.side_effect = InetrackAuthError("Forbidden")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "still-wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    mock_client.get_last_reports.side_effect = None
    mock_client.get_last_reports.return_value = []
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "new-key"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_API_KEY] == "new-key"


async def test_options_flow(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """Trackers and polling options can be changed; the entry reloads."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_TRACKERS: str(TRACKER_A),
            CONF_SCAN_INTERVAL: 120.0,
            CONF_ADDRESS_LOOKUP: False,
            CONF_EXTENDED_DATA: False,
        },
    )
    assert result["step_id"] == "names"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {str(TRACKER_A): "Red Mazda"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {
        CONF_SCAN_INTERVAL: 120,
        CONF_ADDRESS_LOOKUP: False,
        CONF_EXTENDED_DATA: False,
        CONF_TRACKERS: {str(TRACKER_A): "Red Mazda"},
    }
    coordinator = config_entry.runtime_data
    assert coordinator.update_interval.total_seconds() == 120
    assert list(coordinator.trackers) == [TRACKER_A]
    mock_client.get_last_reports.assert_called_with([TRACKER_A], "POSITION")
    # Renaming changes the device name; entity IDs stay as they were.
    assert find_device(hass, config_entry, TRACKER_A).name == "Red Mazda"
    assert hass.states.get("device_tracker.mazda3_new") is not None
    assert find_device(hass, config_entry, TRACKER_B) is None
