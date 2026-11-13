# Contributing to ha-inetrack

Thanks for helping improve the IneTrack integration for Home Assistant! This is a
community project and not affiliated with Inepex Zrt. / IneTrack.

## Ways to help

- **Report a bug or request a feature** — use the issue templates.
- **Report an IneTrack API problem** — see [docs/INETRACK_API.md](docs/INETRACK_API.md)
  for the known API issues; new ones are very welcome (attach redacted diagnostics).
- **Improve the code** — open a pull request (see below).

## Development setup

You need **Python 3.14** (older versions pull in an old Home Assistant and some tests
behave differently). On Windows, run the tests under Python 3.14 as noted below.

```bash
python -m venv .venv
# Windows:        .venv\Scripts\activate
# Linux / macOS:  source .venv/bin/activate
pip install -r requirements_test.txt aiohttp
```

Run the checks the same way CI does:

```bash
ruff check .
ruff format --check .
pytest -q
```

### Looking at the real API (optional)

`scripts/probe_api.py` calls the API for your own account and writes a **redacted**
`probe-output.json` (git-ignored):

```bash
python scripts/probe_api.py <tracker-id-or-app-url> ...
```

It can save your key to `.inetrack_key` (git-ignored) so it does not ask every run.
**Never commit `.inetrack_key` or `probe-output.json`** — both are git-ignored, and CI
runs a secret scan that fails the build if a secret file or key is ever committed.

## Pull requests

1. Branch from `main` (see [docs/BRANCHING.md](docs/BRANCHING.md) for the branch policy).
2. Keep the change focused; add or update tests in `tests/`.
3. Make sure `ruff check .`, `ruff format .` and `pytest` all pass.
4. Bump `"version"` in `custom_components/inetrack/manifest.json` only if asked — releases
   are cut from `main`.
5. Open the PR against `main`. CI (hassfest, HACS, lint, tests, secret scan) must be green.

## Code style

- `custom_components/inetrack/api.py` stays free of any Home Assistant imports, so it can
  be tested on its own and reused.
- Follow the patterns already in the codebase: a `DataUpdateCoordinator`, entity
  descriptions, and `has_entity_name` entities.
- User-facing strings go through `translations/` (English and Hungarian are both kept up
  to date).

## Reporting security issues

Please do **not** open a public issue for a security problem. See
[SECURITY.md](SECURITY.md).
