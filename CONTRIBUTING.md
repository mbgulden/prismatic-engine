# Contributing to Prismatic Engine

Thanks for helping make Prismatic Engine better.

## Development setup

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[gateway]"
python scripts/public_launch_smoke.py
```

## Before opening a PR

Run focused checks for the area you changed and at least:

```bash
python scripts/public_launch_smoke.py
python scripts/plugin_architecture catalog
plugin-load-gate
```

For plugin work, also run the relevant focused tests:

```bash
python -m pytest tests/test_plugin_policy.py tests/test_plugin_artifacts.py tests/test_plugin_jobs.py -q
```

## Plugin rules

- Keep incomplete future plugins under `docs/plugin-blueprints/`, not `plugins/`.
- Do not commit credentials, local state, virtualenvs, or generated build artifacts.
- Manifests may document credential variable names, but never credential values.
- Risky actions need policy/approval gates.
- Artifacts should be registered through PE Core with provenance.

## PR expectations

Include:

- clear summary
- files changed
- verification commands and real output
- screenshots for dashboard UI changes when useful
- docs updates for public behavior changes

## Code style

If available, run:

```bash
python -m ruff check prismatic tests scripts
python -m ruff format prismatic tests scripts
```
