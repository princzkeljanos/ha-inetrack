# IneTrack developer API — integration notes & findings

This document records how the [IneTrack developer API](https://api.inetrack.com) behaves
in practice, as observed while building the Home Assistant integration in this repository.
It is meant both as reference for contributors and as feedback for the IneTrack / Inepex
team.

All observations below were made against **one account's own two trackers** on
**2026-11-13**, using a normally generated API key. Device IDs are shown as `100001` /
`100002` (placeholders); real IDs, keys, coordinates and addresses are not included.

> **A note on the one security-sensitive item (F1):** it is described here but is **not**
> filed as a public GitHub issue, and no cross-account testing was done. Please treat it as
> a private report. See [SECURITY.md](../SECURITY.md).

---

## 1. How the API works

### Endpoint and transport

```
GET https://api.inetrack.com/api?command=<Command>&<params...>&apikey=<key>
```

- One HTTP endpoint, `/api`; the operation is selected by `command`.
- Parameters are passed in the query string.
- **Arrays** are comma-joined: `deviceIds=100001,100002`.
- **Nested objects** are flattened with dots: `searchParameters.deviceId=100001`.
- Responses are JSON (`Content-Type: application/json;charset=utf-8`).

### Authentication

- An API key is passed as the `apikey` query parameter. It is generated in the web app
  under **Settings → Security**; generating a new key disables the old one.
- Commands that enforce auth answer an unknown key with **HTTP 403**
  `{"success": false, "message": "Forbidden"}`.

### Response envelope

Every response is an object with a common envelope plus command-specific fields:

```json
{ "success": true, "message": null, "errorId": null, "<payload>": [ ... ] }
```

- `success` (bool), `message` (string|null), `errorId` (int|null).
- On failure: `success:false` and a `message`; sometimes an `errorId` (e.g. `15`).

### Schema

A machine-readable schema is published and was very useful:

- Index: `https://api.inetrack.com/apischema` (JSON Schema draft-04).
- Per command: `https://api.inetrack.com/apischema/command?objectName=<Name>`
- Per response: `https://api.inetrack.com/apischema/response?objectName=<FQN>`

The browsable UI at `https://api.inetrack.com` (the GWT "CommandBrowser") still works. The
older prose docs (the 2013 blog post and the `support.inetrack.hu/IneTrackAPI` wiki) are
out of date.

---

## 2. Command status

| Command | With a valid key | Used by the integration |
| --- | --- | --- |
| `GetLastReports` | ✅ works | Yes — positions |
| `GetExtendedDataList` | ✅ works | Yes — voltage, odometer, I/O |
| `GetLastOneStatusData` | ✅ works | Yes — GSM signal |
| `GetLastOneDeviceEvents` / `GetLastDeviceEvents` | ✅ works | Yes — ignition, connectivity |
| `ListUserDevices` | ❌ HTTP 500 | No (see F2) |
| `ListUserDevicesInGroups` | ❌ HTTP 500 | No |
| `GetTrackersByIds` | ❌ HTTP 500 | No |
| `GetConnectionStates` | ❌ HTTP 500 (`errorId 15`) | No (see F3) |
| `ListStatusDataRules` | ❌ HTTP 500 | No |
| `GetStatusDataList` | ❌ HTTP 500 with a valid key; ⚠️ 200 **without** a key | No (see F1, F4) |

Working request shapes (key redacted):

```
# Last position, reverse-geocoded, for two trackers
/api?command=GetLastReports&deviceIds=100001,100002&positionType=REVERSE_GEOCODE&apikey=REDACTED

# Extended data (raw tracker values) in a time window
/api?command=GetExtendedDataList&deviceId=100001&minCreationTimestamp=<ms>&maxCreationTimestamp=<ms>&apikey=REDACTED

# Latest status data value per tracker
/api?command=GetLastOneStatusData&trackerIds=100001,100002&apikey=REDACTED

# Latest events (ACC_On/Off, connected/disconnected) per tracker
/api?command=GetLastOneDeviceEvents&trackerIds=100001,100002&names=ACC_On,ACC_Off,connected,disconnected&apikey=REDACTED
```

`positionType` accepts the enum `NONE | POSITION | POI | REVERSE_GEOCODE` and also the
legacy integer form (`positionType=1`).

---

## 3. Findings

Severity is from the point of view of a third-party client / data owner.

### F1 — `GetStatusDataList` returns data without a valid API key  ·  **Critical (please verify)**

**Observed:** calling `GetStatusDataList` for my **own** device ID returned HTTP 200 with
the device's status-data history (hundreds of rows) **with no `apikey` parameter at all**,
and also with an obviously invalid `apikey=invalid`:

```
# No apikey — still returned 200 with my device's data:
/api?command=GetStatusDataList&deviceId=100002&minCreationTimeStamp=<ms>&maxCreationTimeStamp=<ms>
→ 200 { "success": true, "statusDatas": [ ...~900 rows... ] }
```

By contrast, `GetLastReports`, `GetExtendedDataList` and `GetLastOneStatusData` all
correctly answer **HTTP 403 Forbidden** when the key is missing or wrong. So the gap looks
specific to `GetStatusDataList`.

**Why it matters:** if the same holds for device IDs that do **not** belong to the caller,
it would let anyone read a tracker's data by ID without authentication. **I did not test
any device ID other than my own**, so I cannot say whether it generalises — please verify
server-side.

**Suggested fix:** enforce the same auth/authorization check on `GetStatusDataList` as on
the other data commands (and confirm the caller owns the requested `deviceId`).

### F2 — No way to list an account's own trackers  ·  **High**

**Observed:** `ListUserDevices` (and `ListUserDevicesInGroups`, `GetTrackersByIds`) return
`HTTP 500 {"success":false,"message":"Internal server error"}` for every parameter
combination tried (paging, `descriptorName`, `searchParameters.*`, no params).

```
/api?command=ListUserDevices&firstResult=0&numMaxResult=100&apikey=REDACTED → 500
```

**Why it matters:** a client cannot discover which trackers the key can access, or their
names/models/subscription. The integration has to ask the user to paste tracker IDs
(copied from the app URL `...route?trackerId=100001`). Device names, model and subscription
expiry — all available in `deviceDescriptor` — are therefore unavailable to API clients.

**Suggested fix:** restore `ListUserDevices`. It would let clients auto-discover trackers
and show proper names and metadata.

### F3 — `GetConnectionStates` returns HTTP 500  ·  **Medium**

**Observed:** `GetConnectionStates` returns `HTTP 500`, `errorId: 15`, for valid-looking
input.

```
/api?command=GetConnectionStates&deviceIds=100001&apikey=REDACTED → 500 errorId 15
```

**Why it matters:** there is no direct "is the tracker currently connected" query. The
integration derives connectivity indirectly from `connected` / `disconnected` /
`trackerKicked` events instead, which is approximate.

### F4 — `GetStatusDataList` / `ListStatusDataRules` fail with a valid key  ·  **Medium**

**Observed:** with a **valid** key, `GetStatusDataList` and `ListStatusDataRules` return
`HTTP 500`. (`GetStatusDataList` without a key returns 200 — see F1.)

**Why it matters:** `ListStatusDataRules` is the source of **units, display names and
calibration** for status-data series. Without it, a client only gets raw numbers and must
guess units (e.g. that `externalVoltage` is millivolts). The integration hard-codes the
common Teltonika value names and units as a workaround.

### F5 — One unknown ID fails a whole batch  ·  **Medium**

**Observed:** `GetLastReports` with several `deviceIds`, where one ID is not visible to the
key, returns `HTTP 403 Forbidden` for the **entire** request rather than returning the
valid trackers plus a per-ID error.

**Why it matters:** a single stale/foreign ID blanks out all trackers. The integration
works around this by retrying one ID at a time to find the offending one.

**Suggested fix:** return per-tracker results, marking only the unauthorised IDs.

### F6 — Client errors are reported as HTTP 500  ·  **Medium**

**Observed:** malformed requests, unknown commands and (for some commands) auth problems
come back as `HTTP 500 {"message":"Internal server error"}` rather than a `4xx`.

```
/api?command=NoSuchCommand&apikey=invalid → 500 "Internal server error"
```

**Why it matters:** a client cannot reliably tell "my request/key is wrong" (client's
fault, don't retry) from "the server is having trouble" (retry later). The integration has
to treat `403` as auth and everything else as retryable, which is coarse.

**Suggested fix:** use `400` for bad input, `401/403` for auth, and reserve `500` for
genuine server faults.

### F7 — Mixed timestamp units in one object  ·  **Low**

**Observed:** within a single report, `creationTimestamp` is epoch **milliseconds** while
`storageTimestamp` is epoch **nanoseconds** (≈ `1.79e18`).

**Why it matters:** easy to misparse. The integration infers the unit from the magnitude.

**Suggested fix:** use the same unit (ms) for all timestamps, documented.

### F8 — `positionType` accepts enum and integer, undocumented  ·  **Low**

Both `positionType=REVERSE_GEOCODE` and `positionType=1` are accepted. The mapping is not
documented; the enum names should be the documented form.

### F9 — Duplicated / inconsistent response fields  ·  **Low**

**Observed:** `ReportWrapper` contains both `reportOrNull` and `report` with identical
content, and both `poiOrAddress` and `addressOrNull`. POI names appear under
`stringValues.name` in the "assisted" object form but as `name` elsewhere. Status-data and
event payloads repeat fields at both the wrapper and inner-object level (`value` and
`statusData.value`, `name` and `deviceEvent.name`).

**Why it matters:** more to parse and more ways to get it subtly wrong; larger payloads.

### F10 — Public documentation is outdated  ·  **Low**

The prose docs (2013 blog post, `support.inetrack.hu/IneTrackAPI` wiki) predate the current
command set. The `apischema` endpoint is current and excellent but is not linked from
human-readable docs. A short, current "getting started + command reference" page would make
the API much easier to adopt.

---

## 4. What the integration does about these

| Finding | Workaround in this integration |
| --- | --- |
| F2 (no device list) | User pastes tracker IDs / app URLs during setup |
| F4 (no status-data rules) | Hard-coded value names and units for common Teltonika fields; everything else exposed as a raw diagnostic sensor |
| F3 (no connection state) | Connectivity derived from `connected`/`disconnected`/`trackerKicked` events |
| F5 (batch fails on one ID) | Retry per tracker; mark the rejected ID and keep the rest |
| F6 (500 for client errors) | Treat `403` as auth (triggers re-auth), other errors as transient; tolerate a few failed polls before going unavailable |
| F7 (timestamp units) | Infer ms/µs/ns from magnitude |
| F1 (`GetStatusDataList` no-auth) | **Not used.** The integration reads status data via the auth-enforced `GetLastOneStatusData` instead |

## 5. Environment

- Observed: 2026-11-13, `https://api.inetrack.com`.
- Trackers: 2 × Teltonika (FMT100-class), one account.
- Client: the integration's `custom_components/inetrack/api.py` (standalone async client)
  and `scripts/probe_api.py`.

## 6. Contact

Integration maintainer: see the repository profile. IneTrack / Inepex: hello@inetrack.hu.
