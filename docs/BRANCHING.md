# Branching and release policy

## Until the first release (v0.1.0)

Work goes straight to `main`. This keeps the initial setup fast while there is a single
author.

## From v0.1.0 onward (once others join)

Switch to a light pull-request flow and protect `main`.

### Branch naming

| Prefix | For | Example |
| --- | --- | --- |
| `feat/` | new feature | `feat/fuel-level-sensor` |
| `fix/` | bug fix | `fix/moving-after-parked` |
| `docs/` | docs only | `docs/api-notes` |
| `chore/` | tooling, CI, deps | `chore/bump-actions` |

### Flow

1. Branch from `main`, make the change with tests.
2. Open a PR into `main`. All CI checks (hassfest, HACS, lint, tests, secret scan) must
   pass, and it needs one review approval.
3. Squash-merge into `main`.

### Protect `main` (GitHub → Settings → Branches → Add rule)

Recommended settings for `main`:

- **Require a pull request before merging** (1 approval).
- **Require status checks to pass**: `Hassfest (Home Assistant)`, `HACS`,
  `Lint and tests`, `Secret scan`.
- **Require branches to be up to date before merging.**
- **Require conversation resolution before merging.**
- **Do not allow bypassing the above** (optional, once the team is set).

### Releases

- Releases are cut from `main`.
- Bump `"version"` in `custom_components/inetrack/manifest.json` (semver).
- Tag `vMAJOR.MINOR.PATCH` (e.g. `v0.2.0`) and publish a GitHub release; HACS/Marketplace
  shows the newest releases as installable versions.

### Versioning

[Semantic Versioning](https://semver.org): patch = fixes, minor = new sensors/options
(backwards compatible), major = breaking changes to entities or configuration.
