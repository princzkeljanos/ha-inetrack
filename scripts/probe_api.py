"""Show what the IneTrack API returns for your account.

Developer tool: calls the API commands the integration uses (plus a few
alternatives) and writes the responses to ``probe-output.json`` with personal
data redacted (API key, e-mail, phone, SIM, exact coordinates, street address).

Usage (needs ``pip install aiohttp``)::

    python scripts/probe_api.py [TRACKER_ID_OR_APP_URL ...]

The API key is read from the ``INETRACK_API_KEY`` environment variable, from a
git-ignored ``.inetrack_key`` file, or asked for with hidden input. Tracker IDs
are needed because the API's device list command answers HTTP 500; you can pass
IDs or app.inetrack.com URLs that contain ``trackerId=...``.

Do not commit ``probe-output.json`` or ``.inetrack_key``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import getpass
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "probe-output.json"
KEY_FILE = ROOT / ".inetrack_key"

_spec = importlib.util.spec_from_file_location(
    "inetrack_api", ROOT / "custom_components" / "inetrack" / "api.py"
)
api = importlib.util.module_from_spec(_spec)
sys.modules["inetrack_api"] = api
_spec.loader.exec_module(api)

SECRET_KEYS = {
    "apikey",
    "apiKey",
    "email",
    "phoneNumber",
    "vodaStylePhoneNumber",
    "icc",
    "ip",
    "lastUsedIp",
    "staySignedInUUID",
    "vatId",
    "consignee",
    "postalCode",
    "publicLastReportUrl",
    "forwardingAddress",
}
ADDRESS_KEYS = {
    "displayAddress",
    "road",
    "houseNum",
    "postcode",
    "suburb",
    "address",
    "stateDistrict",
}
COORD_KEYS = {"lat", "lng", "startLat", "startLng", "stopLat", "stopLng"}
PARTIAL_KEYS = {"nativeID", "forwardingNativeId"}
# Extended data series whose values identify the SIM / device.
SECRET_SERIES = re.compile(r"iccid|imsi|imei|ibutton", re.IGNORECASE)
EVENT_NAMES = ["connected", "disconnected", "trackerKicked", "ACC_On", "ACC_Off"]


def redact(value: Any, key: str | None = None) -> Any:
    """Recursively redact personal data but keep the structure visible."""
    if isinstance(value, dict):
        if key == "owner":
            return {"id": value.get("id"), "<keys>": sorted(value)}
        if SECRET_SERIES.search(str(value.get("dataName") or value.get("name") or "")):
            return {k: ("**REDACTED**" if "alue" in k else redact(v, k)) for k, v in value.items()}
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, key) for v in value]
    if value is None or key is None:
        return value
    if key in SECRET_KEYS:
        return "**REDACTED**"
    if key in ADDRESS_KEYS and isinstance(value, str):
        return f"**REDACTED ({len(value)} chars)**"
    if key in COORD_KEYS and isinstance(value, (int, float)):
        return round(value, 1)
    if key in PARTIAL_KEYS and isinstance(value, str):
        return f"***{value[-3:]}"
    return value


def stamp(row: dict[str, Any]) -> float:
    return float(row.get("creationTimestamp") or 0)


def summarize(rows: list[dict[str, Any]], name_key: str) -> dict[str, Any]:
    """Group a time series by name and keep the newest few points of each."""
    series: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        series.setdefault(str(row.get(name_key)), []).append(row)
    return {
        "total_rows": len(rows),
        "row_keys": sorted({k for row in rows for k in row}),
        "series": {
            name: {
                "count": len(items),
                "newest": redact(sorted(items, key=stamp)[-3:]),
                "newest_time": str(api.ms_to_datetime(max(stamp(i) for i in items))),
            }
            for name, items in sorted(series.items())
        },
    }


async def call(client: Any, label: str, command: str, **params: Any) -> dict[str, Any]:
    """Run one command and capture either the response or the error."""
    try:
        data = await client.request(command, **params)
    except api.InetrackError as err:
        print(f"  {label:<40} FAILED  {type(err).__name__}: {err}")
        return {
            "command": command,
            "params": redact(params),
            "ok": False,
            "error": type(err).__name__,
            "detail": str(err),
        }
    print(f"  {label:<40} ok")
    return {"command": command, "params": redact(params), "ok": True, "response": data}


def clean_key(raw: str) -> str:
    """Drop bracketed-paste markers and other invisible characters a paste may add."""
    raw = raw.replace("\x1b[200~", "").replace("\x1b[201~", "")
    return "".join(ch for ch in raw if ch.isprintable()).strip().strip("\"'")


def get_key() -> str:
    """Read the key from the environment, the key file, or a hidden prompt."""
    if os.environ.get("INETRACK_API_KEY"):
        return clean_key(os.environ["INETRACK_API_KEY"])
    if KEY_FILE.exists():
        print(f"Using the key saved in {KEY_FILE.name}")
        return clean_key(KEY_FILE.read_text(encoding="utf-8"))
    raw = getpass.getpass("IneTrack API key (input hidden): ")
    key = clean_key(raw)
    removed = len(raw) - len(key)
    print(
        f"Key received: {len(key)} characters"
        + (f" ({removed} invisible removed)" if removed else "")
    )
    if (
        key
        and input(f"Save it in {KEY_FILE.name} (git-ignored) for later runs? [y/N]: ").lower()
        == "y"
    ):
        KEY_FILE.write_text(key, encoding="utf-8")
        print(f"Saved. Delete {KEY_FILE.name} (or generate a new key in IneTrack) when done.")
    return key


def parse_ids(values: list[str]) -> list[int]:
    """Accept tracker IDs or app URLs containing trackerId=..."""
    ids: list[int] = []
    for value in values:
        found = re.findall(r"trackerId=(\d+)", value) or re.findall(r"\d+", value)
        ids.extend(int(item) for item in found if int(item) not in ids)
    return ids


async def main() -> None:
    key = get_key()
    if not key:
        sys.exit("No API key given.")
    ids = parse_ids(sys.argv[1:] or [os.environ.get("INETRACK_TRACKER_IDS", "")])

    out: dict[str, Any] = {"generated": datetime.now(UTC).isoformat(), "calls": {}}
    calls = out["calls"]
    now = datetime.now(UTC)
    now_ms = int(now.timestamp() * 1000)
    day_ms = int((now - timedelta(days=1)).timestamp() * 1000)
    recent_ms = int((now - timedelta(minutes=15)).timestamp() * 1000)

    async with aiohttp.ClientSession() as session:
        client = api.InetrackApiClient(session, key)

        print("Tracker list variants:")
        for label, params in (
            ("ListUserDevices paging", {"firstResult": 0, "numMaxResult": 100}),
            ("ListUserDevices descriptor", {"descriptorName": "ListUserDevicesDescriptor"}),
            ("ListUserDevices groupTrackers", {"searchParameters": {"groupTrackers": False}}),
            ("ListUserDevices insertShared", {"searchParameters": {"insertShared": True}}),
        ):
            calls[label] = await call(client, label, "ListUserDevices", **params)
            if calls[label]["ok"] and not ids:
                ids = [int(d["id"]) for d in calls[label]["response"].get("list") or []]
        if not ids:
            typed = input("Tracker IDs or app URLs (comma separated): ")  # noqa: ASYNC250
            ids = parse_ids(typed.split(","))
        print(f"  -> trackers: {ids}")
        if not ids:
            sys.exit("No tracker IDs, nothing more to probe.")
        calls["GetTrackersByIds"] = await call(
            client,
            "GetTrackersByIds",
            "GetTrackersByIds",
            searchParameters={"trackerIds": ",".join(map(str, ids))},
        )

        print("All trackers at once:")
        calls["GetLastReports"] = await call(
            client,
            "GetLastReports",
            "GetLastReports",
            deviceIds=ids,
            positionType="REVERSE_GEOCODE",
        )
        calls["GetLastOneStatusData"] = await call(
            client, "GetLastOneStatusData", "GetLastOneStatusData", trackerIds=ids
        )
        calls["GetLastOneDeviceEvents"] = await call(
            client, "GetLastOneDeviceEvents", "GetLastOneDeviceEvents", trackerIds=ids
        )
        calls["GetLastOneDeviceEvents names"] = await call(
            client,
            "GetLastOneDeviceEvents names",
            "GetLastOneDeviceEvents",
            trackerIds=ids,
            names=EVENT_NAMES,
        )
        calls["GetLastDeviceEvents limit 10"] = await call(
            client, "GetLastDeviceEvents limit 10", "GetLastDeviceEvents", trackerIds=ids, limit=10
        )
        calls["GetLastDeviceEvents names"] = await call(
            client,
            "GetLastDeviceEvents names",
            "GetLastDeviceEvents",
            trackerIds=ids,
            names=EVENT_NAMES,
            limit=10,
        )

        for tracker_id in ids:
            print(f"Tracker {tracker_id}:")
            per: dict[str, Any] = {}
            for label, list_key, name_key, command, params in (
                (
                    "ext 15 min",
                    "extDataList",
                    "dataName",
                    "GetExtendedDataList",
                    {"minCreationTimestamp": recent_ms, "maxCreationTimestamp": now_ms},
                ),
                (
                    "ext 24 h names filter",
                    "extDataList",
                    "dataName",
                    "GetExtendedDataList",
                    {
                        "minCreationTimestamp": day_ms,
                        "maxCreationTimestamp": now_ms,
                        "names": ["externalVoltage", "powerSupplyVoltage"],
                    },
                ),
                (
                    "events 24 h",
                    "deviceEvents",
                    "name",
                    "GetDeviceEventList",
                    {"minCreationTimeStamp": day_ms, "maxCreationTimeStamp": now_ms},
                ),
            ):
                result = await call(client, label, command, deviceId=tracker_id, **params)
                if result["ok"]:
                    rows = result["response"].get(list_key) or []
                    if command == "GetDeviceEventList":
                        rows = [r.get("deviceEvent") or r for r in rows]
                    result["response"] = {
                        k: v for k, v in result["response"].items() if k != list_key
                    } | {"summary": summarize(rows, name_key)}
                per[label] = result
            calls[f"tracker_{tracker_id}"] = per

    OUTPUT.write_text(json.dumps(redact(out), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {OUTPUT}")


if __name__ == "__main__":
    asyncio.run(main())
