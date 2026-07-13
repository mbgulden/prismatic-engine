# Upgrade guide

This guide is the stable public upgrade path for Prismatic Engine.

## Upgrade path

For a normal local checkout:

```bash
git fetch origin
git checkout main
git pull --ff-only
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[release]"
python scripts/release_smoke.py
```

For an installed package:

```bash
python -m pip install --upgrade prismatic-engine[gateway]
prismatic --help
python scripts/release_smoke.py
```

If you are not running from a source checkout, copy `scripts/release_smoke.py` from the release tag or run the installed package CLI smoke commands documented in `docs/stable-cli-entrypoints.md`.

## Back up state

Before upgrading a long-running local instance, back up state:

```bash
cp -R "${PRISMATIC_STATE_DIR:-./prismatic_state}" "${PRISMATIC_STATE_DIR:-./prismatic_state}.backup.$(date +%Y%m%d%H%M%S)"
```

Important files include:

```text
plugin_jobs.json
plugin_artifacts.json
event_log.sqlite
curator.sqlite
```

## Bootstrap a clean environment

From a fresh checkout:

```bash
bash scripts/bootstrap_env.sh
```

The bootstrap creates `.venv`, copies `.env.example` to `.env` if needed, copies `config/prismatic.sample.yaml` to `config.local.yaml` if needed, installs `.[release]`, and runs release smoke checks.

## Run release smoke

```bash
python scripts/release_check.py
python scripts/release_smoke.py
```

Expected markers:

```text
RELEASE_READINESS_OK
RELEASE_SMOKE_OK
```

## Check migration notes

Before upgrading across minor versions, read:

```text
docs/migrations.md
CHANGELOG.md
```

## Rollback

If an upgrade fails:

1. stop the Gateway/dispatcher process
2. restore the backed-up state directory
3. reinstall the previous package or check out the previous tag
4. run `python scripts/release_smoke.py`
5. document the failure before retrying

## Compatibility expectations

- Supported Python versions are 3.10, 3.11, 3.12, and 3.13.
- Public quickstart and release smoke are credential-free.
- Remote production deployments still require auth/TLS/network controls outside the local quickstart.
