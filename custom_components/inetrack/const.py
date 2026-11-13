"""Constants for the IneTrack integration."""

from __future__ import annotations

from datetime import timedelta
import re
from typing import Final

DOMAIN: Final = "inetrack"
MANUFACTURER: Final = "IneTrack"
WEB_APP_URL: Final = "https://app.inetrack.com/"

# Options
CONF_TRACKERS: Final = "trackers"  # {"100001": "Mazda6", ...}
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_ADDRESS_LOOKUP: Final = "address_lookup"
CONF_EXTENDED_DATA: Final = "extended_data"

DEFAULT_SCAN_INTERVAL: Final = 60  # seconds
MIN_SCAN_INTERVAL: Final = 30
MAX_SCAN_INTERVAL: Final = 3600
DEFAULT_ADDRESS_LOOKUP: Final = True
DEFAULT_EXTENDED_DATA: Final = True

# Extended data is read from shortly before the last known report onwards.
EXTENDED_DATA_LOOKBACK: Final = timedelta(minutes=10)
# Re-read this much before the previous read, for data stored late.
EXTENDED_DATA_OVERLAP: Final = timedelta(minutes=5)

# Consecutive failed polls before entities go unavailable / optional commands pause.
TOLERATED_FAILURES: Final = 3
COMMAND_PAUSE: Final = timedelta(hours=1)

# Events used for the ignition and connectivity binary sensors.
EVENT_IGNITION_ON: Final = "ACC_On"
EVENT_IGNITION_OFF: Final = "ACC_Off"
EVENT_CONNECTED: Final = "connected"
EVENTS_DISCONNECTED: Final = ("disconnected", "trackerKicked")
STATE_EVENTS: Final = (
    EVENT_IGNITION_ON,
    EVENT_IGNITION_OFF,
    EVENT_CONNECTED,
    *EVENTS_DISCONNECTED,
)

# Above this speed (km/h) in a recent report the vehicle counts as moving.
MOVING_SPEED_THRESHOLD: Final = 3.0
# A report older than this does not count for the "moving" sensor.
MOVING_MAX_REPORT_AGE: Final = timedelta(minutes=15)

# Extended data values that identify the SIM card or driver key, or are noise.
PRIVATE_DATA_NAMES: Final = re.compile(r"iccid|imsi|imei|ibutton|iokeyvalue", re.IGNORECASE)
