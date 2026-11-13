# Security policy

## Reporting a vulnerability

If you find a security problem in **this integration**, please report it privately:

- Use GitHub's **Report a vulnerability** button
  (repository → **Security** → **Advisories** → **Report a vulnerability**), or
- email the maintainer listed in the repository profile.

Please do not open a public issue for a security problem until it has been fixed.

## Scope

This repository is a Home Assistant client for the IneTrack developer API. It:

- stores your IneTrack API key in Home Assistant's config entry storage (the same place
  every integration keeps its credentials);
- sends the key only to `https://api.inetrack.com` over HTTPS;
- never writes the key, exact coordinates or addresses into logs or diagnostics
  (diagnostics are redacted — see `diagnostics.py`).

### Issues in the IneTrack API itself

Problems in the **IneTrack service / API** (not this code) are tracked openly in
[docs/INETRACK_API.md](docs/INETRACK_API.md) and in the issue tracker, because they
affect how the integration has to behave. If you believe an API problem exposes other
users' data, treat it as sensitive: report it privately as above **and** to IneTrack
(hello@inetrack.hu) rather than posting details publicly.

## Handling of your data

- The integration only reads data for the tracker IDs you configure.
- It does not send your data anywhere except to the IneTrack API.
- `scripts/probe_api.py` output and the saved key file are git-ignored and must never be
  committed; CI enforces this with a secret scan.
