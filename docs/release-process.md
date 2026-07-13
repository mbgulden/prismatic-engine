# Release process

This is the public release engineering path for Prismatic Engine. It keeps versioning, tags, changelog, package builds, smoke tests, and rollback expectations explicit.

## Versioning

Prismatic Engine uses SemVer-like versions while the project is alpha:

```text
MAJOR.MINOR.PATCH
```

Rules:

- bump `PATCH` for bug fixes, docs, and release engineering hardening
- bump `MINOR` for new public APIs, plugin lifecycle features, dashboard surfaces, or state schema additions
- bump `MAJOR` only for intentional breaking changes after the alpha phase
- keep `pyproject.toml` `[project].version` and `prismatic.__version__` identical
- add a matching `CHANGELOG.md` section before tagging

## Tagged releases

Release tags must use:

```text
vX.Y.Z
```

Example:

```bash
git tag -a v0.2.0 -m "Prismatic Engine v0.2.0"
git push origin v0.2.0
```

Tags trigger the publish workflow. Use GitHub workflow dispatch for a dry-run build/check before publishing.

## Pre-release checks

```bash
python scripts/release_check.py
python scripts/release_smoke.py
python scripts/public_launch_smoke.py
python scripts/public_security_readiness_audit.py
python -m build
python -m twine check dist/*
```

Expected markers:

```text
RELEASE_READINESS_OK
RELEASE_SMOKE_OK
PUBLIC_LAUNCH_SMOKE_OK
PUBLIC_SECURITY_READINESS_OK
```

## CI matrix

The test workflow runs the supported Python matrix:

```text
3.10
3.11
3.12
3.13
```

Each matrix leg installs `.[release]`, runs lint/format checks, focused public smoke checks, release readiness checks, and tests.

## PyPI publish flow

The publish workflow:

1. runs on `v*` tags or manual workflow dispatch
2. verifies the tag matches `pyproject.toml`
3. runs release readiness and release smoke
4. builds wheel and sdist
5. runs `twine check`
6. uploads build artifacts
7. creates build provenance attestation
8. publishes to PyPI only for tag pushes, unless TestPyPI/manual publishing is configured separately

## Smoke test

`python scripts/release_smoke.py` is the release-level smoke. It composes:

- package/runtime metadata probe
- stable CLI import/run probe
- shipped plugin load gate
- public launch smoke
- public security readiness audit

It is credential-free and safe for local CI.

## Docker and devcontainer

The Dockerfile and devcontainer are for local public-user development and smoke testing. They are not a production deployment recipe. Remote production still requires explicit auth, TLS, state storage, and secret management.

## Release checklist

Use [`docs/release-checklist.md`](release-checklist.md) for the exact step-by-step checklist.

## Rollback

If a release is bad:

1. stop promotion/publish pipeline if still running
2. yank the PyPI release only if the package is actively harmful
3. create a patch release with a clear changelog note
4. document migration/rollback notes in `docs/migrations.md`
