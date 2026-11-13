"""Config flow for the IneTrack integration."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import logging
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import (
    InetrackApiClient,
    InetrackAuthError,
    InetrackConnectionError,
    InetrackError,
    Tracker,
    parse_tracker_ids,
)
from .const import (
    CONF_ADDRESS_LOOKUP,
    CONF_EXTENDED_DATA,
    CONF_SCAN_INTERVAL,
    CONF_TRACKERS,
    DEFAULT_ADDRESS_LOOKUP,
    DEFAULT_EXTENDED_DATA,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    WEB_APP_URL,
)

_LOGGER = logging.getLogger(__name__)

PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
MULTILINE = TextSelector(TextSelectorConfig(multiline=True))
PLACEHOLDERS = {"url": WEB_APP_URL}


def _clean_key(value: str) -> str:
    """Drop whitespace, quotes and terminal paste markers around a pasted key."""
    value = value.replace("\x1b[200~", "").replace("\x1b[201~", "")
    return "".join(ch for ch in value if ch.isprintable()).strip().strip("\"'")


def _key_id(api_key: str) -> str:
    return "key-" + hashlib.sha256(api_key.encode()).hexdigest()[:16]


async def _async_check_trackers(
    hass: HomeAssistant, api_key: str, tracker_ids: list[int]
) -> tuple[dict[str, str], dict[str, str]]:
    """Check that the key can read the trackers; return (errors, placeholders)."""
    client = InetrackApiClient(async_get_clientsession(hass), api_key)
    try:
        await client.get_last_reports(tracker_ids)
    except InetrackAuthError:
        # One unknown ID makes the API refuse the whole request: find which.
        rejected: list[int] = []
        try:
            for tracker_id in tracker_ids:
                try:
                    await client.get_last_reports([tracker_id])
                except InetrackAuthError:
                    rejected.append(tracker_id)
        except InetrackError:
            return {"base": "cannot_connect"}, {}
        if len(rejected) == len(tracker_ids):
            return {"base": "invalid_auth"}, {}
        return {CONF_TRACKERS: "unknown_trackers"}, {"trackers": ", ".join(map(str, rejected))}
    except InetrackConnectionError:
        return {"base": "cannot_connect"}, {}
    except InetrackError as err:
        _LOGGER.debug("Tracker check failed: %s", err)
        return {"base": "api_error"}, {}
    except Exception:
        _LOGGER.exception("Unexpected error while checking the IneTrack trackers")
        return {"base": "unknown"}, {}
    return {}, {}


async def _async_discover_trackers(hass: HomeAssistant, api_key: str) -> dict[int, str]:
    """Try to list the account's trackers (the API currently refuses this)."""
    client = InetrackApiClient(async_get_clientsession(hass), api_key)
    try:
        devices = await client.list_devices()
    except InetrackError as err:
        _LOGGER.debug("Tracker list not available: %s", err)
        return {}
    trackers = {}
    for item in devices:
        try:
            tracker = Tracker.from_api(item)
        except (KeyError, TypeError, ValueError):
            continue
        trackers[tracker.id] = tracker.name
    return trackers


def _names_schema(tracker_ids: list[int], names: Mapping[int, str]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                str(tracker_id), default=names.get(tracker_id, f"Tracker {tracker_id}")
            ): str
            for tracker_id in tracker_ids
        }
    )


def _names_from_input(tracker_ids: list[int], user_input: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(tracker_id): str(user_input.get(str(tracker_id)) or "").strip()
        or f"Tracker {tracker_id}"
        for tracker_id in tracker_ids
    }


class InetrackConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up IneTrack with an API key and tracker IDs."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._api_key = ""
        self._tracker_ids: list[int] = []
        self._names: dict[int, str] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the API key and the tracker IDs."""
        errors: dict[str, str] = {}
        placeholders = dict(PLACEHOLDERS)
        if user_input is not None:
            api_key = _clean_key(user_input[CONF_API_KEY])
            tracker_ids = parse_tracker_ids(user_input.get(CONF_TRACKERS, ""))
            names: dict[int, str] = {}
            if not tracker_ids:
                names = await _async_discover_trackers(self.hass, api_key)
                tracker_ids = list(names)
            if not tracker_ids:
                errors[CONF_TRACKERS] = "trackers_required"
            else:
                errors, extra = await _async_check_trackers(self.hass, api_key, tracker_ids)
                placeholders.update(extra)
            if not errors:
                await self.async_set_unique_id(_key_id(api_key))
                self._abort_if_unique_id_configured()
                for entry in self._async_current_entries(include_ignore=False):
                    configured = set(map(int, entry.options.get(CONF_TRACKERS, {})))
                    if configured & set(tracker_ids):
                        return self.async_abort(reason="already_configured")
                self._api_key = api_key
                self._tracker_ids = tracker_ids
                self._names = names
                return await self.async_step_names()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_API_KEY): PASSWORD,
                        vol.Optional(CONF_TRACKERS): MULTILINE,
                    }
                ),
                user_input or {},
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_names(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Name the trackers."""
        if user_input is not None:
            return self.async_create_entry(
                title="IneTrack",
                data={CONF_API_KEY: self._api_key},
                options={CONF_TRACKERS: _names_from_input(self._tracker_ids, user_input)},
            )
        return self.async_show_form(
            step_id="names", data_schema=_names_schema(self._tracker_ids, self._names)
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start re-authentication after the API key was rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new API key."""
        errors: dict[str, str] = {}
        placeholders = dict(PLACEHOLDERS)
        entry = self._get_reauth_entry()
        if user_input is not None:
            api_key = _clean_key(user_input[CONF_API_KEY])
            tracker_ids = [int(t) for t in entry.options.get(CONF_TRACKERS, {})]
            errors, extra = await _async_check_trackers(self.hass, api_key, tracker_ids)
            placeholders.update(extra)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_KEY: api_key}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): PASSWORD}),
            errors=errors,
            description_placeholders=placeholders,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> InetrackOptionsFlow:
        """Return the options flow."""
        return InetrackOptionsFlow()


class InetrackOptionsFlow(OptionsFlow):
    """Trackers, polling and data options."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._options: dict[str, Any] = {}
        self._tracker_ids: list[int] = []

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the options."""
        errors: dict[str, str] = {}
        placeholders = dict(PLACEHOLDERS)
        options = self.config_entry.options
        if user_input is not None:
            tracker_ids = parse_tracker_ids(user_input.get(CONF_TRACKERS, ""))
            if not tracker_ids:
                errors[CONF_TRACKERS] = "trackers_required"
            else:
                errors, extra = await _async_check_trackers(
                    self.hass, self.config_entry.data[CONF_API_KEY], tracker_ids
                )
                placeholders.update(extra)
            if not errors:
                self._tracker_ids = tracker_ids
                self._options = {
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_ADDRESS_LOOKUP: user_input[CONF_ADDRESS_LOOKUP],
                    CONF_EXTENDED_DATA: user_input[CONF_EXTENDED_DATA],
                }
                return await self.async_step_names()

        schema = vol.Schema(
            {
                vol.Required(CONF_TRACKERS): MULTILINE,
                vol.Required(CONF_SCAN_INTERVAL): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(CONF_ADDRESS_LOOKUP): BooleanSelector(),
                vol.Required(CONF_EXTENDED_DATA): BooleanSelector(),
            }
        )
        suggested = {
            CONF_TRACKERS: "\n".join(options.get(CONF_TRACKERS, {})),
            CONF_SCAN_INTERVAL: options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            CONF_ADDRESS_LOOKUP: options.get(CONF_ADDRESS_LOOKUP, DEFAULT_ADDRESS_LOOKUP),
            CONF_EXTENDED_DATA: options.get(CONF_EXTENDED_DATA, DEFAULT_EXTENDED_DATA),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or suggested),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_names(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Name the trackers."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    **self._options,
                    CONF_TRACKERS: _names_from_input(self._tracker_ids, user_input),
                }
            )
        current = {
            int(tracker_id): name
            for tracker_id, name in self.config_entry.options.get(CONF_TRACKERS, {}).items()
        }
        return self.async_show_form(
            step_id="names", data_schema=_names_schema(self._tracker_ids, current)
        )
