# Changelog

All notable public-facing changes will be tracked here.

The project is currently alpha. Dates use UTC.

## Unreleased

### Release engineering

- Public release engineering layer: release process docs, release checklist, migration notes, upgrade guide, stable CLI entrypoint reference, sample config, bootstrap script, Dockerfile, devcontainer, release smoke, and release readiness check.

### Changed

- CI now runs a supported Python matrix and release smoke/readiness checks.
- Publish workflow now verifies tags, checks distributions, uploads artifacts, and creates build provenance before PyPI publication.

## [0.2.0] - 2026-07-13

### Added

- Public README quickstart and first-user path.
- `.env.example` with safe local defaults.
- Public onboarding guide.
- Public architecture overview.
- Plugin developer guide.
- Hello plugin tutorial.
- Troubleshooting guide.
- Dashboard screenshot documentation and illustrative dashboard asset.
- Contribution guide.
- Security policy.
- One-command public launch smoke test at `scripts/public_launch_smoke.py`.
- Public security readiness audit at `scripts/public_security_readiness_audit.py` and `docs/public-security-readiness.md`.

### Current plugin foundation

- Durable plugin jobs and audit events.
- Universal artifact/provenance registry.
- Generic plugin policy decisions and enforcement.
- Dashboard/API visibility for plugin governance, jobs, artifacts, and policy decisions.

### Migration notes

- See `docs/migrations.md` for the `0.1.x to 0.2.0` migration path.
